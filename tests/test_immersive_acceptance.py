"""Offline integration checks for the race engineer's immersive runtime.

These exercise Runtime and OpenRouter orchestration with temporary SQLite
stores and deterministic fake provider/audio/input boundaries. No live ACC,
microphone, controller, or network service is contacted.
"""
import asyncio
import io
import json
import time
import wave

import httpx
from acc_engineer.app import create_app
from acc_engineer.providers import voice_identity
from acc_engineer.strategy import strategy_plan


def _runtime(path):
    return create_app(path).state.runtime


def _frame(**changes):
    return {
        "timestamp": time.time(), "session_id": "test-session", "status": "live",
        "stale": False, "physics_valid": True, "session": "race", "phase": "started",
        "race_status": "started", "track": "spa", "car_model": "gt3", "position": 5,
        "completed_laps": 4, "fuel_l": 42.0, "fuel_per_lap_l": 2.8,
        "session_time_left_s": 1200.0, "speed_kmh": 180.0,
        "damage": [0.0, 0.0, 0.0, 0.0], "flag": "none", "lap_valid": True,
    } | changes


def _wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(b"\0\0" * 32)
    return output.getvalue()


def _approved_clip(runtime, clip_id, file_id):
    clip = runtime.store.clip(clip_id)
    identity = voice_identity(runtime.store.settings())
    (runtime.store.root / "audio" / f"{file_id}.wav").write_bytes(_wav())
    clip["versions"] = [{"id": file_id, "approved": True, "text": clip["text"], **identity}]
    runtime.store.put_clip(clip)
    return clip["text"]


def _close_runtime(runtime):
    async def close_async():
        tasks = list(runtime.conversations)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.provider.client.aclose()
    asyncio.run(close_async())
    runtime.store.close()


def test_fresh_severe_impact_uses_cached_critical_voice_then_one_followup_and_ack(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path / "runtime")
    runtime.running = True
    runtime.source = "acc"
    _approved_clip(runtime, "damage_severe", "approved-severe")
    queued = []
    speech_calls = []

    def enqueue(path, priority=2, ttl=15, session="", emotion="neutral", prepared=False):
        queued.append((path.name, priority, emotion, prepared))

    async def speech(text, emotion="neutral"):
        speech_calls.append((text, emotion))
        return _wav()

    monkeypatch.setattr(runtime.audio, "enqueue", enqueue)
    monkeypatch.setattr(runtime.provider, "speech", speech)

    async def exercise():
        await runtime.ingest(_frame())
        impact = _frame(timestamp=time.time(), speed_kmh=40.0, damage=[13.0, 0.0, 0.0, 0.0])
        await runtime.ingest(impact)

        assert runtime.last_call["kind"] == "severe_incident"
        assert runtime.last_call["emotion"] == "concerned"
        assert len(queued) == 1
        assert queued[0][1:] == (0, "concerned", True)
        assert speech_calls == []  # Approved P0 audio did not call the speech provider.
        assert runtime.director.pending_concern is not None

        # Advance only the director's incident age; retain fresh telemetry.
        runtime.director.pending_concern["at"] = time.time() - 13
        await runtime.ingest(_frame(timestamp=time.time(), speed_kmh=145.0, damage=[13.0, 0.0, 0.0, 0.0]))
        tasks = list(runtime.conversations)
        if tasks:
            await asyncio.gather(*tasks)
        followups = [text for text, _emotion in speech_calls if "How's the car feeling" in text]
        assert followups == ["Talk to me. How's the car feeling after that?"]
        assert len([item for item in queued if item[1] == 0]) == 1
        assert runtime.director.pending_concern["followed_up"] is True

        async def answer(_message, _context):
            return "Copy, car feels fine."
        monkeypatch.setattr(runtime.provider, "answer", answer)
        await runtime.chat("The car feels fine, yes.")
        assert runtime.director.pending_concern is None
        assert runtime.director.snapshot()["mood"] == "focused"

    try:
        asyncio.run(exercise())
    finally:
        _close_runtime(runtime)


