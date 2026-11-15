import ctypes
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from acc_engineer.controller import ButtonEdge, ControllerService
from acc_engineer import telemetry
from acc_engineer.telemetry import AccSharedMemory, BroadcastingClient, Graphics, Physics, parse_broadcast_packet, registration_packet


def test_shared_memory_offsets_are_windows_sdk_offsets():
    assert Graphics.completedLaps.offset == 132
    assert Graphics.sessionTimeLeft.offset == 152
    assert Graphics.flag.offset == 1224
    assert Physics.fuel.offset == 12
    assert Physics.wheelsPressure.offset == 88
    assert Physics.brakeTemp.offset == 348
    assert ctypes.sizeof(Graphics) == 1588


def test_button_hold_bounce_release_and_repress():
    button = ButtonEdge()
    assert button.update(True,0)
    assert not button.update(True,1)
    assert not button.update(False,1.01)
    assert button.update(True,1.02)
    assert not button.update(False,1.03)
    assert not button.update(True,1.04)
    assert not button.update(True,2)


def test_registration_utf8_length_and_protocol():
    packet = registration_packet('páss')
    assert packet[:2] == bytes([1,4])
    assert 'páss'.encode() in packet


def test_truncated_packet_rejected_and_does_not_mark_connected():
    client = BroadcastingClient()
    client.datagram_received(b'\x01\x00',None)
    assert client.malformed_packets == 1
    assert client.snapshot()['status'] == 'disconnected'
    with pytest.raises((ValueError,struct.error)):
        parse_broadcast_packet(b'\x07\x01\xff\xff')


def test_registration_rejected_does_not_send_requests():
    client = BroadcastingClient()
    client.datagram_received(struct.pack('<BiBBH',1,12,0,1,0),None)
    assert client.connection_id is None
    assert client.snapshot()['status'] == 'registration_rejected'


def test_car_packet_parses_protocol_v4_driver_count_and_laps():
    lap = struct.pack('<iHHBBBBB',90234,4,1,0,0,1,0,0)
    packet = bytes([3])+struct.pack('<HHBBfffBHHHHfHi',4,1,2,4,1.,2.,3.,1,150,3,2,4,.5,8,-123)+lap*3
    parsed = parse_broadcast_packet(packet)
    assert parsed['car_id'] == 4
    assert parsed['position'] == 3
    assert parsed['laps'] == 8
    assert parsed['last_lap']['time_ms'] == 90234


def test_invalid_binding_and_nonlocal_broadcast_rejected():
    with pytest.raises(ValueError):
        ControllerService(lambda:None,button='nope')
    with pytest.raises(ValueError):
        BroadcastingClient(host='8.8.8.8')


def test_shared_memory_close_waits_for_inflight_read(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    class FakeMapping:
        def __init__(self, payload, blocks=False):
            self.payload = payload
            self.blocks = blocks
            self.closed = False

        def read(self):
            if self.blocks:
                entered.set()
                assert release.wait(timeout=2)
            return self.payload

        def close(self):
            self.closed = True

    # Use synthetic, zeroed SDK-sized pages. No Windows mapping is opened.
    physics, graphics = FakeMapping(bytes(ctypes.sizeof(Physics)), True), FakeMapping(bytes(ctypes.sizeof(Graphics)))
    reader = AccSharedMemory()
    reader._maps = [physics, graphics]
    monkeypatch.setattr(telemetry, "os", SimpleNamespace(name="nt"))
    read_thread = threading.Thread(target=reader.read)
    close_thread = threading.Thread(target=reader.close)
    read_thread.start()
    assert entered.wait(timeout=1)
    close_thread.start()
    time.sleep(.03)
    assert physics.closed is False
    release.set()
    read_thread.join(timeout=1)
    close_thread.join(timeout=1)
    assert not read_thread.is_alive()
    assert not close_thread.is_alive()
    assert physics.closed and graphics.closed
    assert reader._maps == []
