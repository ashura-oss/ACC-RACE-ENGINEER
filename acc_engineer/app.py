"""Loopback-only application orchestrator and dashboard API."""
import asyncio
import contextlib
import hashlib
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .audio import AudioScheduler, apply_volume, seed_library
from .engineering import EventEngine
from .providers import OpenRouter, ProviderError
from .setups import SetupManager
from .storage import DEFAULTS, Store
from .telemetry import AccSharedMemory, BroadcastingClient


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    model: str = Field(default=DEFAULTS["model"], min_length=3, max_length=120, pattern=r"^[a-zA-Z0-9_./:\-]+$")
    speech_model: str = Field(default=DEFAULTS["speech_model"], min_length=3, max_length=120, pattern=r"^[a-zA-Z0-9_./:\-]+$")
    voice: str = Field(default="", max_length=200)
    weekly_budget_usd: float = Field(default=5, ge=0, le=1000)
    request_limit_usd: float = Field(default=.1, ge=0, le=10)
    speech_speed: float = Field(default=1, ge=.5, le=2)
    volume: float = Field(default=.8, ge=0, le=1)
    commentary_frequency: str = Field(default="normal", pattern="^(off|low|normal|high)$")
    radio_button: str = Field(default="R3", pattern="^(R3|L3|A|B|X|Y|LB|RB|START|BACK)$")
    keyboard_binding: str = Field(default="<f9>", pattern=r"^<f([1-9]|1[0-9]|2[0-4])>$")
    follow_up_listening: bool = False
    recording_timeout_s: int = Field(default=30, ge=3, le=60)
    stale_after_s: float = Field(default=2, ge=.1, le=10)
    setup_root: str = Field(default="", max_length=500)
    setup_profiles: dict = Field(default_factory=dict)
    thresholds: dict = Field(default_factory=dict)
    broadcast_port: int = Field(default=9000, ge=1024, le=65535)
    broadcast_password: str = Field(default="", max_length=200)
    broadcast_enabled: bool = False

    @field_validator("thresholds")
    @classmethod
    def valid_thresholds(cls, value):
        EventEngine(value)
        return value


