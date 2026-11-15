"""Read-only ACC telemetry and protocol-v4 broadcasting adapters.

Layouts follow Kunos' ACC shared-memory v1.8.12 documentation. No mapping is
created when ACC is absent: OpenFileMappingW is intentionally used over mmap.
"""
from __future__ import annotations

import asyncio
import ctypes as c
import math
import os
import struct
import time
import threading
from copy import deepcopy

I, F, W = c.c_int32, c.c_float, c.c_uint16


class Physics(c.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [
        ('packetId', I), ('gas', F), ('brake', F), ('fuel', F), ('gear', I),
        ('rpms', I), ('steerAngle', F), ('speedKmh', F), ('velocity', F*3),
        ('accG', F*3), ('wheelSlip', F*4), ('wheelLoad', F*4),
        ('wheelsPressure', F*4), ('wheelAngularSpeed', F*4), ('tyreWear', F*4),
        ('tyreDirtyLevel', F*4), ('tyreCoreTemperature', F*4), ('camberRAD', F*4),
        ('suspensionTravel', F*4), ('drs', F), ('tc', F), ('heading', F),
        ('pitch', F), ('roll', F), ('cgHeight', F), ('carDamage', F*5),
        ('numberOfTyresOut', I), ('pitLimiterOn', I), ('abs', F),
        ('kersCharge', F), ('kersInput', F), ('autoshifterOn', I),
        ('rideHeight', F*2), ('turboBoost', F), ('ballast', F), ('airDensity', F),
        ('airTemp', F), ('roadTemp', F), ('localAngularVel', F*3), ('finalFF', F),
        ('performanceMeter', F), ('engineBrake', I), ('ersRecoveryLevel', I),
        ('ersPowerLevel', I), ('ersHeatCharging', I), ('ersIsCharging', I),
        ('kersCurrentKJ', F), ('drsAvailable', I), ('drsEnabled', I),
        ('brakeTemp', F*4),
    ]


class Graphics(c.LittleEndianStructure):
    _pack_ = 4
    _fields_ = [
        ('packetId', I), ('status', I), ('session', I), ('currentTime', W*15),
        ('lastTime', W*15), ('bestTime', W*15), ('split', W*15),
        ('completedLaps', I), ('position', I), ('iCurrentTime', I),
        ('iLastTime', I), ('iBestTime', I), ('sessionTimeLeft', F),
        ('distanceTraveled', F), ('isInPit', I), ('currentSectorIndex', I),
        ('lastSectorTime', I), ('numberOfLaps', I), ('tyreCompound', W*33),
        ('replayTimeMultiplier', F), ('normalizedCarPosition', F),
        ('activeCars', I), ('carCoordinates', (F*3)*60), ('carID', I*60),
        ('playerCarID', I), ('penaltyTime', F), ('flag', I), ('penalty', I),
        ('idealLineOn', I), ('isInPitLane', I), ('surfaceGrip', F),
        ('mandatoryPitDone', I), ('windSpeed', F), ('windDirection', F),
        ('isSetupMenuVisible', I), ('mainDisplayIndex', I),
        ('secondaryDisplayIndex', I), ('TC', I), ('TCCUT', I),
        ('EngineMap', I), ('ABS', I), ('fuelXLap', F), ('rainLights', I),
        ('flashingLights', I), ('lightsStage', I), ('exhaustTemperature', F),
        ('wiperLV', I), ('driverStintTotalTimeLeft', I), ('driverStintTimeLeft', I),
        ('rainTyres', I), ('sessionIndex', I), ('usedFuel', F),
        ('deltaLapTime', W*15), ('iDeltaLapTime', I), ('estimatedLapTime', W*15),
        ('iEstimatedLapTime', I), ('isDeltaPositive', I), ('iSplit', I),
        ('isValidLap', I), ('fuelEstimatedLaps', F), ('trackStatus', W*33),
        ('missingMandatoryPits', I), ('Clock', F), ('directionLightsLeft', I),
        ('directionLightsRight', I), ('GlobalYellow', I), ('GlobalYellow1', I),
        ('GlobalYellow2', I), ('GlobalYellow3', I), ('GlobalWhite', I),
        ('GlobalGreen', I), ('GlobalChequered', I), ('GlobalRed', I),
        ('mfdTyreSet', I), ('mfdFuelToAdd', F), ('mfdTyrePressureLF', F),
        ('mfdTyrePressureRF', F), ('mfdTyrePressureLR', F), ('mfdTyrePressureRR', F),
        ('trackGripStatus', I), ('rainIntensity', I), ('rainIntensityIn10min', I),
        ('rainIntensityIn30min', I), ('currentTyreSet', I), ('strategyTyreSet', I),
        ('gapAhead', I), ('gapBehind', I),
    ]


class StaticPrefix(c.LittleEndianStructure):
    """Documented static-page prefix through track; excludes driver details."""
    _pack_ = 4
    _fields_ = [
        ('smVersion', W*15), ('acVersion', W*15), ('numberOfSessions', I),
        ('numCars', I), ('carModel', W*33), ('track', W*33),
    ]


def _wide_string(value):
    raw = c.string_at(c.addressof(value), c.sizeof(value))
    return raw.decode('utf-16-le', 'replace').split('\0', 1)[0].strip()


def _identity(value):
    normalized = '_'.join(value.casefold().split())
    return normalized or None


class _WindowsMapping:
    def __init__(self, name, size):
        from ctypes import wintypes
        self.api = c.WinDLL('kernel32', use_last_error=True)
        self.api.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.OpenFileMappingW.restype = wintypes.HANDLE
        self.api.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, c.c_size_t]
        self.api.MapViewOfFile.restype = c.c_void_p
        self.api.UnmapViewOfFile.argtypes = [c.c_void_p]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.OpenFileMappingW(4, False, name)
        if not self.handle:
            raise OSError(c.get_last_error(), 'ACC shared memory not available')
        self.pointer = self.api.MapViewOfFile(self.handle, 4, 0, 0, size)
        self.size = size
        if not self.pointer:
            self.api.CloseHandle(self.handle)
            raise OSError(c.get_last_error(), 'Cannot map ACC shared memory')

    def read(self):
        # Packet IDs bracket each copy to avoid accepting a torn page.
        for _ in range(3):
            before = c.c_int32.from_address(self.pointer).value
            data = c.string_at(self.pointer, self.size)
            if before == c.c_int32.from_address(self.pointer).value == struct.unpack_from('<i', data)[0]:
                return data
        raise OSError('ACC page changed during read')

    def close(self):
        self.api.UnmapViewOfFile(self.pointer)
        self.api.CloseHandle(self.handle)