def test_finish_is_a_win_only_for_an_explicit_race_finish(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path / "runtime")
    runtime.source = "acc"
    _approved_clip(runtime, "race_finish", "approved-finish")
    _approved_clip(runtime, "race_win", "approved-win")
    queued = []
    monkeypatch.setattr(runtime.audio, "enqueue", lambda path, priority=2, **kwargs:
                        queued.append((path.name, priority, kwargs.get("prepared"))))

    async def exercise():
        # A checkered finish in a practice session may call the session complete,
        # but must not mark or announce a verified win.
        await runtime.ingest(_frame(session="practice", phase="started", race_status="started"))
        await runtime.ingest(_frame(timestamp=time.time(), session="practice", phase="finished",
                                    race_status="finished", flag="checkered", position=1))
        assert runtime.director.snapshot()["confirmed_win"] is False
        calls = runtime.store.recent("engineer_call", 20)
        assert not any(row.get("kind") == "win" for row in calls)

        # A new race session with an explicit finish and measured P1 is a win.
        await runtime.ingest(_frame(timestamp=time.time(), session_id="race-session", session="race",
                                    phase="started", race_status="started", completed_laps=9,
                                    position=1, flag="none"))
        await runtime.ingest(_frame(timestamp=time.time(), session_id="race-session", session="race",
                                    phase="finished", race_status="finished", completed_laps=10,
                                    position=1, flag="checkered"))
        assert runtime.director.snapshot()["confirmed_win"] is True
        calls = runtime.store.recent("engineer_call", 20)
        assert any(row.get("kind") == "win" and row.get("emotion") == "excited" for row in calls)
        assert any(priority == 1 and prepared is True for _name, priority, prepared in queued)

    try:
        asyncio.run(exercise())
    finally:
        _close_runtime(runtime)


def test_reopened_local_memory_is_manifest_only_until_tool_retrieval(tmp_path, monkeypatch):
    data_path = tmp_path / "runtime"
    first = _runtime(data_path)
    first.memory.update({"driver_name": "Ari", "driving_preferences": ["late braking"]})
    first.memory.remember_conversation("I prefer late braking", "Copy, I'll account for that.", "session-a")
    before_close = first.memory.manifest()
    _close_runtime(first)

    reopened = _runtime(data_path)
    context = reopened.context("What do you remember about my driving style?")
    serialized = json.dumps(context, ensure_ascii=False)
    assert reopened.memory.profile()["driving_preferences"] == ["late braking"]
    assert before_close["record_count"] >= 2
    assert "memory" in context and context["memory"]["available"] is True
    assert "conversation" not in context
    assert "I prefer late braking" not in serialized
    assert "Copy, I'll account for that" not in serialized
    assert "late braking" not in serialized  # The profile content is retrieved on demand.

    provider = reopened.provider
    provider.catalogue[reopened.store.settings()["model"]] = {
        "pricing": {"prompt": "0.00000001", "completion": "0.00000001"},
    }
    monkeypatch.setattr(provider, "key", lambda: "sk-or-offline-test")
    requests = []
    turn = 0

    async def handler(request):
        nonlocal turn
        payload = json.loads(request.content)
        requests.append(payload)
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            turn += 1
            if turn == 1:
                return httpx.Response(200, json={"choices": [{"message": {
                    "tool_calls": [{"id": "memory-call", "function": {
                        "name": "search_driver_memory",
                            "arguments": json.dumps({"query": "late braking"}),
                    }}],
                }}]})
            tool = next(message for message in payload["messages"] if message.get("role") == "tool")
            result = json.loads(tool["content"])
            assert result["status"] == "available"
            assert any("late braking" in row["excerpt"].casefold() for row in result["results"])
            return httpx.Response(200, json={"choices": [{"message": {"content": "You prefer late braking."}}]})
        return httpx.Response(404)

    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=provider.BASE)
    try:
        answer = asyncio.run(provider.answer("What is my preferred braking style?", context))
        assert answer == "You prefer late braking."
        initial = json.loads(requests[0]["messages"][1]["content"][0]["text"])
        assert "late braking" not in json.dumps(initial["race"]).casefold()
        assert len(requests) == 2
        assert any(message.get("role") == "tool" for message in requests[1]["messages"])
    finally:
        _close_runtime(reopened)


def test_opt_in_follow_up_waits_for_playback_and_generation_change_cancels_start(tmp_path):
    runtime = _runtime(tmp_path / "runtime")
    runtime.running = True
    runtime.recorder = type("Recorder", (), {"start": lambda self: setattr(self, "starts", self.starts + 1),
                                             "stop": lambda self: b"", "cancel": lambda self: None})()
    runtime.recorder.starts = 0
    runtime.store.save_settings({"follow_up_listening": True})
    runtime.audio.current = object()  # Simulates an utterance still playing.

    async def exercise():
        generation, radio_generation = runtime.generation, runtime.radio_generation
        from acc_engineer.audio import Message
        question = Message(2, 1, "question.wav", time.monotonic() + 10)
        cancelled = asyncio.create_task(runtime.follow_up_window(generation, radio_generation, question))
        await asyncio.sleep(.15)
        assert runtime.recorder.starts == 0
        runtime.generation += 1
        await asyncio.wait_for(cancelled, timeout=.5)
        assert runtime.recorder.starts == 0

        # Once output has completed, the opt-in window can open a single recording.
        runtime.audio.current = None
        runtime.audio.finish(question, "completed")
        generation, radio_generation = runtime.generation, runtime.radio_generation
        listening = asyncio.create_task(runtime.follow_up_window(generation, radio_generation, question))
        await asyncio.sleep(.4)
        assert runtime.recorder.starts == 1
        assert runtime.radio["state"] == "recording"
        await runtime.cancel_radio()
        listening.cancel()
        await asyncio.gather(listening, return_exceptions=True)

    try:
        asyncio.run(exercise())
    finally:
        _close_runtime(runtime)