class Chat(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class Runtime:
    def __init__(self, root):
        self.store = Store(root)
        seed_library(self.store)
        self.provider = OpenRouter(self.store)
        self.audio = AudioScheduler(self.store)
        self.engine = EventEngine(self.store.settings()["thresholds"])
        self.reader = AccSharedMemory()
        self.broadcast = None
        self.running = False
        self.telemetry = None
        self.source = "disconnected"
        self.errors = []
        self.tasks = []
        self.conversations = set()
        self.chat_lock = asyncio.Lock()
        self.radio = {"state": "idle", "controller": "unavailable", "stt": "local faster-whisper (optional)"}
        self.controller = None
        self.recorder = None
        self.radio_lock = asyncio.Lock()
        self.generation = 0
        self.radio_generation = 0
        self.transcriber = None
        self.response_tasks = set()
        self.processing_task = None
        self.lifecycle_lock = asyncio.Lock()

    def error(self, message):
        # Only controlled messages, never exception representations or provider bodies.
        entry = {"timestamp": time.time(), "message": message}
        self.errors = (self.errors + [entry])[-20:]
        self.store.add("error", entry)

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.conversations.add(task)
        def finished(done):
            self.conversations.discard(done)
            if not done.cancelled() and done.exception():
                self.error("Background radio operation failed; check devices and provider configuration.")
        task.add_done_callback(finished)
        return task

    async def start(self):
        if self.running:
            return
        self.running = True
        self.generation += 1
        self.engine = EventEngine(self.store.settings()["thresholds"])
        self.tasks = [asyncio.create_task(self.poll()), asyncio.create_task(self.audio.run())]
        settings = self.store.settings()
        if settings["broadcast_enabled"]:
            try:
                self.broadcast = BroadcastingClient(port=settings["broadcast_port"], password=settings["broadcast_password"])
                await self.broadcast.start()
            except Exception:
                self.error("ACC Broadcasting connection failed. Check broadcasting.json.")
        await self.start_inputs()

    async def stop(self):
        self.running = False
        self.generation += 1
        await self.cancel_radio()
        if self.controller:
            with contextlib.suppress(Exception):
                await self.controller.stop()
            self.controller = None
        tasks = self.tasks + list(self.conversations) + list(self.response_tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.tasks = []
        self.audio.clear()
        self.reader.close()
        if self.broadcast:
            await self.broadcast.stop()
            self.broadcast = None
        self.source = "disconnected"

    async def start_inputs(self):
        # Optional hardware dependencies must not prevent offline telemetry/radio alerts.
        try:
            from .controller import ControllerService, MicrophoneRecorder, LocalTranscriber
            loop = asyncio.get_running_loop()
            self.recorder = MicrophoneRecorder(max_seconds=self.store.settings()["recording_timeout_s"])
            self.transcriber = LocalTranscriber()
            key = self.store.settings()["keyboard_binding"].lower().strip("<>")
            vk = 111 + int(key[1:]) if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24 else None
            self.controller = ControllerService(
                on_toggle=lambda: loop.call_soon_threadsafe(lambda: self.spawn(self.toggle_radio())),
                button=self.store.settings()["radio_button"],
                keyboard_vk=vk)
            await self.controller.start()
            self.radio["controller"] = "polling"
        except Exception:
            self.radio["controller"] = "unavailable"
            self.error("Radio hardware unavailable. Install radio extras and check input devices.")

    async def poll(self):
        while self.running:
            try:
                frame = await asyncio.to_thread(self.reader.read)
                if frame:
                    self.source = "acc"
                    await self.ingest(frame)
                elif self.source == "acc":
                    self.source = "disconnected"
            except Exception:
                self.source = "disconnected"
            await asyncio.sleep(.05)

    async def ingest(self, frame):
        old_session = (self.telemetry or {}).get("session_id")
        self.telemetry = frame
        self.audio.session = str(frame.get("session_id", ""))
        if old_session and old_session != frame.get("session_id"):
            self.audio.clear()
            self.generation += 1
        for event in self.engine.process(frame):
            event["session_id"] = frame.get("session_id")
            self.store.add("event", event)
            await self.announce(event)
        if self.engine.fuel_history and len(self.engine.fuel_history) != getattr(self, "persisted_samples", 0):
            self.store.add("fuel_sample", {"session_id": frame.get("session_id"),
                "fuel_l": self.engine.fuel_history[-1], "lap_s": self.engine.lap_history[-1]})
            self.persisted_samples = len(self.engine.fuel_history)
        # Persist completed laps rather than every 20 Hz sample.
        previous = getattr(self, "persisted_lap", None)
        lap = (frame.get("session_id"), frame.get("completed_laps"))
        if lap != previous:
            self.store.add("telemetry", frame)
            self.persisted_lap = lap

    async def announce(self, event):
        if event["priority"] == 3 and self.store.settings()["commentary_frequency"] == "off":
            return
        clip_id = {"flag_yellow": "yellow", "flag_green": "green", "flag_blue": "blue",
                   "flag_red": "red", "flag_checkered": "race_finish", "flag_chequered": "race_finish",
                   "low_fuel": "fuel_critical", "fuel_deficit": "fuel_low",
                   "severe_incident": "damage_severe", "damage": "damage_moderate",
                   "penalty_received": "penalty", "lap_invalidated": "invalid_lap",
                   "pit_window_opened": "pit_open", "session_started": "race_start", "session_finished": "race_finish",
                   "minor_damage": "damage_minor", "tyres_hot": "tyre_hot", "brakes_hot": "brake_hot"}.get(event["kind"], event["kind"])
        clip = self.store.clip(clip_id)
        if clip:
            version = next((v for v in reversed(clip["versions"]) if v["approved"]), None)
            if version:
                if event["priority"] == 0:
                    await self.cancel_radio()
                self.audio.enqueue(self.store.root / "audio" / (version["id"] + ".wav"),
                                   event["priority"], session=self.audio.session)
                return
        self.store.add("playback", {"outcome": "missing_approved_clip", "event": event["kind"]})

    def context(self, message=""):
        frame = self.telemetry or {}
        age = time.time()-frame.get("timestamp", 0)
        fresh = age <= self.store.settings()["stale_after_s"] and frame.get("status") == "live" and self.source == "acc"
        session = frame.get("session_id")
        return {"telemetry": frame if fresh else None, "telemetry_stale": not fresh,
                "source": self.source, "fuel_projection": self.engine.projection(frame) if fresh else {"status": "unknown"},
                "competitors": self.broadcast.snapshot() if self.broadcast and fresh else None,
                "events": [e for e in self.store.recent("event", 30) if e.get("session_id") == session][:8],
                "conversation": [e for e in self.store.recent("conversation", 30) if e.get("session_id") == session][:6]}

    async def chat(self, message):
        if not self.running:
            raise ValueError("Start the engineer before using the radio.")
        if self.chat_lock.locked():
            raise ValueError("A radio response is already in progress.")
        async with self.chat_lock:
            generation = self.generation
            context = self.context(message)
            started = time.perf_counter()
            text = await self.provider.answer(message, context)
            if generation != self.generation or not self.running:
                return {"text": "Response cancelled.", "suppressed": True}
            self.store.add("conversation", {"driver": message, "engineer": text,
                "session_id": self.audio.session})
            self.store.add("activity", {"request": message, "response": text, "context": context,
                "latency_ms": round((time.perf_counter()-started)*1000)})
            if generation != self.generation or not self.running:
                return {"text": text, "suppressed": True}
            try:
                data = await self.provider.speech(text)
                path = self.store.root / "audio" / ("reply-"+uuid.uuid4().hex+".wav")
                path.write_bytes(apply_volume(data, self.store.settings()["volume"]))
                if generation == self.generation and self.running and time.perf_counter()-started < 20:
                    self.audio.enqueue(path, 2, session=self.audio.session)
            except (ProviderError, ValueError):
                self.error("Response text is available; speech generation unavailable.")
            return {"text": text}

    async def toggle_radio(self):
        async with self.radio_lock:
            if not self.running:
                raise ValueError("Start the engineer first.")
            if not self.recorder:
                raise ValueError("Microphone unavailable. Install radio extras.")
            if self.radio["state"] == "processing":
                return self.radio
            if self.radio["state"] == "recording":
                audio = self.recorder.stop()
                self.audio.recording = False
                self.radio["state"] = "processing"
                self.processing_task = self.spawn(self.transcribe_and_chat(audio, self.radio_generation))
            else:
                self.audio.clear()
                try:
                    self.recorder.start()
                except Exception:
                    raise ValueError("Microphone could not start. Install radio extras and select a working Windows input device.") from None
                self.radio_generation += 1
                self.audio.recording = True
                self.radio["state"] = "recording"
                self.spawn(self.recording_timeout(self.radio_generation))
            return self.radio

    async def recording_timeout(self, generation):
        await asyncio.sleep(self.store.settings()["recording_timeout_s"])
        if self.radio["state"] == "recording" and generation == self.radio_generation:
            await self.cancel_radio()

    async def cancel_radio(self):
        self.radio_generation += 1
        self.generation += 1
        if self.processing_task and self.processing_task is not asyncio.current_task():
            self.processing_task.cancel()
            self.processing_task = None
        if self.recorder and self.radio["state"] == "recording":
            with contextlib.suppress(Exception):
                self.recorder.cancel()
        self.radio["state"] = "idle"
        self.audio.recording = False

    async def transcribe_and_chat(self, audio, generation):
        try:
            text = await asyncio.to_thread(self.transcriber.transcribe, audio)
            if text.strip() and generation == self.radio_generation and self.running:
                await self.chat(text)
        except Exception:
            self.error("Radio transcription or response failed. Check local STT and provider configuration.")
        finally:
            if generation == self.radio_generation:
                self.radio["state"] = "idle"


def create_app(data_dir=None):
    root = Path(data_dir or os.getenv("ACC_ENGINEER_DATA", Path(os.getenv("LOCALAPPDATA", ".")) / "ACC Race Engineer"))
    runtime = Runtime(root)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await runtime.stop()
        await runtime.provider.client.aclose()
        runtime.store.close()

    app = FastAPI(title="ACC AI Race Engineer", lifespan=lifespan)
    app.state.runtime = runtime

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0]
        origin = request.headers.get("origin")
        if host not in {"localhost", "127.0.0.1", "testserver"}:
            return JSONResponse({"detail": "Loopback host required"}, 403)
        if origin and origin not in {"http://localhost:8000", "http://127.0.0.1:8000"}:
            return JSONResponse({"detail": "Cross-origin requests forbidden"}, 403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, 400)

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        runtime.error(str(exc))
        return JSONResponse({"detail": str(exc)}, 503)

    @app.get("/api/status")
    async def status():
        frame = runtime.telemetry
        return {"running": runtime.running, "muted": runtime.audio.muted,
            "telemetry": frame, "telemetry_age_s": time.time()-frame["timestamp"] if frame else None,
            "source": runtime.source, "radio": runtime.radio, "provider": {
                "model": runtime.store.settings()["model"], "speech_model": runtime.store.settings()["speech_model"],
                "key_configured": bool(runtime.provider.key()), "configured": bool(runtime.provider.key())}, "spend_usd": runtime.store.spend(),
            "errors": runtime.errors, "broadcast": runtime.broadcast.snapshot() if runtime.broadcast else None,
            "fuel_projection": runtime.engine.projection(frame) if frame else {"status": "unknown"}}

    @app.post("/api/engineer/start")
    async def start():
        async with runtime.lifecycle_lock:
            await runtime.start()
        return await status()

    @app.post("/api/engineer/stop")
    async def stop():
        async with runtime.lifecycle_lock:
            await runtime.stop()
        return await status()

    @app.post("/api/engineer/mute")
    async def mute(body: dict):
        runtime.audio.muted = bool(body.get("muted", True))
        if runtime.audio.muted:
            runtime.audio.clear()
        return await status()

    @app.get("/api/settings")
    async def settings():
        return runtime.store.settings() | {"broadcast_password": ""}

    @app.put("/api/settings")
    async def save_settings(body: Settings):
        if runtime.running:
            raise ValueError("Stop the engineer before changing settings.")
        values = body.model_dump(exclude_unset=True)
        if values.get("follow_up_listening"):
            raise ValueError("Automatic follow-up listening is not enabled in this release; use R3.")
        if values.get("broadcast_password") == "":
            values.pop("broadcast_password")
        runtime.store.save_settings(values)
        return await settings()

    @app.post("/api/credentials")
    async def credentials(body: dict):
        key = body.get("api_key", "")
        if not isinstance(key, str) or not key.startswith("sk-or-") or len(key) > 300:
            raise ValueError("Provide an OpenRouter API key.")
        try:
            await asyncio.to_thread(runtime.provider.save_key, key)
        except Exception:
            raise ValueError("Windows credential storage unavailable; use OPENROUTER_API_KEY environment variable.") from None
        return {"saved": True}

    @app.get("/api/models")
    async def models():
        try:
            return {"data": await runtime.provider.models()}
        except Exception:
            raise ProviderError("Model catalogue unavailable.") from None

    @app.get("/api/events")
    async def events():
        return runtime.store.recent("event")

    @app.get("/api/activity")
    async def activity():
        rows = []
        for kind in ("activity", "usage_detail", "tool", "error"):
            rows.extend(record | {"kind": kind} for record in runtime.store.recent(kind, 50))
        return sorted(rows, key=lambda row: row["timestamp"], reverse=True)[:150]

    @app.get("/api/usage")
    async def usage():
        return {"reservations": runtime.store.recent("usage"), "provider_reports": runtime.store.recent("usage_detail")}

    @app.get("/api/playback")
    async def playback():
        return runtime.store.recent("playback")

    @app.get("/api/library")
    async def library():
        return runtime.store.clips()

    def require_clip(clip_id):
        clip = runtime.store.clip(clip_id)
        if not clip:
            raise HTTPException(404, "Clip not found")
        return clip

    @app.put("/api/library/{clip_id}")
    async def edit_clip(clip_id: str, body: dict):
        clip = require_clip(clip_id)
        text = body.get("text", "")
        if not isinstance(text, str) or not 1 <= len(text) <= 500:
            raise ValueError("Clip text must contain 1–500 characters.")
        clip["text"] = text
        # Old recordings keep their script and remain visible, but are no longer active.
        for version in clip["versions"]:
            version["approved"] = False
        runtime.store.put_clip(clip)
        return clip

    @app.post("/api/library/{clip_id}/generate")
    async def generate_clip(clip_id: str):
        clip = require_clip(clip_id)
        data = await runtime.provider.speech(clip["text"])
        version_id = uuid.uuid4().hex
        (runtime.store.root / "audio" / (version_id+".wav")).write_bytes(apply_volume(data, runtime.store.settings()["volume"]))
        latest = require_clip(clip_id)
        latest["versions"].append({"id": version_id, "approved": False, "text": clip["text"],
            "voice": runtime.store.settings()["voice"], "timestamp": time.time(), "url": "/api/audio/"+version_id})
        runtime.store.put_clip(latest)
        return latest

    @app.post("/api/library/{clip_id}/approve")
    async def approve(clip_id: str, body: dict):
        clip = require_clip(clip_id)
        version = next((v for v in clip["versions"] if v["id"] == body.get("version_id")), None)
        if not version:
            raise HTTPException(404, "Version not found")
        version["approved"] = bool(body.get("approved", True))
        if version["approved"] and version["text"] != clip["text"]:
            raise ValueError("Recording script differs from current script. Regenerate it.")
        runtime.store.put_clip(clip)
        return clip

    @app.delete("/api/library/{clip_id}/versions/{version_id}")
    async def delete_version(clip_id: str, version_id: str):
        clip = require_clip(clip_id)
        version = next((v for v in clip["versions"] if v["id"] == version_id), None)
        if not version:
            raise HTTPException(404, "Version not found")
        clip["versions"].remove(version)
        runtime.store.put_clip(clip)
        (runtime.store.root / "audio" / (version_id+".wav")).unlink(missing_ok=True)
        return clip

    @app.get("/api/audio/{version_id}")
    async def audio(version_id: str):
        if not version_id.isalnum() or len(version_id) > 50:
            raise HTTPException(404)
        path = runtime.store.root / "audio" / (version_id+".wav")
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="audio/wav")

    @app.post("/api/chat")
    async def chat(body: Chat):
        task = runtime.spawn(runtime.chat(body.message))
        runtime.response_tasks.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            return {"text": "Response cancelled.", "suppressed": True}
        finally:
            runtime.response_tasks.discard(task)

    @app.post("/api/vision")
    async def vision(body: Chat):
        if not runtime.running:
            raise ValueError("Start the engineer before requesting visual analysis.")
        from .vision import capture_data_url
        generation = runtime.generation
        image = await asyncio.to_thread(capture_data_url)
        if not runtime.running or generation != runtime.generation:
            return {"text": "Visual analysis cancelled.", "suppressed": True}
        if not image:
            raise ValueError("ACC must be the foreground game. Install vision extras and use supported display capture.")
        async def analyze():
            if not runtime.running or generation != runtime.generation:
                return {"text": "Visual analysis cancelled.", "suppressed": True}
            text = await runtime.provider.answer(body.message, runtime.context(), image=image)
            if generation != runtime.generation:
                return {"text": "Visual analysis cancelled.", "suppressed": True}
            runtime.store.add("activity", {"request": body.message, "response": text,
                "source": "explicit_acc_screenshot", "screenshot_retained": False})
            return {"text": text}
        task = runtime.spawn(analyze())
        try:
            return await task
        except asyncio.CancelledError:
            return {"text": "Visual analysis cancelled.", "suppressed": True}

    @app.get("/api/fuel/recommendation")
    async def fuel_recommendation(duration_s: float = 900, reserve_l: float = 1):
        from .engineering import fuel_projection
        if not 0 < duration_s <= 86400 or not 0 <= reserve_l <= 100:
            raise ValueError("Invalid race duration or fuel reserve")
        samples = runtime.store.recent("fuel_sample", 10)
        result = fuel_projection(0, [s["fuel_l"] for s in samples],
            [s["lap_s"] for s in samples], duration_s, reserve_l)
        return result | {"note": "Historical samples may be from different cars/tracks. Verify the baseline before racing."}

    @app.get("/api/devices")
    async def devices():
        try:
            import sounddevice
            return {"devices": [dict(d) for d in sounddevice.query_devices()],
                    "controller": runtime.controller.snapshot() if runtime.controller else None}
        except Exception:
            return {"devices": [], "error": "Install radio extras to inspect audio devices."}

    @app.post("/api/radio/toggle")
    async def toggle():
        return await runtime.toggle_radio()

    @app.post("/api/radio/cancel")
    async def cancel():
        await runtime.cancel_radio()
        return runtime.radio

    @app.post("/api/debug/event")
    async def debug_event(body: dict):
        kind = {"yellow": "yellow", "damage": "damage_severe", "fuel": "fuel_low", "lap": "personal_best"}.get(body.get("kind"))
        if not kind:
            raise ValueError("Unknown test event")
        clip = require_clip(kind)
        event = {"kind": kind, "priority": clip["priority"], "text": clip["text"],
                 "timestamp": time.time(), "source": "manual_test"}
        runtime.store.add("event", event)
        if runtime.running:
            await runtime.announce(event)
        return event

    @app.post("/api/debug/replay")
    async def replay(body: dict):
        if runtime.running:
            raise ValueError("Stop the live engineer before replaying telemetry.")
        frames = body.get("frames", [])
        if not isinstance(frames, list) or not 1 <= len(frames) <= 10000:
            raise ValueError("Replay must contain 1–10000 frames.")
        engine = EventEngine(runtime.store.settings()["thresholds"])
        output = []
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                raise ValueError("Every replay frame must be an object.")
            # Original timestamps remain in the response; processing clock is rebased.
            frame = frame | {"timestamp": time.time()+index*.000001, "status": "replay", "session_id": "replay"}
            output.extend(engine.process(frame))
        runtime.store.add("replay", {"frame_count": len(frames), "events": output})
        return {"source": "replay", "events": output, "fuel_projection": engine.projection(frames[-1])}

    def setup_manager():
        s = runtime.store.settings()
        return SetupManager(s["setup_root"], s["setup_profiles"])

    @app.get("/api/setups")
    async def list_setups():
        settings = runtime.store.settings()
        if not settings["setup_root"]:
            return []
        root = Path(settings["setup_root"])
        return [str(p.relative_to(root)) for p in root.rglob("*.json") if "backups" not in p.parts][:500]

    @app.post("/api/setups/inspect")
    async def inspect_setup(body: dict):
        return setup_manager().inspect(body.get("path", ""))

    @app.post("/api/setups/create")
    async def create_setup(body: dict):
        if runtime.running:
            frame = runtime.telemetry or {}
            if (runtime.source != "acc" or frame.get("status") != "live" or
                    time.time()-frame.get("timestamp", 0) > runtime.store.settings()["stale_after_s"] or
                    not isinstance(frame.get("speed_kmh"), (int, float)) or not 0 <= frame["speed_kmh"] < .1):
                raise ValueError("Stop the engineer or verify fresh stationary telemetry before editing setups.")
        return setup_manager().create(body.get("source", ""), body.get("name", ""),
                                      body.get("changes", {}), body.get("profile_name", body.get("profile")))

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    async def index():
        return FileResponse(static / "index.html")

    return app


app = create_app()