class AccSharedMemory:
    def __init__(self, stale_after_s=2.0):
        self._lock = threading.RLock()
        self.stale_after_s = stale_after_s
        self._maps = []
        self._last_physics_packet = None
        self._last_graphics_packet = None
        self._physics_updated = None
        self._graphics_updated = None
        self._session_epoch = time.time_ns()
        self._last_laps = 0
        self._session_key = None
        self.error = None
        self._owns_maps = False
        self._static_retry_at = 0.0

    def read(self):
        with self._lock:
            return self._read_locked()

    def _read_locked(self):
        if os.name != 'nt':
            self.error = 'ACC shared memory requires Windows'
            return None
        try:
            if not self._maps:
                for name, layout in [('Local\\acpmf_physics', Physics), ('Local\\acpmf_graphics', Graphics)]:
                    self._maps.append(_WindowsMapping(name, c.sizeof(layout)))
                self._owns_maps = True
            if self._owns_maps and len(self._maps) == 2 and time.monotonic() >= self._static_retry_at:
                # Static metadata is optional and can be published later than the
                # live pages. Retry slowly; its absence never disables telemetry.
                self._static_retry_at = time.monotonic() + 1.0
                try:
                    self._maps.append(_WindowsMapping('Local\\acpmf_static', c.sizeof(StaticPrefix)))
                except OSError:
                    pass
            p = Physics.from_buffer_copy(self._maps[0].read())
            g = Graphics.from_buffer_copy(self._maps[1].read())
            static = None
            if len(self._maps) > 2:
                try:
                    static = StaticPrefix.from_buffer_copy(self._maps[2].read())
                except OSError:
                    self._maps[2].close()
                    self._maps.pop()
            now = time.time()
            if p.packetId != self._last_physics_packet:
                self._last_physics_packet, self._physics_updated = p.packetId, now
            if g.packetId != self._last_graphics_packet:
                self._last_graphics_packet, self._graphics_updated = g.packetId, now
            physics_age = max(0.0, now-self._physics_updated) if self._physics_updated is not None else None
            graphics_age = max(0.0, now-self._graphics_updated) if self._graphics_updated is not None else None
            # A newly mapped all-zero physics page is not a believable car
            # sample. Keep zero fuel valid when other physics signals exist.
            physics_signals = (p.speedKmh, p.rpms, p.gear, p.fuel, p.gas, p.brake,
                               *p.accG, *p.wheelsPressure, *p.tyreCoreTemperature,
                               *p.brakeTemp)
            has_physics_data = any(math.isfinite(float(value)) and value != 0 for value in physics_signals)
            physics_valid = (physics_age is not None and physics_age <= self.stale_after_s
                             and has_physics_data)
            stale = (physics_age is None or graphics_age is None
                     or physics_age > self.stale_after_s or graphics_age > self.stale_after_s)
            timestamp = min(self._physics_updated, self._graphics_updated) if not stale else min(
                t for t in (self._physics_updated, self._graphics_updated) if t is not None)
            session_key = (g.sessionIndex, g.session)
            if session_key != self._session_key or g.completedLaps < self._last_laps:
                self._session_epoch = time.time_ns()
            self._session_key = session_key
            self._last_laps = g.completedLaps
            self.error = None
            return {
                'timestamp': timestamp, 'received_at': now, 'stale': stale,
                'physics_valid': physics_valid, 'physics_age_s': physics_age,
                'graphics_age_s': graphics_age,
                'physics_packet_id': p.packetId, 'graphics_packet_id': g.packetId,
                'session_id': f'{self._session_epoch}:{g.sessionIndex}:{g.session}',
                'status': {0:'off', 1:'replay', 2:'live', 3:'paused'}.get(g.status, 'unknown'),
                'car_model': _identity(_wide_string(static.carModel)) if static else None,
                'track': _identity(_wide_string(static.track)) if static else None,
                'is_setup_menu_visible': bool(g.isSetupMenuVisible),
                'session': {-1:'unknown', 0:'practice', 1:'qualifying', 2:'race', 3:'hotlap',
                            4:'time_attack', 5:'drift', 6:'drag', 7:'hotstint',
                            8:'hotstint_superpole'}.get(g.session, 'unknown'),
                'speed_kmh': p.speedKmh if physics_valid else None,
                'rpm': p.rpms if physics_valid else None,
                'gear': p.gear-1 if physics_valid else None,
                'fuel_l': p.fuel if physics_valid else None, 'fuel_per_lap_l': g.fuelXLap,
                'completed_laps': g.completedLaps, 'current_lap_ms': g.iCurrentTime,
                'last_lap_ms': None if g.iLastTime == 2147483647 else g.iLastTime,
                'best_lap_ms': None if g.iBestTime == 2147483647 else g.iBestTime,
                # The SDK PDF omits the unit. A live 10-minute quick race read
                # 600000 here, so ACC encodes milliseconds like its i*Time fields.
                'session_time_left_s': g.sessionTimeLeft/1000,
                'position': g.position, 'player_car_id': g.playerCarID,
                'flag': {0:'none',1:'blue',2:'yellow',3:'black',4:'white',5:'chequered',6:'penalty',7:'green',8:'orange'}.get(g.flag, 'unknown'),
                'penalty': g.penalty, 'lap_valid': bool(g.isValidLap),
                'invalid_lap': not bool(g.isValidLap),
                'damage': list(p.carDamage) if physics_valid else None,
                'tyre_pressure_psi': list(p.wheelsPressure) if physics_valid else None,
                'tyre_temp_c': list(p.tyreCoreTemperature) if physics_valid else None,
                'brake_temp_c': list(p.brakeTemp) if physics_valid else None,
                'acceleration_g': list(p.accG) if physics_valid else None,
                'ambient_temp_c': p.airTemp if physics_valid else None,
                'track_temp_c': p.roadTemp if physics_valid else None, 'rain_intensity': g.rainIntensity,
                'rain_10min': g.rainIntensityIn10min, 'rain_30min': g.rainIntensityIn30min,
                'gap_ahead_s': g.gapAhead/1000 if g.gapAhead > 0 else None,
                'gap_behind_s': g.gapBehind/1000 if g.gapBehind > 0 else None,
                'in_pit': bool(g.isInPit), 'in_pit_lane': bool(g.isInPitLane),
                'global_flags': {
                    'yellow': bool(g.GlobalYellow), 'yellow_1': bool(g.GlobalYellow1),
                    'yellow_2': bool(g.GlobalYellow2), 'yellow_3': bool(g.GlobalYellow3),
                    'white': bool(g.GlobalWhite), 'green': bool(g.GlobalGreen),
                    'chequered': bool(g.GlobalChequered), 'red': bool(g.GlobalRed),
                },
                'tc': g.TC, 'abs': g.ABS,
                'source': 'acc_shared_memory',
            }
        except (OSError, ValueError) as exc:
            self.error = str(exc)
            self.close()
            return None

    def close(self):
        with self._lock:
            for mapping in self._maps:
                mapping.close()
            self._maps.clear()
            self._owns_maps = False
            self._static_retry_at = 0.0
            self._last_physics_packet = None
            self._last_graphics_packet = None
            self._session_key = None
            self._last_laps = 0
            self._physics_updated = None
            self._graphics_updated = None