def test_stale_frame_skips_director_and_strategy_is_unknown(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path / "runtime")
    runtime.source = "acc"
    processed = []
    # Preserve the real method for any accidental fresh-frame path.
    process = runtime.director.process
    monkeypatch.setattr(runtime.director, "process", lambda *args, **kwargs: (processed.append(args), process(*args, **kwargs))[1])
    stale = _frame(timestamp=time.time() - 20, stale=True, fuel_l=0.0, speed_kmh=40)

    async def exercise():
        await runtime.ingest(stale)
        plan = strategy_plan(runtime.context())
        assert plan["status"] == "unknown"
        assert plan["actions"] == []
        assert processed == []
        assert runtime.director.snapshot()["strategy_called"] is False

    try:
        asyncio.run(exercise())
    finally:
        _close_runtime(runtime)


def test_start_renders_approved_warning_clips_before_background_polling(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path / "runtime")
    _approved_clip(runtime, "yellow", "approved-yellow")
    renders = []

    def render(path, emotion="neutral"):
        renders.append((path.name, emotion))
        return path

    async def idle_forever():
        await asyncio.Future()

    async def no_hardware():
        return None

    monkeypatch.setattr(runtime.audio.renderer, "render_file", render)
    monkeypatch.setattr(runtime, "poll", idle_forever)
    monkeypatch.setattr(runtime.audio, "run", idle_forever)
    monkeypatch.setattr(runtime, "start_inputs", no_hardware)
    monkeypatch.setattr(runtime.reader, "close", lambda: None)

    async def exercise():
        await runtime.start()
        assert ("approved-yellow.wav", "urgent") in renders
        await runtime.stop()

    try:
        asyncio.run(exercise())
    finally:
        _close_runtime(runtime)


def test_suppressed_question_never_opens_follow_up_microphone(tmp_path):
    from acc_engineer.audio import Message
    runtime = _runtime(tmp_path / "runtime")
    runtime.running = True
    runtime.store.save_settings({"follow_up_listening": True})
    starts = []
    runtime.recorder = type("Recorder", (), {"start": lambda self: starts.append(True)})()
    async def exercise():
        for outcome in ("failed", "suppressed", "interrupted", "cancelled"):
            message = Message(2, 1, "question.wav", time.monotonic() + 10)
            runtime.audio.finish(message, outcome)
            await runtime.follow_up_window(runtime.generation, runtime.radio_generation, message)
        assert starts == []
    try:
        asyncio.run(exercise())
    finally:
        _close_runtime(runtime)


def test_missing_critical_clip_still_interrupts_recording_and_cloud_task(tmp_path):
    runtime = _runtime(tmp_path / "runtime")
    runtime.running = True
    runtime.radio["state"] = "recording"
    cancelled = []
    runtime.recorder = type("Recorder", (), {"cancel": lambda self: cancelled.append(True)})()
    async def exercise():
        cloud = asyncio.create_task(asyncio.sleep(60))
        runtime.response_tasks.add(cloud)
        await runtime.announce({"kind": "severe_incident", "priority": 0, "text": "Big hit. You okay?"})
        await asyncio.gather(cloud, return_exceptions=True)
        assert cloud.cancelled()
        assert runtime.radio["state"] == "idle"
        assert cancelled == [True]
        assert runtime.store.recent("playback")[0]["outcome"] == "missing_approved_clip"
    try:
        asyncio.run(exercise())
    finally:
        _close_runtime(runtime)


def test_paused_frames_cancel_old_race_audio_once_then_allow_general_radio(tmp_path):
    runtime = _runtime(tmp_path / "runtime")
    runtime.source = "acc"
    async def exercise():
        await runtime.ingest(_frame())
        runtime.audio.enqueue("old-race-call.wav", 2, prepared=True)
        await runtime.ingest(_frame(status="paused"))
        assert runtime.audio.queue == []
        generation = runtime.generation
        response = runtime.audio.enqueue("new-general-answer.wav", 2, prepared=True)
        await runtime.ingest(_frame(status="paused"))
        assert runtime.generation == generation
        assert runtime.audio.queue == [response]
        assert response.outcome == "queued"
        assert runtime.context()["telemetry_stale"] is True
    try:
        asyncio.run(exercise())
    finally:
        _close_runtime(runtime)
