"""Offline acceptance workflows across the HTTP API and provider boundary.

Every persistent path is a pytest temporary directory. Provider traffic is
served by httpx.MockTransport and radio hardware/transcription are fakes.
"""
import asyncio
import json
import time
import wave
import io

import httpx
import pytest
from fastapi.testclient import TestClient
from PySide6.QtCore import QObject, Signal, QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication

from acc_engineer.app import Settings, create_app
from acc_engineer.desktop.pages import create_pages
from acc_engineer.providers import OpenRouter, ProviderError
from acc_engineer.storage import Store


def test_setup_api_inspects_and_creates_verified_copy_with_temp_profile(tmp_path):
    setup_root = tmp_path / "acc-setups"
    setup_root.mkdir()
    original = {"fuel": 20, "tyres": [25.0, 25.0, 25.0, 25.0]}
    (setup_root / "baseline.json").write_text(json.dumps(original), encoding="utf-8")
    profile = {"safe-test": {"validated": True, "parameters": {
        "fuel": {"path": "/fuel", "min": 1, "max": 100, "unit": "L", "integer": True},
        "front_left": {"path": "/tyres/0", "min": 24, "max": 28, "unit": "psi"},
    }}}

    with TestClient(create_app(tmp_path / "app-data")) as client:
        saved = client.put("/api/settings", json={
            "setup_root": str(setup_root), "setup_profiles": profile,
        })
        assert saved.status_code == 200
        inspected = client.post("/api/setups/inspect", json={"path": "baseline.json"})
        assert inspected.status_code == 200
        assert inspected.json()["data"] == original

        created = client.post("/api/setups/create", json={
            "source": "baseline.json", "name": "engineer-copy.json",
            "changes": {"fuel": 34, "front_left": 26.2}, "profile_name": "safe-test",
        })
        assert created.status_code == 200
        result = created.json()
        assert result["written"] is True and result["active_in_game"] is False
        assert result["changes"][0]["requested"] == 34
        assert (setup_root / "baseline.json").read_text(encoding="utf-8") == json.dumps(original)
        assert json.loads((setup_root / "engineer-copy.json").read_text(encoding="utf-8")) == {
            "fuel": 34, "tyres": [26.2, 25.0, 25.0, 25.0],
        }
        assert "engineer-copy.json" in client.get("/api/setups").json()


def test_provider_catalogue_fallback_and_tool_call_complete_offline(tmp_path, monkeypatch):
    store = Store(tmp_path)
    provider = OpenRouter(store)
    calls = []
    chat_turn = 0

    async def handler(request):
        nonlocal chat_turn
        calls.append(request.url.path)
        if request.method == "GET" and request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": [{
                "id": "openai/gpt-6-luna",
                "pricing": {"prompt": "0.00000001", "completion": "0.00000002"},
                "architecture": {"input_modalities": ["text"]},
            }]})
        if request.method == "GET" and request.url.path.endswith("/endpoints"):
            return httpx.Response(200, json={"data": {"endpoints": [{
                "pricing": {"prompt": "0.00000001", "completion": "0"},
            }]}})
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            chat_turn += 1
            if chat_turn == 1:
                return httpx.Response(200, json={"choices": [{"message": {
                    "tool_calls": [{"id": "call-1", "function": {
                        "name": "get_tyre_status", "arguments": "{}",
                    }}],
                }}]})
            payload = json.loads(request.content)
            tool_message = next(m for m in payload["messages"] if m.get("role") == "tool")
            assert json.loads(tool_message["content"])["status"] == "available"
            return httpx.Response(200, json={"choices": [{"message": {"content": "Tyre readings are available."}}]})
        return httpx.Response(404)

    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=provider.BASE)
    monkeypatch.setattr(provider, "key", lambda: "sk-or-offline-test")
    try:
        async def exercise():
            first = await provider.models()
            assert len(first) == 2  # text catalogue plus speech endpoint fallback
            context = {"telemetry": {"timestamp": time.time(), "tyre_pressure_psi": [26, 26, 26, 26]},
                       "telemetry_stale": False}
            answer = await provider.answer("How are the tyres?", context)
            assert answer == "Tyre readings are available."
        asyncio.run(exercise())
        assert calls.count("/api/v1/models") == 1
        assert len([path for path in calls if path.endswith("/endpoints")]) == 1
        assert len([path for path in calls if path.endswith("/chat/completions")]) == 2
        assert store.recent("tool")[0]["result"]["status"] == "available"
        assert len(store.recent("usage")) == 2
    finally:
        asyncio.run(provider.client.aclose())
        store.close()


