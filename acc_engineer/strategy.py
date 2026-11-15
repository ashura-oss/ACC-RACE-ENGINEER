"""Measured race plan. Unknown pit costs and opponent intentions stay unknown."""
import math
import time


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def strategy_plan(context):
    frame = context.get("telemetry")
    if not isinstance(frame, dict) or context.get("telemetry_stale"):
        return {"status": "unknown", "reason": "Fresh race telemetry is unavailable.", "actions": []}
    stamp = _number(frame.get("timestamp"))
    if (stamp is None or not -1 <= time.time() - stamp <= 2 or frame.get("stale") or
            frame.get("physics_valid") is False or frame.get("status") != "live"):
        return {"status": "unknown", "reason": "Fresh race telemetry is unavailable.", "actions": []}
    actions = []
    projection = context.get("fuel_projection") or {}
    fuel = _number(frame.get("fuel_l"))
    surplus = _number(projection.get("surplus_l"))
    pit_closed = frame.get("pit_window_open") is False
    if projection.get("status") == "estimated" and surplus is not None:
        if surplus < 0:
            actions.append({"kind": "fuel", "priority": 1,
                "call": f"Fuel estimate is {-surplus:.1f} litres short with reserve. Start saving.",
                "evidence": projection, "pit_call": "window_closed" if pit_closed else "assess_refuelling"})
        else:
            actions.append({"kind": "fuel", "priority": 3,
                "call": f"Estimated fuel margin is {surplus:.1f} litres including reserve.", "evidence": projection})
    elif fuel is not None and fuel <= 3:
        actions.append({"kind": "fuel", "priority": 0,
            "call": "Fuel is critical. Save fuel and assess whether you can reach the pits.", "evidence": {"fuel_l": fuel}})
    for field, threshold, kind, call in (
        ("tyre_temp_c", 110, "tyres", "Ease the tyre load; measured core temperatures are high."),
        ("brake_temp_c", 900, "brakes", "Manage the brakes; measured temperatures are high."),
    ):
        values = frame.get(field)
        if isinstance(values, (list, tuple)):
            values = [v for x in values if (v := _number(x)) is not None]
            if values and max(values) >= threshold:
                actions.append({"kind": kind, "priority": 1, "call": call,
                    "evidence": {field: values, "provisional_threshold": threshold}})
    rain, forecast = _number(frame.get("rain_intensity")), _number(frame.get("rain_10min"))
    if rain is not None and forecast is not None and forecast > rain:
        actions.append({"kind": "weather", "priority": 2,
            "call": "ACC forecasts more rain in ten minutes. Be ready to reassess grip and tyres.",
            "evidence": {"rain_intensity": rain, "rain_10min": forecast}})
    return {"status": "measured", "actions": sorted(actions, key=lambda x: x["priority"]),
        "fuel": projection, "pit_window_open": frame.get("pit_window_open"),
        "limits": "No verified pit-loss or opponent stint model: an optimal undercut, overcut, or tyre change is not established."}
