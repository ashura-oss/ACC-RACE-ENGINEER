"""HTTP boundary and runtime lifecycle tests; all state stays in tmp_path."""
from fastapi.testclient import TestClient

from acc_engineer.app import create_app
from acc_engineer import controller as controller_module


def test_loopback_host_and_origin_are_enforced(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/status", headers={"host": "example.com"}).status_code == 403
        assert client.get("/api/status", headers={"origin": "https://example.com"}).status_code == 403
        assert client.get("/api/status", headers={"host": "localhost:8000", "origin": "http://localhost:8000"}).status_code == 200


def test_settings_validation_and_secret_redaction(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        invalid = client.put("/api/settings", json={"volume": 5})
        assert invalid.status_code == 422
        assert client.put("/api/settings", json={"model": "invalid model name"}).status_code == 422
        saved = client.put("/api/settings", json={"volume": 0.25})
        assert saved.status_code == 200
        assert saved.json()["volume"] == 0.25

        runtime = client.app.state.runtime
        monkeypatch.setattr(runtime.provider, "key", lambda: "sk-or-test-secret")
        status = client.get("/api/status").json()
        settings = client.get("/api/settings").json()
        assert status["provider"]["key_configured"] is True
        assert "sk-or-test-secret" not in str(status)
        assert "sk-or-test-secret" not in str(settings)
        assert settings["broadcast_password"] == ""


def test_replay_is_isolated_from_live_runtime(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        runtime = client.app.state.runtime
        runtime.telemetry = {"session_id": "live-session", "speed_kmh": 120, "timestamp": 1}
        runtime.source = "acc"
        runtime.audio.session = "live-session"
        runtime.engine.last = {"sentinel": True}

        response = client.post("/api/debug/replay", json={"frames": [{"speed_kmh": 0, "completed_laps": 0}]})
        assert response.status_code == 200
        assert response.json()["source"] == "replay"
        assert runtime.telemetry["session_id"] == "live-session"
        assert runtime.source == "acc"
        assert runtime.audio.session == "live-session"
        assert runtime.engine.last == {"sentinel": True}
        assert client.post("/api/debug/replay", json={"frames": []}).status_code == 400


def test_start_stop_with_hardware_start_mocked(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        runtime = client.app.state.runtime
        starts = []

        async def fake_start_inputs():
            starts.append(True)

        monkeypatch.setattr(runtime, "start_inputs", fake_start_inputs)
        monkeypatch.setattr(runtime.reader, "read", lambda: None)
        started = client.post("/api/engineer/start")
        assert started.status_code == 200
        assert started.json()["running"] is True
        assert starts == [True]
        stopped = client.post("/api/engineer/stop")
        assert stopped.status_code == 200
        assert stopped.json()["running"] is False
        assert runtime.tasks == []


def test_controller_start_and_stop_are_awaited(tmp_path, monkeypatch):
    class FakeRecorder:
        def __init__(self, **kwargs):
            pass

    class FakeTranscriber:
        def __init__(self, **kwargs):
            pass

    class FakeController:
        def __init__(self, **kwargs):
            self.started = False
            self.stopped = False

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    monkeypatch.setattr(controller_module, "MicrophoneRecorder", FakeRecorder)
    monkeypatch.setattr(controller_module, "LocalTranscriber", FakeTranscriber)
    monkeypatch.setattr(controller_module, "ControllerService", FakeController)

    with TestClient(create_app(tmp_path)) as client:
        runtime = client.app.state.runtime
        import asyncio
        asyncio.run(runtime.start_inputs())
        controller = runtime.controller
        assert controller.started is True
        assert runtime.radio["controller"] == "polling"
        import asyncio
        asyncio.run(runtime.stop())
        assert controller.stopped is True


def test_critical_event_kinds_resolve_approved_clips(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path

    with TestClient(create_app(tmp_path)) as client:
        runtime = client.app.state.runtime
        queued = []
        monkeypatch.setattr(runtime.audio.renderer, "render_file", lambda path, *args: path)
        monkeypatch.setattr(runtime.audio, "enqueue", lambda path, priority, session="", **kwargs: queued.append((str(path), priority, session)))
        cases = [("flag_yellow", "yellow"), ("flag_red", "red"),
                 ("low_fuel", "fuel_critical"), ("severe_incident", "damage_severe")]
        async def exercise():
            for event_kind, clip_id in cases:
                clip = runtime.store.clip(clip_id)
                from acc_engineer.providers import voice_identity
                clip["versions"] = [{"id": "approved-" + clip_id, "approved": True,
                                     "text": clip["text"], **voice_identity(runtime.store.settings())}]
                runtime.store.put_clip(clip)
                await runtime.announce({"kind": event_kind, "priority": 0})
        asyncio.run(exercise())
        assert [Path(path).name for path, _, _ in queued] == ["approved-" + clip + ".wav" for _, clip in cases]
        assert all(priority == 0 for _, priority, _ in queued)


def test_canceled_transcription_does_not_start_chat(tmp_path):
    import asyncio
    import threading
    import time

    runtime = create_app(tmp_path).state.runtime
    entered = threading.Event()
    release = threading.Event()
    calls = []
    class BlockingTranscriber:
        def transcribe(self, audio):
            entered.set()
            release.wait(timeout=2)
            return "box this lap"
    runtime.transcriber = BlockingTranscriber()
    runtime.running = True
    runtime.radio_generation = 4
    async def chat(message):
        calls.append(message)
    runtime.chat = chat

    async def exercise():
        task = asyncio.create_task(runtime.transcribe_and_chat(b"recording", 4))
        assert await asyncio.to_thread(entered.wait, 1)
        await runtime.cancel_radio()
        release.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(exercise())
    assert calls == []
    runtime.store.close()
    asyncio.run(runtime.provider.client.aclose())


def test_old_recording_timeout_cannot_cancel_new_generation(tmp_path, monkeypatch):
    import asyncio

    runtime = create_app(tmp_path).state.runtime
    real_sleep = asyncio.sleep
    gate = asyncio.Event()
    canceled = []
    class Recorder:
        def cancel(self):
            canceled.append(True)
    runtime.recorder = Recorder()
    runtime.radio["state"] = "recording"
    runtime.radio_generation = 10

    async def controlled_sleep(_seconds):
        await gate.wait()
    monkeypatch.setattr(asyncio, "sleep", controlled_sleep)

    async def exercise():
        task = asyncio.create_task(runtime.recording_timeout(10))
        # Yield once through the saved implementation because asyncio.sleep is patched.
        await real_sleep(0)
        runtime.radio_generation = 11
        gate.set()
        await task

    asyncio.run(exercise())
    assert runtime.radio["state"] == "recording"
    assert canceled == []
    runtime.store.close()
    asyncio.run(runtime.provider.client.aclose())


def test_stop_cancels_direct_chat_response_task(tmp_path):
    import asyncio

    runtime = create_app(tmp_path).state.runtime
    runtime.running = True
    entered = asyncio.Event()
    canceled = []
    async def answer(*_args):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            canceled.append(True)
            raise
    async def speech(_text):
        raise AssertionError("speech should not run after stop")
    runtime.provider.answer = answer
    runtime.provider.speech = speech

    async def exercise():
        task = asyncio.create_task(runtime.chat("status"))
        await entered.wait()
        runtime.response_tasks.add(task)
        await runtime.stop()
        assert task.cancelled()

    asyncio.run(exercise())
    assert canceled == [True]
    runtime.store.close()
    asyncio.run(runtime.provider.client.aclose())


def test_stop_during_screenshot_capture_suppresses_provider_call(tmp_path, monkeypatch):
    import asyncio
    import threading
    import acc_engineer.vision as vision_module
    from acc_engineer.app import Chat

    app = create_app(tmp_path)
    runtime = app.state.runtime
    runtime.running = True
    entered, release = threading.Event(), threading.Event()
    provider_calls = []
    def capture():
        entered.set()
        release.wait(timeout=2)
        return "data:image/jpeg;base64,captured"
    async def answer(*args, **kwargs):
        provider_calls.append((args, kwargs))
        return "unexpected"
    monkeypatch.setattr(vision_module, "capture_data_url", capture)
    monkeypatch.setattr(runtime.provider, "answer", answer)
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/vision")

    async def exercise():
        task = asyncio.create_task(endpoint(Chat(message="What is on screen?")))
        assert await asyncio.to_thread(entered.wait, 1)
        runtime.running = False
        runtime.generation += 1
        release.set()
        result = await asyncio.wait_for(task, 1)
        assert result["suppressed"] is True

    asyncio.run(exercise())
    assert provider_calls == []
    runtime.store.close()
    asyncio.run(runtime.provider.client.aclose())
