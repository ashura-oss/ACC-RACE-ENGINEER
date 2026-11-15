"""Read-only deterministic race-data tools exposed to the model."""
from __future__ import annotations

import math
import time
from copy import deepcopy


def _unknown(reason: str) -> dict:
    return {"status": "unknown", "reason": reason}


def _finite_number(value) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _has_measurement(value) -> bool:
    if isinstance(value, (list, tuple)):
        return any(_finite_number(item) for item in value)
    return _finite_number(value)


class RaceTools:
    """Read-only queries over a snapshot of the application's race context."""

    def __init__(self, context: dict | None):
        self.context = context if isinstance(context, dict) else {}

    def _frame(self) -> dict | None:
        ctx = self.context
        frame = ctx.get("telemetry")
        if not isinstance(frame, dict) or ctx.get("telemetry_stale", False):
            return None
        stamp = frame.get("timestamp")
        if not _finite_number(stamp):
            return None
        age = time.time() - float(stamp)
        if age < -1 or age > 2:
            return None
        if frame.get("stale") is True:
            return None
        return frame

    def get_tyre_status(self) -> dict:
        f = self._frame()
        if f is None:
            return _unknown("Fresh telemetry is unavailable.")
        pressure, temp = f.get("tyre_pressure_psi"), f.get("tyre_temp_c")
        if not _has_measurement(pressure) and not _has_measurement(temp):
            return _unknown("Tyre readings are unavailable.")
        return {"status": "available", "pressure_psi": deepcopy(pressure), "core_temp_c": deepcopy(temp)}

    def get_fuel_projection(self) -> dict:
        if self._frame() is None:
            return _unknown("Fresh telemetry is unavailable.")
        projection = self.context.get("fuel_projection")
        if not isinstance(projection, dict) or projection.get("status") in (None, "unknown"):
            return _unknown("Measured fuel consumption or lap timing is unavailable.")
        return deepcopy(projection)

    def get_competitor_gaps(self) -> dict:
        f = self._frame()
        if f is None:
            return _unknown("Fresh telemetry is unavailable.")
        gaps = {"ahead_s": f.get("gap_ahead_s") if _finite_number(f.get("gap_ahead_s")) else None,
                "behind_s": f.get("gap_behind_s") if _finite_number(f.get("gap_behind_s")) else None}
        competitors = self.context.get("competitors")
        if isinstance(competitors, dict):
            cars = competitors.get("competitors")
            if competitors.get("status") not in ("stale", "disconnected", "unavailable") and isinstance(cars, list):
                pid = f.get("player_car_id")
                others = [c for c in cars if isinstance(c, dict) and c.get("car_id") != pid]
                gaps["broadcast_status"] = competitors.get("status")
                gaps["nearby_competitors"] = [{k: c.get(k) for k in ("car_id", "position", "delta_ms", "speed_kmh", "location") if k in c} for c in others[:8]]
        if gaps["ahead_s"] is None and gaps["behind_s"] is None and "nearby_competitors" not in gaps:
            return _unknown("Competitor gaps are not available in the current telemetry.")
        return {"status": "available", **gaps}

    def get_recent_incidents(self) -> dict:
        if self._frame() is None:
            return _unknown("Fresh telemetry is unavailable.")
        events = self.context.get("events")
        if not isinstance(events, list):
            return _unknown("Recent incident history is unavailable.")
        incidents = [deepcopy(e) for e in events if isinstance(e, dict) and any(x in str(e.get("kind", "")).lower() for x in ("damage", "incident", "impact", "collision"))]
        return {"status": "available", "incidents": incidents}

    def get_weather_forecast(self) -> dict:
        f = self._frame()
        if f is None:
            return _unknown("Fresh telemetry is unavailable.")
        keys = ("ambient_temp_c", "track_temp_c", "rain_intensity", "rain_10min", "rain_30min")
        values = {k: f[k] for k in keys if _finite_number(f.get(k))}
        if not values:
            return _unknown("Weather readings are unavailable.")
        return {"status": "available", **values}

    def get_lap_comparison(self) -> dict:
        f = self._frame()
        if f is None:
            return _unknown("Fresh telemetry is unavailable.")
        last, best = f.get("last_lap_ms"), f.get("best_lap_ms")
        if not _finite_number(last) or not _finite_number(best) or float(last) <= 0 or float(best) <= 0:
            return _unknown("Completed lap and best lap times are unavailable.")
        last_ms, best_ms = float(last), float(best)
        return {"status": "available", "last_lap_ms": last_ms, "best_lap_ms": best_ms,
                "delta_to_best_ms": last_ms - best_ms, "lap_valid": f.get("lap_valid")}

    def get_strategy_plan(self) -> dict:
        from .strategy import strategy_plan
        return strategy_plan(self.context)


TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": name, "description": description,
     "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}}
    for name, description in (
        ("get_tyre_status", "Read current tyre pressure and core temperatures."),
        ("get_fuel_projection", "Read the deterministic fuel projection."),
        ("get_competitor_gaps", "Read measured gaps to competitors."),
        ("get_recent_incidents", "Read recent detected damage or incident events."),
        ("get_weather_forecast", "Read current and ACC-provided near-term weather readings."),
        ("get_lap_comparison", "Compare last completed lap with the recorded best."),
        ("get_strategy_plan", "Read the measured fuel, tyre, brake and weather plan; unknown pit strategy remains unknown."),
    )
]

TOOL_DEFINITIONS.append({"type": "function", "function": {
    "name": "search_driver_memory",
    "description": "Search persistent local driver memory when you need a name, preference, or past session/conversation. Retrieve only what this question needs; past assistant statements are not verified current telemetry.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 200},
        "limit": {"type": "integer", "minimum": 1, "maximum": 8}},
        "required": ["query"], "additionalProperties": False}}})

_METHODS = {name: getattr(RaceTools, name) for name in (
    "get_tyre_status", "get_fuel_projection", "get_competitor_gaps",
    "get_recent_incidents", "get_weather_forecast", "get_lap_comparison", "get_strategy_plan")}


def execute(name: str, args: dict, context: dict | None = None, memory_search=None) -> dict:
    """Execute a whitelisted, argument-free read-only tool."""
    if name == "search_driver_memory":
        if not callable(memory_search) or not isinstance(args, dict) or set(args) - {"query", "limit"}:
            return _unknown("Local memory search is unavailable or arguments are invalid.")
        query, limit = args.get("query"), args.get("limit", 5)
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8:
            return _unknown("Memory search needs a query and a limit between one and eight.")
        return {"status": "available", "results": memory_search(query, limit=limit)}
    method = _METHODS.get(name)
    if method is None:
        return _unknown("Tool is not available.")
    if args not in ({}, None):
        return _unknown("This tool accepts no arguments.")
    try:
        return method(RaceTools(context))
    except Exception:
        return _unknown("Race data could not be read.")
