"""Offline queue policy tests for the WAV scheduler."""
import asyncio
import heapq
import io
import wave

import numpy as np
import pytest

from acc_engineer.audio import AudioRenderer, AudioScheduler
from acc_engineer.storage import Store


def test_priority_then_fifo_order(tmp_path):
    store = Store(tmp_path)
    try:
        scheduler = AudioScheduler(store)
        scheduler.enqueue("later.wav", priority=2)
        scheduler.enqueue("critical.wav", priority=0)
        scheduler.enqueue("same-priority.wav", priority=2)
        scheduler.enqueue("same-priority-next.wav", priority=2)
        ordered = [heapq.heappop(scheduler.queue) for _ in range(3)]
        assert [item.path for item in ordered] == ["critical.wav", "same-priority.wav", "same-priority-next.wav"]
    finally:
        store.close()


def test_expired_messages_are_suppressed_without_playback(tmp_path):
    store = Store(tmp_path)
    try:
        scheduler = AudioScheduler(store)
        scheduler.enqueue("expired.wav", priority=1, ttl=-1)

        async def exercise():
            worker = asyncio.create_task(scheduler.run())
            await asyncio.sleep(0.02)
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        asyncio.run(exercise())
        outcomes = [record["outcome"] for record in store.recent("playback")]
        assert "suppressed" in outcomes
        assert "started" not in outcomes
    finally:
        store.close()


def test_messages_from_old_sessions_are_suppressed(tmp_path):
    store = Store(tmp_path)
    try:
        scheduler = AudioScheduler(store)
        scheduler.session = "new-session"
        scheduler.enqueue("old-session.wav", priority=1, session="old-session")

        async def exercise():
            worker = asyncio.create_task(scheduler.run())
            await asyncio.sleep(0.02)
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        asyncio.run(exercise())
        assert store.recent("playback")[0]["outcome"] == "suppressed"
    finally:
        store.close()


def _wav(seconds=0.8, amplitude=6000, rate=24000):
    samples = np.rint(amplitude * np.sin(2 * np.pi * 440 * np.arange(round(seconds * rate)) / rate)).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    return output.getvalue()


def test_renderer_cache_is_deterministic_and_settings_sensitive(tmp_path):
    store = Store(tmp_path)
    try:
        store.save_settings({"speech_speed": 1.18, "radio_effect": "dry", "volume": 0.8})
        renderer = AudioRenderer(store)
        source = _wav()
        first = renderer.render_bytes(source)
        assert renderer.render_bytes(source) == first

        store.save_settings({"volume": 0.7})
        second = renderer.render_bytes(source)
        assert second != first
        second_path = renderer._cache_path(source, "neutral")[0]
        store.save_settings({"voice": "different-fixed-voice-id"})
        third = renderer.render_bytes(source)
        third_path = renderer._cache_path(source, "neutral")[0]
        assert third == second  # voice identity affects cache identity, not DSP output
        assert third_path != second_path
    finally:
        store.close()


def test_renderer_shortens_once_and_file_render_keeps_source_untouched(tmp_path):
    store = Store(tmp_path)
    try:
        store.save_settings({"speech_speed": 1.18, "radio_effect": "dry", "volume": 1.0})
        source = tmp_path / "audio" / "source.wav"
        original = _wav(seconds=1.0)
        source.write_bytes(original)
        output = AudioRenderer(store).render_file(source)
        assert source.read_bytes() == original
        with wave.open(str(output), "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
            peak = np.max(np.abs(np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.int32)))
        assert duration == pytest.approx(1.0 / 1.18, abs=0.03)
        assert peak <= 32767
    finally:
        store.close()


def test_renderer_handles_short_pcm16_clip_and_emotion_pace(tmp_path):
    store = Store(tmp_path)
    try:
        store.save_settings({"speech_speed": 1.0, "radio_effect": "gritty", "volume": 1.0})
        renderer = AudioRenderer(store)
        short = _wav(seconds=0.25, amplitude=28000)
        neutral = renderer.render_bytes(short, emotion="neutral")
        urgent = renderer.render_bytes(short, emotion="urgent")
        with wave.open(io.BytesIO(neutral), "rb") as wav:
            assert wav.getsampwidth() == 2
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.int32)
            assert wav.getnframes() > 0
        with wave.open(io.BytesIO(urgent), "rb") as wav:
            urgent_frames = wav.getnframes()
        assert samples.size > 0
        assert np.max(np.abs(samples)) <= 32113
        assert urgent_frames < len(samples)
    finally:
        store.close()
