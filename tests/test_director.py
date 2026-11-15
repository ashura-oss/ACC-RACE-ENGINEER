from acc_engineer.director import EngineerDirector


def frame(t=1000, **extra):
    return {"timestamp": t, "session_id": "r1", "status": "live",
            "physics_valid": True, "session": "race", "speed_kmh": 160,
            "position": 2, "completed_laps": 5, "flag": "none", **extra}


def test_rejects_stale_replay_invalid_physics_and_out_of_order():
    d = EngineerDirector()
    ev = [{"kind": "personal_best"}]
    assert d.process(frame(t=980), ev, {}, now=1000) == []
    assert d.process(frame(status="replay"), ev, {}, now=1000) == []
    assert d.process(frame(physics_valid=False), ev, {}, now=1000) == []
    assert len(d.process(frame(t=1000), ev, {}, now=1000)) == 1
    assert d.process(frame(t=999), ev, {}, now=1000) == []


def test_session_lifecycle_resets_cooldown_and_never_claims_win_from_finish_alone():
    d = EngineerDirector()
    first = d.process(frame(t=1000, session_id="a", phase="finished"),
                       [{"kind": "session_finished"}], {}, now=1000)
    assert first[0]["kind"] == "finish"
    assert "win" not in first[0]["text"].lower()
    assert d.snapshot()["confirmed_win"] is False
    win = d.process(frame(t=1001, session_id="b", position=1, phase="finished"),
                    [{"kind": "session_finished"}], {}, now=1001)
    assert win[0]["kind"] == "win"
    assert win[0]["clip_id"] == "race_win"
    assert win[0]["priority"] == 1


def test_chequered_race_position_one_is_confirmed_win_only_once():
    d = EngineerDirector()
    first = d.process(frame(position=1, track="monza", car_model="bmw_m4"),
                      [{"kind": "flag_checkered"}], {}, now=1000)
    assert [(x["kind"], x["clip_id"]) for x in first] == [("win", "race_win")]
    assert d.process(frame(t=1001, position=1), [{"kind": "session_finished"}], {}, now=1001) == []
    assert d.summary() == {"verified_final": True, "session_id": "r1", "track": "monza",
                           "car": "bmw_m4", "laps": 5, "position": 1,
                           "confirmed_win": True}


def test_replay_chequered_never_claims_a_win():
    d = EngineerDirector()
    assert d.process(frame(status="replay", position=1), [{"kind": "flag_checkered"}], {}, now=1000) == []
    assert not d.snapshot()["confirmed_win"]


def test_severe_incident_uses_cached_p0_and_acknowledgement_cancels_followup():
    d = EngineerDirector()
    got = d.process(frame(), [{"kind": "severe_incident", "source": {"damage_delta": [13]}}], {}, now=1000)
    assert got[0]["priority"] == 0
    assert got[0]["clip_id"] == "damage_severe"
    assert d.note_driver("I'm okay", now=1003)
    assert d.process(frame(t=1015), [], {}, now=1015) == []
    assert d.snapshot()["pending_checkin"] is False


def test_negative_car_status_does_not_clear_concern_or_count_as_ack():
    d = EngineerDirector()
    d.process(frame(), [{"kind": "severe_incident"}], {}, now=1000)
    assert d.note_driver("I'm not okay", now=1002) is False
    assert d.snapshot()["mood"] == "concerned"
    assert d.snapshot()["pending_checkin"] is False  # driver's answer ends the prompt cycle
    assert d.process(frame(t=1015), [], {}, now=1015) == []


def test_low_fuel_is_cached_p0_even_when_frequency_is_off():
    d = EngineerDirector({"frequency": "off"})
    call = d.process(frame(), [{"kind": "low_fuel"}], {}, now=1000)
    assert call[0]["priority"] == 0
    assert call[0]["clip_id"] == "fuel_critical"


