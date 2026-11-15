"""Offline speech conversion and radio preparation regression tests."""
import asyncio
import io
import wave

import pytest

from acc_engineer.providers import OpenRouter, ProviderError, speech_to_wav
from acc_engineer.storage import Store
from fastapi.testclient import TestClient

from acc_engineer.app import create_app
from acc_engineer import controller as controller_module


@pytest.mark.parametrize("rate,channels", [(44100, 1), (24000, 2)])
def test_pcm_is_wrapped_with_provider_rate_and_channel_metadata(rate, channels):
    frames = b"\x00\x00" * channels * 12
    wav_data = speech_to_wav(frames, f"audio/pcm; rate={rate}; channels={channels}")

    with wave.open(io.BytesIO(wav_data), "rb") as recording:
        assert recording.getframerate() == rate
        assert recording.getnchannels() == channels
        assert recording.getsampwidth() == 2
        assert recording.getnframes() == 12
        assert recording.readframes(12) == frames


@pytest.mark.parametrize("content_type,data", [
    ("audio/pcm", b"\x00\x00" * 8),
    ("audio/pcm; channels=1", b"\x00\x00" * 8),
    ("audio/pcm; rate=24000", b"\x00\x00" * 8),
    ("audio/wav", b"not a recording"),
    ("audio/pcm; rate=24000; channels=2", b"\x00\x00" * 3),
])
def test_missing_or_malformed_audio_metadata_is_rejected(content_type, data):
    with pytest.raises(ProviderError):
        speech_to_wav(data, content_type)


def test_real_wav_is_preserved():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(32000)
        recording.writeframes(b"\x01\x00" * 10)
    wav_data = buffer.getvalue()
    assert speech_to_wav(wav_data, "audio/wav") == wav_data


PINNED_VOICE = "bf322df2096a46f18c579d0baa36f41d"


@pytest.mark.parametrize("voice,expected", [("", PINNED_VOICE), ("engineer-voice-7", "engineer-voice-7")])
def test_speech_request_uses_pcm_and_pinned_or_explicit_voice(tmp_path, voice, expected):
    store = Store(tmp_path)
    provider = OpenRouter(store)
    store.save_settings({"voice": voice})
    sent = {}

    async def fake_post(model, endpoint, payload, **kwargs):
        sent.update(model=model, endpoint=endpoint, payload=payload)

        class Response:
            content = b"\x00\x00" * 4
            headers = {"content-type": "audio/pcm; rate=24000; channels=1"}

        return Response()

    provider._post = fake_post
    try:
        wav_data = asyncio.run(provider.speech("Test radio call."))
        assert sent["endpoint"] == "/audio/speech"
        assert sent["payload"]["response_format"] == "pcm"
        assert sent["payload"]["voice"] == expected
        with wave.open(io.BytesIO(wav_data), "rb") as recording:
            assert recording.getframerate() == 24000
    finally:
        asyncio.run(provider.client.aclose())
        store.close()


def test_repeated_speech_texts_always_send_same_resolved_voice(tmp_path):
    store = Store(tmp_path)
    provider = OpenRouter(store)
    sent = []

    async def fake_post(model, endpoint, payload, **kwargs):
        sent.append(payload.copy())

        class Response:
            content = b"\x00\x00" * 4
            headers = {"content-type": "audio/pcm; rate=24000; channels=1"}

        return Response()

    provider._post = fake_post
    try:
        asyncio.run(provider.speech("First call."))
        asyncio.run(provider.speech("Different words, same engineer."))
        assert [payload["voice"] for payload in sent] == [PINNED_VOICE, PINNED_VOICE]
    finally:
        asyncio.run(provider.client.aclose())
        store.close()


def test_provider_rejection_of_pinned_voice_is_not_retried_with_fallback(tmp_path):
    store = Store(tmp_path)
    provider = OpenRouter(store)
    attempts = []

    async def reject(model, endpoint, payload, **kwargs):
        attempts.append(payload.copy())
        raise ProviderError("OpenRouter returned HTTP 400.")

    provider._post = reject
    try:
        with pytest.raises(ProviderError):
            asyncio.run(provider.speech("Pinned voice request."))
        assert len(attempts) == 1
        assert attempts[0]["voice"] == PINNED_VOICE
    finally:
        asyncio.run(provider.client.aclose())
        store.close()


def _wav_bytes(rate=24000, samples=8):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(rate)
        recording.writeframes(b"\x00\x00" * samples)
    return buffer.getvalue()


def test_voice_test_endpoint_returns_playable_local_wav(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        runtime = client.app.state.runtime
        async def fake_speech(_text):
            return _wav_bytes()

        monkeypatch.setattr(runtime.provider, "speech", fake_speech)

        response = client.post("/api/voice/test")

        assert response.status_code == 200
        result = response.json()
        assert result["text"] == "Radio check. Engineer online."
        audio = client.get(result["url"])
        assert audio.status_code == 200
        assert audio.headers["content-type"].startswith("audio/wav")
        with wave.open(io.BytesIO(audio.content), "rb") as recording:
            assert recording.getframerate() == 24000
            assert recording.getnchannels() == 1


def test_voice_test_endpoint_requires_stopped_engineer(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        runtime = client.app.state.runtime
        runtime.running = True
        response = client.post("/api/voice/test")
        assert response.status_code == 400


def test_radio_prepare_endpoint_uses_tmp_cache_and_reports_ready(tmp_path, monkeypatch):
    seen = {}

    class FakeTranscriber:
        def __init__(self, cache_dir=None):
            seen["cache_dir"] = cache_dir

        def prepare(self):
            return {"ready": True, "model": "test-model"}

        def status(self):
            return {"ready": True, "model": "test-model"}

    monkeypatch.setattr(controller_module, "LocalTranscriber", FakeTranscriber)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/radio/prepare")
        assert response.status_code == 200
        assert response.json() == {"ready": True, "model": "test-model"}
        assert seen["cache_dir"] == tmp_path / "stt-models"
        assert client.get("/api/status").json()["radio"]["stt_state"] == "ready"


def test_radio_prepare_endpoint_surfaces_safe_transcription_category(tmp_path, monkeypatch):
    class FailingTranscriber:
        def __init__(self, cache_dir=None):
            pass

        def prepare(self):
            raise controller_module.TranscriptionError("unavailable", "Local recognition unavailable.")

        def status(self):
            raise AssertionError("status should not be requested after prepare failure")

    monkeypatch.setattr(controller_module, "LocalTranscriber", FailingTranscriber)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/radio/prepare")
        assert response.status_code == 400
        assert response.json()["detail"] == "Local recognition unavailable."
        status = client.get("/api/status").json()
        assert status["radio"]["stt_state"] == "error"
        assert client.app.state.runtime.errors[-1]["message"] == "Local recognition unavailable."
