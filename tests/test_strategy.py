import time

from acc_engineer.strategy import strategy_plan


def test_fuel_deficit_with_closed_pit_window_does_not_issue_box_instruction():
    context = {"telemetry": {"timestamp": time.time(), "status": "live", "fuel_l": 8,
                             "pit_window_open": False},
               "fuel_projection": {"status": "estimated", "surplus_l": -2.4}}
    plan = strategy_plan(context)
    assert plan["status"] == "measured"
    assert plan["actions"][0]["pit_call"] == "window_closed"
    assert "2.4" in plan["actions"][0]["call"]
    assert "box" not in plan["actions"][0]["call"].lower()
    context["telemetry"]["physics_valid"] = False
    assert strategy_plan(context)["status"] == "unknown"


def test_missing_fuel_history_does_not_fabricate_a_finish_margin():
    context = {"telemetry": {"timestamp": time.time(), "status": "live", "fuel_l": 20},
               "fuel_projection": {"status": "unknown"}}
    plan = strategy_plan(context)
    assert plan["actions"] == []
    assert plan["fuel"]["status"] == "unknown"
