"""Voice identity regressions for speech generation and approved radio clips."""
import asyncio
import io
import wave

import pytest

from acc_engineer.app import create_app
from acc_engineer.providers import voice_identity


def _wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(24000)
        recording.writeframes(b"\x00\x00" * 8)
    return buffer.getvalue()


def _endpoint(app, path):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


def test_blank_voice_resolves_to_pinned_fish_voice():
    identity = voice_identity({"voice": "", "speech_model": "fish-audio/drama-3"})
    assert identity == {
        "voice": "bf322df2096a46f18c579d0baa36f41d",
        "speech_model": "fish-audio/drama-3",
    }


def test_missing_current_voice_critical_recordings_are_reported(tmp_path):
    app = create_app(tmp_path)
    runtime = app.state.runtime
    readiness = runtime.audio_readiness()
    assert readiness["ready"] is False
    assert {"yellow", "red", "fuel_critical", "damage_severe"}.issubset(readiness["missing_critical"])
    runtime.store.close()
    asyncio.run(runtime.provider.client.aclose())


def test_only_approved_current_voice_files_satisfy_critical_readiness(tmp_path):
    app = create_app(tmp_path)
    runtime = app.state.runtime
    current = voice_identity(runtime.store.settings())
    for clip in runtime.store.clips():
        if clip["priority"] != 0:
            continue
        recording_id = "ready-" + clip["id"]
        (runtime.store.root / "audio" / (recording_id + ".wav")).write_bytes(_wav_bytes())
        clip["versions"] = [{"id": recording_id, "approved": True, "text": clip["text"], **current}]
        runtime.store.put_clip(clip)
    assert runtime.audio_readiness()["ready"] is True
    runtime.store.save_settings({"voice": "a-new-speaker"})
    assert runtime.audio_readiness()["ready"] is False
    runtime.store.close()
    asyncio.run(runtime.provider.client.aclose())


def test_announce_plays_only_approved_clip_matching_current_voice_and_model(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    runtime = app.state.runtime
    current = voice_identity(runtime.store.settings())
    clip = runtime.store.clip("yellow")
    clip["versions"] = [
        {"id": "wrong", "approved": True, "text": clip["text"], "voice": "another-voice",
         "speech_model": current["speech_model"]},
        {"id": "current", "approved": True, "text": clip["text"], **current},
    ]
    runtime.store.put_clip(clip)
    queued = []
    monkeypatch.setattr(runtime.audio, "enqueue", lambda path, priority, session="": queued.append(path.name))

    asyncio.run(runtime.announce({"kind": "flag_yellow", "priority": 0}))

    assert queued == ["current.wav"]
    runtime.store.close()
    asyncio.run(runtime.provider.client.aclose())


def test_old_default_voice_recording_cannot_be_approved(tmp_path):
    app = create_app(tmp_path)
    runtime = app.state.runtime
    clip = runtime.store.clip("yellow")
    clip["versions"] = [{"id": "legacy", "approved": False, "text": clip["text"],
                         "voice": "", "speech_model": runtime.store.settings()["speech_model"]}]
    runtime.store.put_clip(clip)
    approve = _endpoint(app, "/api/library/{clip_id}/approve")

    with pytest.raises(ValueError):
        asyncio.run(approve("yellow", {"version_id": "legacy", "approved": True}))

    runtime.store.close()
    asyncio.run(runtime.provider.client.aclose())


def test_voice_or_model_change_during_generation_makes_version_unapprovable(tmp_path):
    app = create_app(tmp_path)
    runtime = app.state.runtime
    entered, release = asyncio.Event(), asyncio.Event()

    async def controlled_speech(_text):
        entered.set()
        await release.wait()
        return _wav_bytes()

    runtime.provider.speech = controlled_speech
    generate = _endpoint(app, "/api/library/{clip_id}/generate")
    approve = _endpoint(app, "/api/library/{clip_id}/approve")
    settings = runtime.store.settings()

    async def exercise():
        task = asyncio.create_task(generate("yellow"))
        await entered.wait()
        runtime.store.save_settings({"voice": "changed-voice"})
        release.set()
        result = await task
        version = result["versions"][-1]
        assert version["voice"] == voice_identity(settings)["voice"]
        assert version["speech_model"] == settings["speech_model"]
        with pytest.raises(ValueError):
            await approve("yellow", {"version_id": version["id"], "approved": True})

    asyncio.run(exercise())
    runtime.store.close()
    asyncio.run(runtime.provider.client.aclose())


def test_script_change_during_generation_keeps_recording_unapprovable(tmp_path):
    app = create_app(tmp_path)
    runtime = app.state.runtime
    entered, release = asyncio.Event(), asyncio.Event()

    async def controlled_speech(_text):
        entered.set()
        await release.wait()
        return _wav_bytes()

    runtime.provider.speech = controlled_speech
    generate = _endpoint(app, "/api/library/{clip_id}/generate")
    edit = _endpoint(app, "/api/library/{clip_id}")
    approve = _endpoint(app, "/api/library/{clip_id}/approve")

    async def exercise():
        task = asyncio.create_task(generate("yellow"))
        await entered.wait()
        await edit("yellow", {"text": "Revised yellow flag script."})
        release.set()
        result = await task
        version = result["versions"][-1]
        assert version["text"] != result["text"]
        with pytest.raises(ValueError):
            await approve("yellow", {"version_id": version["id"], "approved": True})

    asyncio.run(exercise())
    runtime.store.close()
    asyncio.run(runtime.provider.client.aclose())
