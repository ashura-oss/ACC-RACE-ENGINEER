"""Deterministic race engineering. Thresholds are provisional and configurable.

Calculations use measured completed laps, never language-model estimates. Missing,
stale, nonfinite and out-of-order telemetry cannot trigger race announcements.
"""
from __future__ import annotations

import math
import statistics
import time
import uuid
from typing import Any


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def fuel_projection(fuel_l: float | None, consumption_laps: list[float],
                    lap_times_s: list[float], remaining_s: float | None,
                    reserve_l: float = 1.0) -> dict:
    """Conservative timed-race projection, including one finishing lap.

    Maximum consumption and fastest measured lap in the last ten valid samples
    bound observed variation. ACC timed races can continue after the clock expires;
    ceil(time / fastest lap) + 1 deliberately budgets a finishing lap. This is an
    estimate, not a guarantee about leader timing or future consumption.
    """
    fuel, remaining, reserve = number(fuel_l), number(remaining_s), number(reserve_l)
    consumption = [x for v in consumption_laps[-10:] if (x := number(v)) is not None and x > 0]
    times = [x for v in lap_times_s[-10:] if (x := number(v)) is not None and x > 10]
    if fuel is None or fuel < 0 or remaining is None or remaining < 0 or reserve is None or reserve < 0 or not consumption or not times:
        return {"status": "unknown", "reason": "Need valid fuel, time remaining and measured completed-lap consumption and times."}
    laps = math.ceil(remaining / min(times)) + 1
    required = laps * max(consumption) + reserve
    return {"status": "estimated", "laps_to_finish": laps, "required_l": round(required, 3),
            "surplus_l": round(fuel - required, 3), "mean_consumption_l": round(statistics.mean(consumption), 3),
            "conservative_consumption_l": max(consumption), "reserve_l": reserve,
            "sample_count": min(len(consumption), len(times)),
            "assumption": "Fastest observed lap, maximum observed fuel per lap, one extra finishing lap."}