class PacketReader:
    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, fmt):
        fmt = '<'+fmt
        value = struct.unpack_from(fmt, self.data, self.offset)
        self.offset += struct.calcsize(fmt)
        return value[0] if len(value) == 1 else value

    def string(self):
        length = self.take('H')
        if self.offset+length > len(self.data):
            raise ValueError('Truncated protocol string')
        value = self.data[self.offset:self.offset+length].decode('utf-8', 'replace')
        self.offset += length
        return value


def _string(value):
    data = value.encode('utf-8')
    return struct.pack('<H', len(data))+data


def registration_packet(password='', update_interval_ms=100):
    return bytes([1,4])+_string('ACC AI Race Engineer')+_string(password)+struct.pack('<i',update_interval_ms)+_string('')


def parse_broadcast_packet(data):
    r = PacketReader(data)
    kind = r.take('B')
    result = {'type':kind, 'timestamp':time.time()}
    if kind == 1:
        result.update(connection_id=r.take('i'), success=bool(r.take('B')), read_only=bool(r.take('B')), error=r.string())
    elif kind == 2:
        result.update(event_index=r.take('H'), session_index=r.take('H'), session_type=r.take('B'), phase=r.take('B'), session_time_ms=r.take('f'), session_end_ms=r.take('f'), focused_car_id=r.take('i'))
    elif kind == 3:
        result.update(car_id=r.take('H'), driver_index=r.take('H'), driver_count=r.take('B'), raw_gear=r.take('B'), world_x=r.take('f'), world_y=r.take('f'), yaw=r.take('f'), location=r.take('B'), speed_kmh=r.take('H'), position=r.take('H'), cup_position=r.take('H'), track_position=r.take('H'), spline_position=r.take('f'), laps=r.take('H'), delta_ms=r.take('i'))
        for label in ('best_lap','last_lap','current_lap'):
            lap_ms, car_id, driver = r.take('iHH')
            splits = [r.take('i') for _ in range(r.take('B'))]
            invalid, valid, outlap, inlap = r.take('BBBB')
            result[label] = {'time_ms':None if lap_ms == 2147483647 else lap_ms, 'splits_ms':splits, 'invalid':bool(invalid)}
    elif kind == 4:
        result.update(connection_id=r.take('i'), car_ids=[r.take('H') for _ in range(r.take('H'))])
    elif kind == 5:
        result.update(connection_id=r.take('i'), track=r.string(), track_id=r.take('i'), track_meters=r.take('i'))
    elif kind == 6:
        result.update(car_id=r.take('H'), car_model=r.take('B'), team=r.string(), race_number=r.take('i'), cup_category=r.take('B'), current_driver=r.take('B'), nationality=r.take('H'))
        result['drivers'] = [dict(first_name=r.string(), last_name=r.string(), short_name=r.string(), category=r.take('B'), nationality=r.take('H')) for _ in range(r.take('B'))]
    elif kind == 7:
        result.update(event_type=r.take('B'), message=r.string(), time_ms=r.take('i'), car_id=r.take('i'))
    return result


