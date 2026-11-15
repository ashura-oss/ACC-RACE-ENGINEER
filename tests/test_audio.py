"""Offline queue policy tests for the WAV scheduler."""
import asyncio
import heapq

from acc_engineer.audio import AudioScheduler
from acc_engineer.storage import Store


def test_priority_then_fifo_order(tmp_path):
    store = Store(tmp_path)
    try:
        scheduler = AudioScheduler(store)
        scheduler.enqueue("later.wav", priority=2)
        scheduler.enqueue("critical.wav", priority=0)
        scheduler.enqueue("same-priority.wav", priority=2)
        ordered = [heapq.heappop(scheduler.queue) for _ in range(3)]
        assert [item.path for item in ordered] == ["critical.wav", "later.wav", "same-priority.wav"]
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
