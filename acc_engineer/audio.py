"""Single-worker WAV playback; P0 preempts without any network dependency."""
import asyncio
import hashlib
import heapq
import json
import os
import tempfile
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
    ("race_win", "session", 1, "Booyah! P1! That's a fantastic drive. Bring it home."),
    ("incident_follow_up", "conversation", 1, "Talk to me. How's the car feeling after that?"),
    ("strategy_call", "strategy", 2, "Let's stick to the plan. I'll keep you updated."),
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


def clip_emotion(clip_id):
    if clip_id in {"damage_severe", "damage_moderate", "incident_follow_up", "check_in"}:
        return "concerned"
    if clip_id in {"red", "yellow", "fuel_critical", "box_urgent"}:
        return "urgent"
    if clip_id in {"race_win", "personal_best"}:
        return "excited"
    if clip_id in {"penalty", "invalid_lap", "fuel_low", "tyre_hot", "brake_hot"}:
        return "firm"
    return "neutral"


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
    finished: asyncio.Event = field(compare=False, default_factory=asyncio.Event)
    outcome: str = field(compare=False, default="queued")


class AudioScheduler:
    def __init__(self, store, renderer=None):
        self.store = store
        self.renderer = renderer
        self.queue = []
        self.wake = asyncio.Event()
        self.current = None
        self.muted = False
        self.recording = False
        self.session = ""
        self.sequence = 0

    def enqueue(self, path, priority=2, ttl=15, session="", emotion="neutral", prepared=False):
        path = Path(path)
        if priority == 0:
            self.interrupt_noncritical()
        if self.renderer is not None and not prepared:
            try:
                path = self.renderer.render_file(path, emotion=emotion)
            except (OSError, ValueError, RuntimeError):
                self.store.add("playback", {"outcome": "invalid_clip", "path": str(path)})
                return False
        self.sequence += 1
        message = Message(priority, self.sequence, str(path), time.monotonic()+ttl, session)
        heapq.heappush(self.queue, message)
        self.wake.set()
        return message

    def interrupt_noncritical(self):
        for item in self.queue:
            if item.priority > 0:
                self.finish(item, "interrupted")
        self.queue[:] = [item for item in self.queue if item.priority == 0]
        heapq.heapify(self.queue)
        if self.current and self.current.priority > 0:
            self.stop_playback()
            self.store.add("playback", {"outcome": "interrupted", "path": self.current.path})
            self.finish(self.current, "interrupted")
            self.current = None

    @staticmethod
    def finish(message, outcome):
        message.outcome = outcome
        message.finished.set()

    def stop_playback(self):
        if os.name == "nt":
            import winsound
            winsound.PlaySound(None, 0)

    def clear(self):
        for message in self.queue:
            self.finish(message, "cancelled")
        if self.current:
            self.finish(self.current, "cancelled")
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
                    self.finish(message, "suppressed")
                    continue
                if self.recording:
                    if message.priority == 0:
                        # Caller cancels microphone before enqueuing critical warnings.
                        self.store.add("playback", {"outcome": "recording_blocked", "path": message.path})
                        self.finish(message, "recording_blocked")
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
                        self.finish(message, "completed")
                except (OSError, ValueError, EOFError, wave.Error):
                    self.current = None
                    self.store.add("playback", {"outcome": "failed", "path": message.path})
                    self.finish(message, "failed")
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


class AudioRenderer:
    """Render source speech once into a deterministic, settings-aware cache.

    Source clips are never modified. Cache identity includes the raw bytes,
    fixed voice identity and every rendering control, so previews and playback
    can share exactly the same processed WAV.
    """

    VERSION = "radio-render-v2-static"
    EMOTION_SPEED = {
        "neutral": 1.0,
        "excited": 1.035,
        "concerned": 0.985,
        "urgent": 1.055,
        "firm": 1.0,
        "calm": 0.965,
    }

    def __init__(self, store):
        self.store = store
        self.cache_dir = Path(store.root) / "audio" / "radio-cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _settings(self, emotion):
        settings = self.store.settings()
        emotion = str(emotion or "neutral").strip().lower()
        if emotion not in self.EMOTION_SPEED:
            emotion = "neutral"
        speed = float(settings.get("speech_speed", 1.18)) * self.EMOTION_SPEED[emotion]
        strength = str(settings.get("radio_effect", "gritty")).lower()
        if strength not in {"dry", "light", "gritty", "strong"}:
            strength = "gritty"
        volume = float(settings.get("volume", 0.8))
        if not 0 <= volume <= 2:
            raise ValueError("volume must be between 0 and 2")
        return settings, emotion, speed, strength, volume

    def _cache_path(self, data, emotion):
        settings, emotion, speed, strength, volume = self._settings(emotion)
        from .providers import voice_identity
        identity = voice_identity(settings)
        key = {
            "version": self.VERSION,
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "voice": identity,
            "emotion": emotion,
            "speed": speed,
            "strength": strength,
            "volume": volume,
        }
        digest = hashlib.sha256(json.dumps(key, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return self.cache_dir / f"{digest}.wav", speed, strength, volume

    def render_bytes(self, data, emotion="neutral"):
        data = bytes(data)
        cache_path, speed, strength, volume = self._cache_path(data, emotion)
        try:
            return cache_path.read_bytes()
        except FileNotFoundError:
            pass
        from .radiofx import process_radio
        rendered = apply_volume(process_radio(data, speed=speed, strength=strength), volume)
        # Atomic replacement prevents concurrent preview/playback renders from
        # exposing a partially written WAV. The source recording is untouched.
        fd, temporary = tempfile.mkstemp(prefix="radio-", suffix=".wav", dir=self.cache_dir)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(rendered)
            try:
                os.link(temporary, cache_path)
            except FileExistsError:
                pass
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return cache_path.read_bytes()

    def render_file(self, path, emotion="neutral") -> Path:
        """Render a source WAV and return its stable cached path."""
        source = Path(path)
        data = source.read_bytes()
        cache_path, _, _, _ = self._cache_path(data, emotion)
        if not cache_path.exists():
            self.render_bytes(data, emotion=emotion)
        return cache_path
