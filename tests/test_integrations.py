import ctypes
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from acc_engineer.controller import ButtonEdge, ControllerService
from acc_engineer import telemetry
from acc_engineer.telemetry import AccSharedMemory, BroadcastingClient, Graphics, Physics, StaticPrefix, parse_broadcast_packet, registration_packet


def test_shared_memory_offsets_are_windows_sdk_offsets():
    assert Graphics.completedLaps.offset == 132
    assert Graphics.sessionTimeLeft.offset == 152
    assert Graphics.flag.offset == 1224
    assert Physics.fuel.offset == 12
    assert Physics.wheelsPressure.offset == 88
    assert Physics.brakeTemp.offset == 348
    assert ctypes.sizeof(Graphics) == 1588


def test_shared_memory_maps_confirmed_session_and_flag_fields(monkeypatch):
    class FakeMapping:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return self.payload

        def close(self):
            pass

    p, g = Physics(), Graphics()
    p.gear, p.speedKmh, p.rpms = 2, 124.5, 5100
    g.session, g.sessionIndex, g.status = 2, 3, 2
    g.sessionTimeLeft, g.completedLaps, g.isValidLap = 900000.0, 4, 0
    g.iLastTime = g.iBestTime = 2147483647
    g.isSetupMenuVisible = 1
    g.isInPit, g.isInPitLane = 1, 1
    g.GlobalYellow, g.GlobalYellow2, g.GlobalRed = 1, 1, 0
    reader = AccSharedMemory()
    reader._maps = [FakeMapping(bytes(p)), FakeMapping(bytes(g))]
    monkeypatch.setattr(telemetry, "os", SimpleNamespace(name="nt"))

    sample = reader.read()

    assert sample["session"] == "race"
    assert sample["is_setup_menu_visible"] is True
    assert sample["session_time_left_s"] == 900.0
    assert sample["last_lap_ms"] is None and sample["best_lap_ms"] is None
    assert sample["lap_valid"] is False
    assert sample["invalid_lap"] is True
    assert sample["in_pit"] and sample["in_pit_lane"]
    assert sample["global_flags"] == {
        "yellow": True, "yellow_1": False, "yellow_2": True, "yellow_3": False,
        "white": False, "green": False, "chequered": False, "red": False,
    }


def test_shared_memory_session_id_changes_on_session_reset(monkeypatch):
    class FakeMapping:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return self.payload

        def close(self):
            pass

    p, g = Physics(), Graphics()
    p.packetId = 1
    g.packetId, g.session, g.sessionIndex, g.completedLaps = 1, 2, 0, 0
    reader = AccSharedMemory()
    physics, graphics = FakeMapping(bytes(p)), FakeMapping(bytes(g))
    reader._maps = [physics, graphics]
    monkeypatch.setattr(telemetry, "os", SimpleNamespace(name="nt"))
    first = reader.read()["session_id"]

    g.sessionIndex = 1
    graphics.payload = bytes(g)
    second = reader.read()["session_id"]

    assert first != second


def test_static_identity_is_optional_and_normalized(monkeypatch):
    class FakeMapping:
        def __init__(self, name, size):
            if name.endswith("acpmf_static"):
                raise OSError(2, "optional static mapping absent")
            self.payload = bytes(size)

        def read(self):
            return self.payload

        def close(self):
            pass

    monkeypatch.setattr(telemetry, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(telemetry, "_WindowsMapping", FakeMapping)
    reader = AccSharedMemory()

    sample = reader.read()

    assert sample is not None
    assert sample["car_model"] is None
    assert sample["track"] is None
    assert reader.error is None


def test_static_prefix_reads_only_documented_car_and_track(monkeypatch):
    class FakeMapping:
        def __init__(self, name, size):
            if name.endswith("acpmf_static"):
                metadata = StaticPrefix()
                for field, value in (("carModel", "McLaren_720s_GT3"), ("track", "Barcelona")):
                    target = getattr(metadata, field)
                    for index, character in enumerate(value):
                        target[index] = ord(character)
                self.payload = bytes(metadata)
            else:
                self.payload = bytes(size)

        def read(self):
            return self.payload

        def close(self):
            pass

    monkeypatch.setattr(telemetry, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(telemetry, "_WindowsMapping", FakeMapping)
    reader = AccSharedMemory()

    sample = reader.read()

    assert ctypes.sizeof(StaticPrefix) == 200
    assert sample["car_model"] == "mclaren_720s_gt3"
    assert sample["track"] == "barcelona"


def test_all_zero_physics_page_is_not_exposed_as_a_real_measurement(monkeypatch):
    class FakeMapping:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return self.payload

        def close(self):
            pass

    p, g = Physics(), Graphics()
    p.packetId, g.packetId = 10, 20
    g.status, g.sessionTimeLeft = 2, 600000
    reader = AccSharedMemory()
    reader._maps = [FakeMapping(bytes(p)), FakeMapping(bytes(g))]
    monkeypatch.setattr(telemetry, "os", SimpleNamespace(name="nt"))

    sample = reader.read()

    assert sample["status"] == "live"
    assert sample["physics_valid"] is False
    assert sample["stale"] is False
    assert sample["physics_age_s"] is not None
    assert sample["speed_kmh"] is None
    assert sample["rpm"] is None
    assert sample["fuel_l"] is None
    assert sample["tyre_pressure_psi"] is None


def test_zero_fuel_is_preserved_when_other_physics_signals_are_valid(monkeypatch):
    class FakeMapping:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return self.payload

        def close(self):
            pass

    p, g = Physics(), Graphics()
    p.packetId, p.speedKmh, p.rpms, p.fuel = 11, 5.0, 900, 0.0
    for wheel in range(4):
        p.wheelsPressure[wheel] = 27.5
    g.packetId, g.status = 21, 2
    reader = AccSharedMemory()
    reader._maps = [FakeMapping(bytes(p)), FakeMapping(bytes(g))]
    monkeypatch.setattr(telemetry, "os", SimpleNamespace(name="nt"))

    sample = reader.read()

    assert sample["physics_valid"] is True
    assert sample["fuel_l"] == 0.0
    assert sample["speed_kmh"] == 5.0


def test_frozen_physics_page_ages_even_while_graphics_updates(monkeypatch):
    class FakeMapping:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return self.payload

        def close(self):
            pass

    p, g = Physics(), Graphics()
    p.packetId, p.speedKmh, p.rpms, p.fuel = 12, 80.0, 4000, 20.0
    g.packetId, g.status, g.sessionTimeLeft = 22, 2, 600000
    physics, graphics = FakeMapping(bytes(p)), FakeMapping(bytes(g))
    reader = AccSharedMemory(stale_after_s=.02)
    reader._maps = [physics, graphics]
    monkeypatch.setattr(telemetry, "os", SimpleNamespace(name="nt"))
    first = reader.read()
    assert first["physics_valid"] is True
    assert first["stale"] is False

    time.sleep(.03)
    g.packetId += 1
    g.sessionTimeLeft -= 1000
    graphics.payload = bytes(g)
    second = reader.read()

    assert second["status"] == "live"
    assert second["graphics_age_s"] < .02
    assert second["physics_age_s"] > .02
    assert second["stale"] is True
    assert second["physics_valid"] is False
    assert second["fuel_l"] is None
    assert second["speed_kmh"] is None


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