def test_unacknowledged_incident_gets_one_delayed_followup():
    d = EngineerDirector()
    d.process(frame(), [{"kind": "severe_incident"}], {}, now=1000)
    got = d.process(frame(t=1015), [], {}, now=1015)
    assert len(got) == 1
    assert got[0]["kind"] == "concern_followup"
    assert got[0]["clip_id"] == "incident_follow_up"
    assert got[0]["priority"] == 1
    assert d.process(frame(t=1017), [], {}, now=1017) == []


def test_fuel_strategy_is_measured_once_and_respects_pit_constraints():
    d = EngineerDirector()
    projection = {"status": "estimated", "surplus_l": -4.2, "laps_to_finish": 3}
    call = d.process(frame(pit_window_open=True, pitlane_closed=True), [], projection, now=1000)
    assert len(call) == 1
    assert "4.2 litres short" in call[0]["text"]
    assert "Box" not in call[0]["text"]
    assert call[0]["dynamic"] is True
    assert d.process(frame(t=1001), [{"kind": "fuel_deficit"}], projection, now=1001) == []


def test_fuel_strategy_never_calls_box_on_final_projected_lap():
    d = EngineerDirector()
    projection = {"status": "estimated", "surplus_l": -2, "laps_to_finish": 1}
    call = d.process(frame(pit_window_open=True), [], projection, now=1000)
    assert "Box" not in call[0]["text"]


def test_off_suppresses_checkins_personal_best_and_strategy_but_keeps_urgent():
    d = EngineerDirector({"frequency": "off"})
    assert d.process(frame(), [{"kind": "personal_best"}], {}, now=1000) == []
    assert d.process(frame(t=1121), [], {"status": "estimated", "surplus_l": -2,
                                         "laps_to_finish": 3}, now=1121)[0]["kind"] == "fuel_strategy"
    assert d.process(frame(t=1122), [{"kind": "severe_incident"}], {}, now=1122)[0]["priority"] == 0


def test_checkin_requires_two_safe_minutes_and_pauses_in_pits_or_under_yellow():
    d = EngineerDirector()
    assert d.process(frame(), [], {}, now=1000) == []
    assert d.process(frame(t=1120), [], {}, now=1120) == []
    got = d.process(frame(t=1121), [], {}, now=1121)
    assert len(got) == 1 and got[0]["kind"] == "checkin"
    assert got[0]["text"] == "how's the car feeling?"
    assert got[0]["emotion"] == "neutral"
    d = EngineerDirector()
    d.process(frame(), [], {}, now=1000)
    d.process(frame(t=1100, flag="yellow"), [], {}, now=1100)
    assert d.process(frame(t=1201), [], {}, now=1201) == []
    assert d.process(frame(t=1204), [], {}, now=1204)[0]["kind"] == "checkin"


def test_existing_engine_events_keep_cached_clip_ids():
    d = EngineerDirector()
    kinds = ["flag_green", "flag_blue", "rain_change", "gap_ahead_closing",
             "gap_behind_closing", "pit_window_opened", "minor_damage"]
    expected = {"flag_green": "green", "flag_blue": "blue", "rain_change": "rain_change",
                "gap_ahead_closing": "gap_ahead_closing", "gap_behind_closing": "gap_behind_gaining",
                "pit_window_opened": "pit_open", "minor_damage": "damage_minor"}
    for i, kind in enumerate(kinds):
        got = d.process(frame(t=1000 + i, flag="none"), [{"kind": kind}], {}, now=1000 + i)
        assert len(got) == 1
        assert got[0]["clip_id"] == expected[kind]


def test_profile_name_and_profanity_only_affect_win_when_enabled():
    ordinary = EngineerDirector(profile={"driver_name": "Ari", "profanity": False})
    normal = ordinary.process(frame(position=1), [{"kind": "flag_checkered"}], {}, now=1000)[0]
    assert "good shit" not in normal["text"].lower()
    assert "Ari" in normal["text"]
    assert normal.get("clip_id") is None
    profane = EngineerDirector(profile={"profanity": True})
    explicit = profane.process(frame(position=1), [{"kind": "flag_checkered"}], {}, now=1000)[0]
    assert "good shit" in explicit["text"].lower()
    assert explicit.get("clip_id") is None