class EventEngine:
    DEFAULTS = {"stale_after_s": 2.0, "cooldown_s": 30.0, "low_fuel_l": 3.0,
                "damage_minor": 0.5, "damage_moderate": 3.0, "damage_severe": 12.0,
                "impact_speed_drop_kmh": 35.0, "tyre_hot_c": 110.0,
                "brake_hot_c": 900.0, "reserve_l": 1.0,
                "gap_trend_window_s": 5.0, "gap_trend_threshold_s": 0.5}

    def __init__(self, config: dict | None = None):
        self.config = self.DEFAULTS | (config or {})
        for key, value in self.config.items():
            if key not in self.DEFAULTS or number(value) is None or value < 0:
                raise ValueError(f"Invalid event threshold: {key}")
        self.session_id = None
        self.previous: dict | None = None
        self.cooldowns: dict[str, float] = {}
        self.fuel_history: list[float] = []
        self.lap_history: list[float] = []
        self._lap_fuel: float | None = None
        self._lap_refuelled = False
        self._lap_valid: bool | None = None
        self._best_lap: float | None = None
        self.gap_history: dict[str, list[tuple[float, float]]] = {"gap_ahead_s": [], "gap_behind_s": []}

    def projection(self, snapshot: dict) -> dict:
        return fuel_projection(snapshot.get("fuel_l"), self.fuel_history, self.lap_history,
                               snapshot.get("session_time_left_s"), self.config["reserve_l"])

    def process(self, snapshot: dict) -> list[dict]:
        stamp = number(snapshot.get("timestamp"))
        now = time.time()
        if stamp is None or now - stamp > self.config["stale_after_s"] or stamp > now + 1:
            return []
        if snapshot.get("status") not in (None, "live", "replay", "demo"):
            return []
        session = snapshot.get("session_id", "unknown")
        if session != self.session_id:
            self.session_id, self.previous = session, None
            self.cooldowns.clear()
            self.fuel_history.clear()
            self.lap_history.clear()
            self._lap_fuel, self._best_lap = None, None
            self._lap_refuelled = False
            self._lap_valid = None
            self.gap_history = {"gap_ahead_s": [], "gap_behind_s": []}
        prev = self.previous
        if prev and stamp <= prev["timestamp"]:
            return []
        events = []
        if self._best_lap is None:
            known_best = number((prev or snapshot).get("best_lap_ms"))
            if known_best is not None and known_best > 10000:
                self._best_lap = known_best

        def emit(kind: str, priority: int, text: str, evidence: dict, cooldown: bool = True):
            if cooldown and stamp - self.cooldowns.get(kind, -math.inf) < self.config["cooldown_s"]:
                return
            self.cooldowns[kind] = stamp
            events.append({"id": uuid.uuid4().hex, "kind": kind, "priority": priority,
                           "text": text, "timestamp": stamp, "source": evidence})

        flag = str(snapshot.get("flag", "none")).lower()
        previous_flag = str((prev or {}).get("flag", "none")).lower()
        flags = {"yellow": (0, "Yellow flag. Be careful."), "blue": (1, "Blue flag. Watch for faster traffic."),
                 "green": (1, "Green flag."), "red": (0, "Red flag. Follow race control instructions."),
                 "checkered": (1, "Chequered flag. Session complete."), "chequered": (1, "Chequered flag. Session complete.")}
        if flag != previous_flag and flag in flags:
            priority, text = flags[flag]
            emit("flag_" + flag, priority, text, {"flag": flag}, cooldown=False)

        # Transition announcements use explicit measurements/states only.
        if prev:
            rain, old_rain = number(snapshot.get("rain_intensity")), number(prev.get("rain_intensity"))
            if rain is not None and old_rain is not None and rain != old_rain:
                emit("rain_change", 2, "Rain conditions have changed.",
                     {"previous_rain": old_rain, "rain": rain}, cooldown=False)
            for field, kind, text in (
                ("penalty", "penalty_received", "Penalty received. Check the race control notice."),
                ("invalid_lap", "lap_invalidated", "Lap invalidated. Keep an eye on track limits."),
                ("pit_window_open", "pit_window_opened", "Pit window is open."),
            ):
                old, current = prev.get(field), snapshot.get(field)
                rising = old is False and current is True
                if field == "penalty":
                    old_n, current_n = number(old), number(current)
                    rising = rising or (old_n is not None and current_n is not None and current_n > old_n)
                if rising:
                    emit(kind, 1, text, {field: True}, cooldown=False)

        # Keep fresh per-frame samples to evaluate a sustained window. A single
        # old frame cannot be compared across the stale telemetry cutoff.
        for field, direction, kind, text in (
            ("gap_ahead_s", "down", "gap_ahead_closing", "The gap ahead is coming down."),
            ("gap_behind_s", "up", "gap_behind_gaining", "The car behind is closing in."),
        ):
            gap = number(snapshot.get(field))
            samples = self.gap_history[field]
            if gap is not None:
                samples.append((stamp, gap))
            samples[:] = [(t, v) for t, v in samples if stamp - t <= self.config["gap_trend_window_s"] + 2]
            anchor = next(((t, v) for t, v in samples if stamp - t >= self.config["gap_trend_window_s"]), None)
            if anchor and gap is not None:
                interval, change = stamp - anchor[0], gap - anchor[1]
                trending = change <= -self.config["gap_trend_threshold_s"] if direction == "down" else change >= self.config["gap_trend_threshold_s"]
                if trending:
                    emit(kind, 2, text, {"field": field, "previous_s": anchor[1],
                         "current_s": gap, "change_s": round(change, 3), "window_s": round(interval, 3)})

        # Session lifecycle is announced only for explicit phase/status values.
        if prev:
            old_phase, phase = prev.get("phase"), snapshot.get("phase")
            old_race, race = prev.get("race_status"), snapshot.get("race_status")
            if (old_phase != phase and phase in ("race", "started")) or (old_race != race and race == "started"):
                emit("session_started", 1, "Race session started.", {"phase": phase, "race_status": race}, cooldown=False)
            if (old_phase != phase and phase in ("finished", "complete")) or (old_race != race and race in ("finished", "complete")):
                emit("session_finished", 1, "Race session finished.", {"phase": phase, "race_status": race}, cooldown=False)

        fuel = number(snapshot.get("fuel_l"))
        if fuel is not None and 0 <= fuel <= self.config["low_fuel_l"]:
            emit("low_fuel", 0, "Fuel is low. Check the fuel projection.", {"fuel_l": fuel})
        if prev:
            previous_fuel = number(prev.get("fuel_l"))
            if fuel is not None and previous_fuel is not None and fuel > previous_fuel + 0.1:
                self._lap_refuelled = True
            lap, previous_lap = number(snapshot.get("completed_laps")), number(prev.get("completed_laps"))
            if lap is not None and previous_lap is not None and lap > previous_lap:
                lap_ms = number(snapshot.get("last_lap_ms"))
                # ACC's isValidLap describes the current lap, which resets at
                # the timing line. Carry its state across the completed lap.
                valid = snapshot.get("last_lap_valid", self._lap_valid) is True
                if lap - previous_lap == 1 and lap_ms and lap_ms > 10000 and valid:
                    self.lap_history.append(lap_ms / 1000)
                    if self._lap_fuel is not None and fuel is not None and not self._lap_refuelled:
                        used = self._lap_fuel - fuel
                        if used > 0:
                            self.fuel_history.append(used)
                    if self._best_lap is not None and lap_ms < self._best_lap:
                        emit("personal_best", 3, "Personal best. Keep that consistency.", {"lap_ms": lap_ms}, cooldown=False)
                    else:
                        emit("lap_complete", 3, f"Lap complete. {lap_ms / 1000:.3f} seconds.", {"lap_ms": lap_ms}, cooldown=False)
                    self._best_lap = min(self._best_lap or lap_ms, lap_ms)
                self._lap_fuel, self._lap_refuelled = fuel, False
                self._lap_valid = snapshot.get("lap_valid") if isinstance(snapshot.get("lap_valid"), bool) else None
            damage, old_damage = snapshot.get("damage"), prev.get("damage")
            if isinstance(damage, (list, tuple)) and isinstance(old_damage, (list, tuple)) and len(damage) == len(old_damage):
                pairs = [(number(a), number(b)) for a, b in zip(damage, old_damage)]
                if pairs and all(a is not None and b is not None for a, b in pairs):
                    deltas = [max(0.0, a - b) for a, b in pairs]
                    increase = sum(deltas)
                    speed, old_speed = number(snapshot.get("speed_kmh")), number(prev.get("speed_kmh"))
                    drop = max(0.0, old_speed - speed) if speed is not None and old_speed is not None else 0.0
                    corroborated = drop >= self.config["impact_speed_drop_kmh"] or sum(d > self.config["damage_minor"] for d in deltas) >= 2
                    evidence = {"damage_delta": deltas, "speed_drop_kmh": drop, "corroborated": corroborated}
                    if increase >= self.config["damage_severe"] and corroborated:
                        emit("severe_incident", 0, "That's a big hit. You alright? Assess the car when it's safe.", evidence)
                    elif increase >= self.config["damage_moderate"]:
                        emit("damage", 1, "We've picked up damage. How is the handling?", evidence)
                    elif increase >= self.config["damage_minor"]:
                        emit("minor_damage", 3, "Some damage detected. Keep an eye on the handling.", evidence)

        for field, threshold, kind, text in (
            ("tyre_temp_c", "tyre_hot_c", "tyres_hot", "Tyre temperatures are high. Manage the tyres."),
            ("brake_temp_c", "brake_hot_c", "brakes_hot", "Brake temperatures are high. Keep an eye on them."),
        ):
            values = snapshot.get(field)
            if isinstance(values, (list, tuple)):
                readings = [x for v in values if (x := number(v)) is not None]
                if readings and max(readings) >= self.config[threshold]:
                    emit(kind, 1, text, {field: readings, "threshold": self.config[threshold]})
        projection = self.projection(snapshot)
        if projection.get("status") == "estimated" and projection["surplus_l"] < 0:
            emit("fuel_deficit", 1, f"Fuel estimate is {abs(projection['surplus_l']):.1f} litres short, including reserve. Start saving.", projection)
        self.fuel_history[:] = self.fuel_history[-50:]
        self.lap_history[:] = self.lap_history[-50:]
        if snapshot.get("lap_valid") is False:
            self._lap_valid = False
        elif self._lap_valid is None and snapshot.get("lap_valid") is True:
            self._lap_valid = True
        self.previous = dict(snapshot)
        return sorted(events, key=lambda event: event["priority"])
