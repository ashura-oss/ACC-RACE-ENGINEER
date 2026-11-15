import time

from acc_engineer.tools import RaceTools, TOOL_DEFINITIONS, execute


def test_fresh_context_returns_existing_measurements():
    context = {
        "telemetry": {"timestamp": time.time(), "tyre_pressure_psi": [27.1, 27.0],
                      "tyre_temp_c": [90, 91], "fuel_l": 20,
                      "gap_ahead_s": 1.25, "gap_behind_s": 2.5,
                      "rain_intensity": 1, "rain_10min": 2, "last_lap_ms": 100000,
                      "best_lap_ms": 99500, "lap_valid": True},
        "fuel_projection": {"status": "estimated", "surplus_l": 4.2},
        "events": [{"kind": "damage", "text": "Observed damage"}],
    }
    tools = RaceTools(context)
    assert tools.get_tyre_status()["pressure_psi"] == [27.1, 27.0]
    assert tools.get_fuel_projection()["surplus_l"] == 4.2
    assert tools.get_competitor_gaps()["ahead_s"] == 1.25
    assert tools.get_recent_incidents()["incidents"] == context["events"]
    assert tools.get_weather_forecast()["rain_10min"] == 2
    assert tools.get_lap_comparison()["delta_to_best_ms"] == 500


def test_stale_or_missing_context_is_unknown_without_fabrication():
    stale = RaceTools({"telemetry": {"timestamp": time.time() - 20}, "fuel_projection": {"status": "estimated"}})
    for name in ("get_tyre_status", "get_fuel_projection", "get_competitor_gaps",
                 "get_recent_incidents", "get_weather_forecast", "get_lap_comparison"):
        assert stale.__getattribute__(name)()["status"] == "unknown"
    missing = RaceTools({})
    assert missing.get_tyre_status() == {"status": "unknown", "reason": "Fresh telemetry is unavailable."}


def test_tools_are_argument_free_and_whitelisted():
    assert {t["function"]["name"] for t in TOOL_DEFINITIONS} == {
        "get_tyre_status", "get_fuel_projection", "get_competitor_gaps", "get_recent_incidents",
        "get_weather_forecast", "get_lap_comparison", "get_strategy_plan", "search_driver_memory"}
    assert execute("get_tyre_status", {}, {})["status"] == "unknown"
    assert execute("get_tyre_status", {"field": "secret"}, {})["status"] == "unknown"
    assert execute("execute_shell", {}, {})["status"] == "unknown"


def test_missing_or_nonfinite_timestamps_are_not_treated_as_fresh():
    for timestamp in (None, float("nan"), float("inf"), "not-a-time"):
        context = {"telemetry": {"timestamp": timestamp, "fuel_l": 10},
                   "fuel_projection": {"status": "estimated", "surplus_l": 5}}
        tools = RaceTools(context)
        for name in ("get_tyre_status", "get_fuel_projection", "get_competitor_gaps",
                     "get_recent_incidents", "get_weather_forecast", "get_lap_comparison"):
            assert tools.__getattribute__(name)()["status"] == "unknown"


def test_empty_and_nonfinite_readings_remain_unknown():
    tools = RaceTools({"telemetry": {"timestamp": time.time(),
                                     "tyre_pressure_psi": [], "tyre_temp_c": [float("nan")],
                                     "gap_ahead_s": float("inf"), "gap_behind_s": None,
                                     "ambient_temp_c": float("nan"), "rain_intensity": None,
                                     "last_lap_ms": True, "best_lap_ms": 90000}})
    assert tools.get_tyre_status()["status"] == "unknown"
    assert tools.get_competitor_gaps()["status"] == "unknown"
    assert tools.get_weather_forecast()["status"] == "unknown"
    assert tools.get_lap_comparison()["status"] == "unknown"
