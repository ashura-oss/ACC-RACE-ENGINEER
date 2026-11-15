"""Single-worker WAV playback; P0 preempts without any network dependency."""
import asyncio
import heapq
import os
import time
from dataclasses import dataclass, field
from pathlib import Path


SCRIPTS = [
    ("red", "flags", 0, "Red flag. Follow race control instructions."),
    ("yellow", "flags", 0, "Yellow flag. Caution ahead."),
    ("green", "flags", 1, "Green flag. Track clear."),
    ("blue", "flags", 1, "Blue flag. Faster car approaching."),
    ("fuel_critical", "fuel", 0, "Fuel is critical. Box if you can."),
    ("fuel_low", "fuel", 1, "Fuel's looking tight. Start saving."),
    ("damage_severe", "damage", 0, "That's a big hit. You alright? Talk to me."),
    ("damage_moderate", "damage", 1, "We've picked up damage. How's the car feeling?"),
    ("damage_minor", "damage", 3, "We've picked up a little damage. Keep an eye on the handling."),
    ("personal_best", "laps", 3, "Personal best. Good lap. Keep that consistency."),
    ("lap_complete", "laps", 3, "Lap complete. Keep it tidy."),
    ("invalid_lap", "laps", 1, "That lap is invalid. Watch the track limits."),
    ("penalty", "penalties", 1, "We've been given a penalty. Check the display."),
    ("pit_open", "pits", 1, "The pit window is open."),
    ("pit_closed", "pits", 1, "The pit window is closed."),
    ("race_start", "session", 1, "We're racing. Settle into your rhythm."),
    ("session_change", "session", 3, "New session. Let's get ready."),
    ("race_finish", "session", 1, "Chequered flag. Bring it home."),
    ("tyre_hot", "tyres", 3, "Tyre temperatures are climbing. Let's manage them."),
    ("brake_hot", "brakes", 1, "Brakes are running hot. Keep an eye on them."),
    ("acknowledge", "conversation", 2, "Copy that."),
    ("box", "pits", 1, "Box this lap."),
    ("box_urgent", "pits", 0, "Box, box, box!"),
    ("check_in", "conversation", 1, "How's the car feeling?"),
    ("rain_change", "weather", 1, "Rain conditions have changed. Watch the grip."),
    ("gap_ahead_closing", "competitors", 3, "You're closing on the car ahead. Keep it clean."),
    ("gap_behind_gaining", "competitors", 1, "The car behind is closing. Keep your focus."),
]


def seed_library(store):
    for key, category, priority, text in SCRIPTS:
        if not store.clip(key):
            store.put_clip({"id": key, "category": category, "priority": priority,
                            "text": text, "versions": []})


@dataclass(order=True)
class Message:
    priority: int
    sequence: int
    path: str = field(compare=False)
    expires: float = field(compare=False)
    session: str = field(compare=False, default="")


class AudioScheduler:
    def __init__(self, store):
        self.store = store
        self.queue = []
        self.wake = asyncio.Event()
        self.current = None
        self.muted = False
        self.recording = False
        self.session = ""
        self.sequence = 0

    def enqueue(self, path, priority=2, ttl=15, session=""):
        self.sequence += 1
        message = Message(priority, self.sequence, str(path), time.monotonic()+ttl, session)
        heapq.heappush(self.queue, message)
        if priority == 0 and self.current and self.current.priority > 0:
            self.stop_playback()
            self.store.add("playback", {"outcome": "interrupted", "path": self.current.path})
            self.current = None
        self.wake.set()

    def stop_playback(self):
        if os.name == "nt":
            import winsound
            winsound.PlaySound(None, 0)

    def clear(self):
        self.queue.clear()
        self.stop_playback()
        self.current = None

    async def run(self):
        import wave
        try:
            while True:
                if not self.queue:
                    self.wake.clear()
                    await self.wake.wait()
                    continue
                message = heapq.heappop(self.queue)
                if (self.muted or message.expires < time.monotonic() or
                        (message.session and message.session != self.session)):
                    self.store.add("playback", {"outcome": "suppressed", "path": message.path})
                    continue
                if self.recording:
                    if message.priority == 0:
                        # Caller cancels microphone before enqueuing critical warnings.
                        self.store.add("playback", {"outcome": "recording_blocked", "path": message.path})
                        continue
                    heapq.heappush(self.queue, message)
                    await asyncio.sleep(0.05)
                    continue
                try:
                    with wave.open(message.path, "rb") as wav:
                        duration = wav.getnframes()/wav.getframerate()
                    if os.name != "nt":
                        raise OSError("WAV playback requires Windows")
                    import winsound
                    self.current = message
                    winsound.PlaySound(message.path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                    self.store.add("playback", {"outcome": "started", "path": message.path,
                                               "priority": message.priority})
                    until = time.monotonic()+duration
                    while self.current is message and time.monotonic() < until:
                        await asyncio.sleep(0.01)
                    if self.current is message:
                        self.current = None
                        self.store.add("playback", {"outcome": "completed", "path": message.path})
                except (OSError, ValueError, EOFError, wave.Error):
                    self.current = None
                    self.store.add("playback", {"outcome": "failed", "path": message.path})
        finally:
            self.clear()


def apply_volume(data, volume):
    """Scale PCM16 WAV; refuse formats we cannot safely process."""
    import io
    import wave
    import array
    with wave.open(io.BytesIO(data), "rb") as src:
        if src.getsampwidth() != 2:
            raise ValueError("Only PCM16 WAV is supported for volume processing")
        params = src.getparams()
        samples = array.array("h", src.readframes(src.getnframes()))
    samples = array.array("h", (int(sample*volume) for sample in samples))
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setparams(params)
        dst.writeframes(samples.tobytes())
    return out.getvalue()
