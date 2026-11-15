import time

import pytest

from acc_engineer.engineering import EventEngine, fuel_projection


def frame(**kwargs):
    return {"timestamp": time.time(), "session_id": "race1", "status": "live",
            "fuel_l": 30, "completed_laps": 1, "speed_kmh": 150,
            "damage": [0, 0, 0, 0], "flag": "none", "lap_valid": True, **kwargs}


def test_projection_uses_fastest_lap_max_consumption_and_finishing_lap():
    result = fuel_projection(20, [2, 2.5], [100, 90], 180, 1)
    assert result["laps_to_finish"] == 3
    assert result["required_l"] == 8.5
    assert result["surplus_l"] == 11.5


@pytest.mark.parametrize("fuel,remaining,consumption,times", [
    (None, 300, [2], [100]), (20, None, [2], [100]),
    (20, 300, [], [100]), (20, 300, [2], []),
    (float("nan"), 300, [2], [100]), (20, -1, [2], [100]),
])
def test_unknown_projection_does_not_invent_values(fuel, remaining, consumption, times):
    assert fuel_projection(fuel, consumption, times, remaining)["status"] == "unknown"


def test_flag_edges_and_cooldown():
    engine = EventEngine()
    assert engine.process(frame(flag="yellow"))[0]["priority"] == 0
    assert engine.process(frame(flag="yellow")) == []
    engine.process(frame(flag="none"))
    assert engine.process(frame(flag="yellow"))[0]["kind"] == "flag_yellow"


def test_stale_out_of_order_and_nonlive_telemetry_is_suppressed():
    engine = EventEngine()
    assert engine.process(frame(timestamp=time.time() - 10, fuel_l=0)) == []
    assert engine.process(frame(status="off", fuel_l=0)) == []
    first = frame()
    engine.process(first)
    assert engine.process(frame(timestamp=first["timestamp"] - .1, fuel_l=0)) == []


def test_severe_incident_requires_corroboration():
    engine = EventEngine()
    engine.process(frame())
    result = engine.process(frame(damage=[13, 0, 0, 0]))
    assert [e["kind"] for e in result] == ["damage"]
    engine = EventEngine()
    engine.process(frame())
    result = engine.process(frame(damage=[13, 0, 0, 0], speed_kmh=40))
    assert result[0]["kind"] == "severe_incident"
    assert result[0]["priority"] == 0
    assert result[0]["source"]["corroborated"] is True


def test_fuel_ignores_initial_partial_lap_and_refuelling():
    engine = EventEngine()
    engine.process(frame(fuel_l=30))
    engine.process(frame(completed_laps=2, fuel_l=29, last_lap_ms=90000))
    assert engine.fuel_history == []
    engine.process(frame(completed_laps=3, fuel_l=26.5, last_lap_ms=91000))
    assert engine.fuel_history == [2.5]
    engine.process(frame(completed_laps=3, fuel_l=40))
    engine.process(frame(completed_laps=4, fuel_l=38, last_lap_ms=95000))
    assert engine.fuel_history == [2.5]


def test_session_change_resets_history_and_warning_cooldown():
    engine = EventEngine()
    engine.process(frame(fuel_l=1))
    engine.fuel_history.append(2)
    events = engine.process(frame(session_id="race2", fuel_l=1))
    assert engine.fuel_history == []
    assert events[0]["kind"] == "low_fuel"


def test_invalid_laps_not_used_for_consumption():
    engine = EventEngine()
    engine.process(frame())
    engine.process(frame(completed_laps=2, fuel_l=28, last_lap_ms=90000))
    engine.process(frame(completed_laps=3, fuel_l=26, last_lap_ms=90000, last_lap_valid=False))
    assert engine.fuel_history == []


def test_transition_events_require_explicit_edges_and_rain_measurement_change():
    engine = EventEngine()
    now = time.time()
    engine.process(frame(timestamp=now, penalty=0, invalid_lap=False,
                         pit_window_open=False, rain_intensity=0))
    assert engine.process(frame(timestamp=now + .1, penalty=0, invalid_lap=False,
                                pit_window_open=False, rain_intensity=0)) == []
    events = engine.process(frame(timestamp=now + .2, penalty=1, invalid_lap=True,
                                  pit_window_open=True, rain_intensity=1))
    assert {event["kind"] for event in events} == {
        "penalty_received", "lap_invalidated", "pit_window_opened", "rain_change"}


def test_gap_trend_requires_sustained_window_and_resets_per_session():
    engine = EventEngine({"stale_after_s": 10, "gap_trend_threshold_s": .5})
    now = time.time()
    engine.process(frame(timestamp=now - 5.2, gap_ahead_s=4, gap_behind_s=3))
    # Fresh intermediate frames preserve the measured trend through the stale cutoff.
    assert engine.process(frame(timestamp=now - 2.6, gap_ahead_s=3.8, gap_behind_s=3.2)) == []
    events = engine.process(frame(timestamp=now, gap_ahead_s=3.2, gap_behind_s=3.8))
    assert {event["kind"] for event in events} == {"gap_ahead_closing", "gap_behind_gaining"}
    assert all(event["source"]["window_s"] >= 5 for event in events)
    # A session reset clears anchors; no trend can bridge sessions.
    assert engine.process(frame(timestamp=now + .1, session_id="race2",
                                 gap_ahead_s=3, gap_behind_s=4)) == []


def test_cooldown_rules_rearm_only_on_transition():
    engine = EventEngine()
    now = time.time()
    engine.process(frame(timestamp=now, pit_window_open=False))
    assert [e["kind"] for e in engine.process(frame(timestamp=now + .1, pit_window_open=True))] == ["pit_window_opened"]
    assert engine.process(frame(timestamp=now + .2, pit_window_open=True)) == []