def test_provider_budget_gate_blocks_dispatch_and_cached_critical_audio_still_queues(tmp_path, monkeypatch):
    store = Store(tmp_path)
    provider = OpenRouter(store)
    called = []

    async def handler(_request):
        called.append(True)
        return httpx.Response(200, json={})

    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=provider.BASE)
    provider.catalogue["test/model"] = {"pricing": {"prompt": "0.01", "completion": "0.01"}}
    monkeypatch.setattr(provider, "key", lambda: "sk-or-offline-test")
    store.save_settings({"model": "test/model", "request_limit_usd": 0.000001, "weekly_budget_usd": 1})
    try:
        with pytest.raises(ProviderError, match="Local expenditure limit"):
            asyncio.run(provider._post("test/model", "/chat/completions", {"prompt": "blocked"}, 20))
        assert called == []
        assert store.recent("usage") == []

        app = create_app(tmp_path / "runtime")
        runtime = app.state.runtime
        clip = runtime.store.clip("yellow")
        from acc_engineer.providers import voice_identity
        current = voice_identity(runtime.store.settings())
        (runtime.store.root / "audio" / "cached-yellow.wav").write_bytes(_pcm_wav())
        clip["versions"] = [{"id": "cached-yellow", "approved": True, "text": clip["text"], **current}]
        runtime.store.put_clip(clip)
        queued = []
        monkeypatch.setattr(runtime.audio.renderer, "render_file", lambda path, *args: path)
        monkeypatch.setattr(runtime.audio, "enqueue", lambda path, priority, session="", **kwargs: queued.append((path.name, priority)))
        asyncio.run(runtime.announce({"kind": "flag_yellow", "priority": 0}))
        assert queued == [("cached-yellow.wav", 0)]
        assert runtime.audio_readiness()["ready"] is False  # other critical scripts still need approval
        runtime.store.close()
        asyncio.run(runtime.provider.client.aclose())
    finally:
        asyncio.run(provider.client.aclose())
        store.close()


def test_radio_toggle_submit_and_cancel_are_fake_hardware_only(tmp_path):
    app = create_app(tmp_path)
    runtime = app.state.runtime
    calls = []

    class Recorder:
        def __init__(self): self.starts = 0; self.stops = 0; self.cancels = 0
        def start(self): self.starts += 1
        def stop(self): self.stops += 1; return _pcm_wav()
        def cancel(self): self.cancels += 1

    class Transcriber:
        def transcribe(self, audio):
            assert audio.startswith(b"RIFF")
            return "box this lap"

    runtime.recorder = Recorder()
    runtime.transcriber = Transcriber()
    runtime.running = True

    async def fake_chat(text): calls.append(text)
    runtime.chat = fake_chat
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/radio/toggle")
    cancel = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/radio/cancel")

    async def exercise():
        state = await endpoint()
        assert state["state"] == "recording" and runtime.audio.recording
        assert runtime.recorder.starts == 1
        state = await endpoint()  # second press submits; microphone bytes are only the fake WAV
        assert state["state"] == "processing"
        await asyncio.sleep(0.05)
        assert runtime.radio["state"] == "idle"
        assert calls == ["box this lap"]
        await endpoint()
        state = await cancel()
        assert state["state"] == "idle"
        assert runtime.recorder.cancels == 1

    try:
        asyncio.run(exercise())
    finally:
        runtime.store.close()
        asyncio.run(runtime.provider.client.aclose())


def test_native_settings_and_setup_forms_emit_api_shaped_payloads(qapp):
    # Kept headless: this checks form serialization only and never starts a backend.
    app = qapp

    class Notice:
        def emit(self, _message): pass

    class FakeAPI(QObject):
        status_changed = Signal(dict)
        def __init__(self):
            super().__init__()
            self.calls = []
            self.notify = Notice()
            self.status = {}
        def request(self, method, path, body=None, callback=None, **_kwargs):
            self.calls.append((method, path, body, callback))

    api = FakeAPI()
    pages = create_pages(api)
    try:
        settings = pages["AI settings"]
        settings._loaded({
            "model": "openai/gpt-6-luna", "speech_model": "fish-audio/drama-3",
            "voice": "bf322df2096a46f18c579d0baa36f41d", "weekly_budget_usd": 5,
            "request_limit_usd": .1, "speech_speed": 1, "volume": .8,
            "commentary_frequency": "normal", "recording_timeout_s": 30,
            "stale_after_s": 2, "broadcast_port": 9000, "broadcast_enabled": False,
            "setup_root": "", "thresholds": {}, "setup_profiles": {}, "broadcast_password": "",
        })
        settings.save()
        method, path, body, _ = api.calls[-1]
        assert (method, path) == ("PUT", "/api/settings")
        Settings.model_validate(body)

        controls = pages["Controller & keybinds"]
        controls._settings_loaded(body)
        controls.save()
        method, path, body, _ = api.calls[-1]
        assert (method, path) == ("PUT", "/api/settings")
        Settings.model_validate(body)

        setup = pages["Setup manager"]
        setup.files.addItem("baseline.json")
        setup.new_name.setText("copy.json")
        setup.profile.addItem("safe-test")
        setup.changes.setPlainText('{"fuel": 30}')
        setup.create()
        method, path, body, _ = api.calls[-1]
        assert (method, path) == ("POST", "/api/setups/create")
        assert body == {"source": "baseline.json", "name": "copy.json",
                        "changes": {"fuel": 30}, "profile_name": "safe-test"}
    finally:
        for page in pages.values():
            page.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            page.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        pages.clear()


def _pcm_wav():
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x01\x00" * 16)
    return out.getvalue()