class BroadcastingClient(asyncio.DatagramProtocol):
    def __init__(self, host='127.0.0.1', port=9000, password='', update_interval_ms=100):
        if host not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('Broadcasting host must be loopback')
        self.host, self.port, self.password = host, port, password
        self.update_interval_ms = max(50, int(update_interval_ms))
        self.transport = None
        self.connection_id = None
        self._status, self._updated = 'disconnected', 0.0
        self._cars, self._session, self._events = {}, {}, []
        self.malformed_packets = 0

    async def start(self):
        if self.transport:
            return
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda:self, remote_addr=(self.host,self.port))
        self._status = 'registering'
        self.transport.sendto(registration_packet(self.password,self.update_interval_ms))

    def connection_made(self, transport):
        self.transport = transport

    def error_received(self, exc):
        self._status = 'unavailable'

    def datagram_received(self, data, addr):
        try:
            packet = parse_broadcast_packet(data)
        except (ValueError, struct.error, UnicodeError):
            self.malformed_packets += 1
            return
        self._updated = time.time()
        kind = packet['type']
        if kind == 1:
            self.connection_id = packet['connection_id'] if packet['success'] else None
            self._status = 'connected' if packet['success'] else 'registration_rejected'
            if packet['success']:
                for request in (10,11):
                    self.transport.sendto(struct.pack('<Bi',request,self.connection_id))
        elif kind == 2:
            self._session = packet
        elif kind in (3,6):
            self._cars.setdefault(packet['car_id'], {}).update(packet)
        elif kind == 4:
            self._cars = {i:self._cars.get(i,{}) for i in packet['car_ids']}
        elif kind == 7:
            self._events = (self._events+[packet])[-50:]

    def snapshot(self):
        stale = bool(self._updated and time.time()-self._updated > 3)
        return deepcopy({'status':'stale' if stale else self._status, 'timestamp':self._updated, 'competitors':list(self._cars.values()), 'session':self._session, 'events':self._events, 'malformed_packets':self.malformed_packets})

    async def stop(self):
        if self.transport:
            if self.connection_id is not None:
                self.transport.sendto(struct.pack('<Bi',9,self.connection_id))
            self.transport.close()
            self.transport = None
        self.connection_id = None
        self._status = 'disconnected'
