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

from .audio import AudioRenderer, AudioScheduler, clip_emotion, seed_library
from .engineering import EventEngine
from .director import EngineerDirector
from .memory import DriverMemory
from .strategy import strategy_plan
from .providers import OpenRouter, ProviderError, voice_identity
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
    speech_speed: float = Field(default=1.18, ge=.5, le=2)
    radio_effect: str = Field(default="gritty", pattern="^(dry|light|gritty|strong)$")
    volume: float = Field(default=.8, ge=0, le=1)
    commentary_frequency: str = Field(default="normal", pattern="^(off|low|normal|high)$")
    radio_button: str = Field(default="R3", pattern="^(R3|L3|A|B|X|Y|LB|RB|START|BACK)$")
    keyboard_binding: str = Field(default="<f9>", pattern=r"^<f([1-9]|1[0-9]|2[0-4])>$")
    follow_up_listening: bool = False
    recording_timeout_s: int = Field(default=30, ge=3, le=30)
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
        if not self.store.settings()["voice"].strip():
            self.store.save_settings({"voice": voice_identity(self.store.settings())["voice"]})
        if self.store.settings()["recording_timeout_s"] > 30:
            self.store.save_settings({"recording_timeout_s": 30})
        seed_library(self.store)
        self.provider = OpenRouter(self.store)
        self.provider.context_supplier = self.context
        self.audio = AudioScheduler(self.store, AudioRenderer(self.store))
        self.engine = EventEngine(self.store.settings()["thresholds"])
        self.memory = DriverMemory(self.store)
        self.provider.memory_search = self.memory.search
        self.director = EngineerDirector(config={"frequency": self.store.settings()["commentary_frequency"]}, profile=self.memory.profile())
        self.last_call = None
        self.follow_up_task = None
        self.reader = AccSharedMemory()
        self.broadcast = None
        self.running = False
        self.live_available = False
        self.telemetry = None
        self.source = "disconnected"
        self.errors = []
        self.tasks = []
        self.conversations = set()
        self.chat_lock = asyncio.Lock()
        self.radio = {"state": "idle", "controller": "unavailable", "stt": "local faster-whisper (optional)",
                      "stt_state": "not_prepared", "stt_model": "base.en"}
        self.controller = None
        self.recorder = None
        self.radio_lock = asyncio.Lock()
        self.generation = 0
        self.radio_generation = 0
        self.transcriber = None
        self.response_tasks = set()
        self.proactive_tasks = set()
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
        missing = self.audio_readiness()["missing_critical"]
        if missing:
            self.error("Critical radio audio needs review for the selected voice: " + ", ".join(missing))
        self.generation += 1
        self.engine = EventEngine(self.store.settings()["thresholds"])
        self.director = EngineerDirector(config={"frequency": self.store.settings()["commentary_frequency"]}, profile=self.memory.profile())
        # Prepare processed warnings before polling: critical calls never wait on TTS.
        for clip in self.store.clips():
            identity = voice_identity(self.store.settings())
            version = next((v for v in reversed(clip["versions"]) if v.get("approved") and
                v.get("text") == clip["text"] and all(v.get(k) == x for k, x in identity.items())), None)
            if version:
                path = self.store.root / "audio" / (version["id"] + ".wav")
                if path.is_file():
                    try:
                        await asyncio.to_thread(self.audio.renderer.render_file, path, clip_emotion(clip["id"]))
                    except (OSError, ValueError, RuntimeError):
                        self.error("A cached radio recording could not be prepared. Regenerate that recording in the library.")
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
        self.save_session_summary()
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
            if self.transcriber is None:
                self.transcriber = LocalTranscriber(cache_dir=self.store.root / "stt-models")
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
                    self.live_available = False
                    self.generation += 1
                    self.audio.clear()
            except Exception:
                if self.source == "acc":
                    self.generation += 1
                    self.audio.clear()
                self.source = "disconnected"
                self.live_available = False
            await asyncio.sleep(.05)

    async def ingest(self, frame):
        old_session = (self.telemetry or {}).get("session_id")
        self.telemetry = frame
        self.audio.session = str(frame.get("session_id", ""))
        if old_session and old_session != frame.get("session_id"):
            self.save_session_summary()
            self.audio.clear()
            self.generation += 1
        if self.source == "acc" and (frame.get("status") != "live" or frame.get("stale") or
                                     frame.get("physics_valid") is False):
            # ACC replays/menus/paused frames are displayable history, never
            # live warning input. Isolated JSON replay uses its own EventEngine.
            if self.live_available:
                self.generation += 1
                self.audio.clear()
            self.live_available = False
            return
        self.live_available = True
        events = self.engine.process(frame)
        for event in events:
            event["session_id"] = frame.get("session_id")
            self.store.add("event", event)
        for intent in self.director.process(frame, events, self.engine.projection(frame)):
            await self.announce(intent)
        self.save_session_summary()
        if self.engine.fuel_history and len(self.engine.fuel_history) != getattr(self, "persisted_samples", 0):
            self.store.add("fuel_sample", {"session_id": frame.get("session_id"),
                "car_model": frame.get("car_model"), "track": frame.get("track"),
                "fuel_l": self.engine.fuel_history[-1], "lap_s": self.engine.lap_history[-1]})
            self.persisted_samples = len(self.engine.fuel_history)
        # Persist completed laps rather than every 20 Hz sample.
        previous = getattr(self, "persisted_lap", None)
        lap = (frame.get("session_id"), frame.get("completed_laps"))
        if lap != previous:
            self.store.add("telemetry", frame)
            self.persisted_lap = lap

    async def announce(self, event):
        was_running = self.running
        if event["priority"] == 3 and self.store.settings()["commentary_frequency"] == "off":
            return
        if event["priority"] == 0:
            await self.cancel_radio()
            for task in list(self.response_tasks):
                if task is not asyncio.current_task():
                    task.cancel()
            self.audio.interrupt_noncritical()
        clip_id = event.get("clip_id") or {"flag_yellow": "yellow", "flag_green": "green", "flag_blue": "blue",
                   "flag_red": "red", "flag_checkered": "race_finish", "flag_chequered": "race_finish",
                   "low_fuel": "fuel_critical", "fuel_deficit": "fuel_low",
                   "severe_incident": "damage_severe", "damage": "damage_moderate",
                   "penalty_received": "penalty", "lap_invalidated": "invalid_lap",
                   "pit_window_opened": "pit_open", "session_started": "race_start", "session_finished": "race_finish",
                   "minor_damage": "damage_minor", "tyres_hot": "tyre_hot", "brakes_hot": "brake_hot"}.get(event["kind"], event["kind"])
        if event["kind"] == "gap_behind_closing":
            clip_id = "gap_behind_gaining"
        clip = None if event.get("dynamic") else self.store.clip(clip_id)
        self.last_call = {"text": event.get("text", (clip or {}).get("text", "")), "emotion": event.get("emotion", "calm"),
            "kind": event["kind"], "timestamp": time.time()}
        self.store.add("engineer_call", self.last_call | {"evidence": event.get("evidence", event.get("source")),
            "session_id": self.audio.session})
        if clip:
            identity = voice_identity(self.store.settings())
            version = next((v for v in reversed(clip["versions"]) if v["approved"] and
                v.get("voice") == identity["voice"] and v.get("speech_model") == identity["speech_model"] and
                v.get("text") == clip["text"]), None)
            if version:
                generation, session = self.generation, self.audio.session
                path = self.store.root / "audio" / (version["id"] + ".wav")
                try:
                    rendered = await asyncio.to_thread(self.audio.renderer.render_file, path, event.get("emotion", "calm"))
                except (OSError, ValueError, RuntimeError):
                    self.store.add("playback", {"outcome": "invalid_clip", "event": event["kind"]})
                    return
                if was_running and (not self.running or generation != self.generation or session != self.audio.session):
                    return
                if time.time() - event.get("timestamp", time.time()) > min(2, event.get("ttl", 8)):
                    return
                message = self.audio.enqueue(rendered, event["priority"], session=self.audio.session,
                                   emotion=event.get("emotion", "calm"), prepared=True)
                self.schedule_follow_up(self.last_call["text"], message)
                return
        if event["priority"] > 0 and self.running and not self.audio.muted and not self.audio.recording:
            if not self.chat_lock.locked() and not self.response_tasks and len(self.audio.queue) < 2:
                task = self.spawn(self.speak_intent(event, self.generation))
                self.response_tasks.add(task)
                self.proactive_tasks.add(task)
                task.add_done_callback(self.response_tasks.discard)
                task.add_done_callback(self.proactive_tasks.discard)
            return
        self.store.add("playback", {"outcome": "missing_approved_clip", "event": event["kind"]})

    async def speak_intent(self, intent, generation):
        async with self.chat_lock:
            if generation != self.generation or not self.running:
                return
            try:
                data = await self.provider.speech(intent["text"], emotion=intent.get("emotion", "calm"))
                if generation != self.generation or not self.running or self.audio.recording:
                    return
                if time.time() - intent.get("timestamp", time.time()) > intent.get("ttl", 10):
                    return
                path = self.store.root / "audio" / ("call-" + uuid.uuid4().hex + ".wav")
                path.write_bytes(data)
                message = self.audio.enqueue(path, intent["priority"], ttl=intent.get("ttl", 10),
                    session=self.audio.session, emotion=intent.get("emotion", "calm"))
                self.schedule_follow_up(intent["text"], message)
            except (ProviderError, ValueError):
                self.error("Proactive call text is available; speech generation unavailable.")

    def save_session_summary(self):
        if self.telemetry:
            summary = self.director.session_summary()
            if (summary.get("verified_final") and
                    summary.get("session_id") != getattr(self, "saved_summary_session", None)):
                self.memory.record_session(summary)
                self.saved_summary_session = summary["session_id"]

    def audio_readiness(self):
        identity = voice_identity(self.store.settings())
        missing = []
        for clip in self.store.clips():
            if clip.get("priority") != 0:
                continue
            valid = any(version.get("approved") and version.get("text") == clip["text"] and
                all(version.get(key) == value for key, value in identity.items()) and
                (self.store.root / "audio" / (version["id"] + ".wav")).is_file()
                for version in clip["versions"])
            if not valid:
                missing.append(clip["id"])
        return {"ready": not missing, "missing_critical": missing, **identity}

    def context(self, message=""):
        frame = self.telemetry or {}
        age = time.time()-frame.get("timestamp", 0)
        fresh = (age <= self.store.settings()["stale_after_s"] and frame.get("status") == "live" and
                 self.source == "acc" and not frame.get("stale") and frame.get("physics_valid") is not False)
        session = frame.get("session_id")
        result = {"telemetry": frame if fresh else None, "telemetry_stale": not fresh,
                "source": self.source, "fuel_projection": self.engine.projection(frame) if fresh else {"status": "unknown"},
                "competitors": self.broadcast.snapshot() if self.broadcast and fresh else None,
                "events": [e for e in self.store.recent("event", 30) if e.get("session_id") == session][:8],
                "memory": self.memory.manifest(), "engineer_state": self.director.snapshot() | {"last_call": self.last_call}}
        result["strategy"] = strategy_plan(result)
        return result

    async def chat(self, message, allow_follow_up=True):
        if not self.running:
            raise ValueError("Start the engineer before using the radio.")
        if self.chat_lock.locked():
            if self.proactive_tasks:
                tasks = list(self.proactive_tasks)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                raise ValueError("A radio response is already in progress.")
        async with self.chat_lock:
            self.director.update_profile(self.memory.observe(message))
            self.director.note_driver(message)
            generation = self.generation
            context = self.context(message)
            started = time.perf_counter()
            text = await self.provider.answer(message, context)
            if generation != self.generation or not self.running:
                return {"text": "Response cancelled.", "suppressed": True}
            self.store.add("conversation", {"driver": message, "engineer": text,
                "session_id": self.audio.session})
            self.memory.remember_conversation(message, text, self.audio.session)
            self.store.add("activity", {"request": message, "response": text, "context": context,
                "latency_ms": round((time.perf_counter()-started)*1000)})
            if generation != self.generation or not self.running:
                return {"text": text, "suppressed": True}
            try:
                data = await self.provider.speech(text)
                path = self.store.root / "audio" / ("reply-"+uuid.uuid4().hex+".wav")
                path.write_bytes(data)
                if generation == self.generation and self.running and time.perf_counter()-started < 20:
                    message = self.audio.enqueue(path, 2, session=self.audio.session)
                    if allow_follow_up:
                        self.schedule_follow_up(text, message)
            except (ProviderError, ValueError):
                self.error("Response text is available; speech generation unavailable.")
            return {"text": text}

    def schedule_follow_up(self, text, message):
        if (not hasattr(message, "finished") or "?" not in text or not self.store.settings()["follow_up_listening"] or
                not self.recorder or (self.follow_up_task and not self.follow_up_task.done())):
            return
        self.follow_up_task = self.spawn(self.follow_up_window(self.generation, self.radio_generation, message))

    async def follow_up_window(self, generation, radio_generation, message):
        # Explicit opt-in only. Wait for output to finish so the mic cannot hear it.
        deadline = time.monotonic() + 30
        while not message.finished.is_set():
            if (time.monotonic() > deadline or not self.running or generation != self.generation or
                    radio_generation != self.radio_generation):
                return
            await asyncio.sleep(.1)
        if message.outcome != "completed":
            return
        while self.audio.current or self.audio.queue or self.radio["state"] == "processing":
            if (time.monotonic() > deadline or not self.running or generation != self.generation or
                    radio_generation != self.radio_generation):
                return
            await asyncio.sleep(.1)
        await asyncio.sleep(.25)
        if (not self.running or generation != self.generation or radio_generation != self.radio_generation or
                self.radio["state"] != "idle" or self.audio.muted or not self.store.settings()["follow_up_listening"]):
            return
        await self.toggle_radio()
        self.radio["follow_up"] = True
        listening_generation = self.radio_generation
        await asyncio.sleep(5)
        if self.radio["state"] == "recording" and listening_generation == self.radio_generation:
            await self.toggle_radio()

    async def toggle_radio(self):
        async with self.radio_lock:
            if not self.running:
                raise ValueError("Start the engineer first.")
            if not self.recorder:
                raise ValueError("Microphone unavailable. Install radio extras.")
            if self.radio.get("stt_state") == "preparing":
                raise ValueError("Speech recognition is preparing. Wait for the ready status before recording.")
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
        self.radio.pop("follow_up", None)
        self.audio.recording = False

    async def transcribe_and_chat(self, audio, generation):
        from .controller import TranscriptionError
        is_follow_up = self.radio.pop("follow_up", False)
        try:
            try:
                text = await asyncio.to_thread(self.transcriber.transcribe, audio)
                if hasattr(self.transcriber, "status"):
                    ready = self.transcriber.status().get("ready", False)
                    self.radio["stt_state"] = "ready" if ready else "not_prepared"
                    if ready:
                        self.radio.pop("stt_error_category", None)
            except TranscriptionError as exc:
                self.radio["stt_state"] = "error"
                self.radio["stt_error_category"] = exc.category
                self.radio["last_error"] = str(exc)
                self.error(str(exc))
                return
            except Exception:
                self.radio["stt_state"] = "error"
                self.error("Local speech recognition failed. Prepare speech recognition in AI settings, then retry.")
                return
            if generation != self.radio_generation or not self.running:
                return
            if not text.strip():
                if is_follow_up:
                    return
                self.radio["last_error"] = "No speech detected. Check the Windows default microphone and try again."
                self.error(self.radio["last_error"])
                return
            try:
                if is_follow_up:
                    await self.chat(text, allow_follow_up=False)
                else:
                    await self.chat(text)
            except ProviderError as exc:
                self.error("AI response failed: " + str(exc))
            except ValueError as exc:
                self.error(str(exc))
        finally:
            if generation == self.radio_generation:
                self.radio["state"] = "idle"


def create_app(data_dir=None, api_token=None):
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
        if api_token:
            import hmac
            supplied = request.headers.get("x-acc-token", "")
            if request.url.path.startswith("/api/audio/"):
                supplied = supplied or request.query_params.get("access_token", "")
            if request.headers.get("origin") or not hmac.compare_digest(supplied, api_token):
                return JSONResponse({"detail": "Native app authentication required"}, 403)
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
            "source": runtime.source, "radio": runtime.radio, "audio_readiness": runtime.audio_readiness(), "provider": {
                "model": runtime.store.settings()["model"], "speech_model": runtime.store.settings()["speech_model"],
                "key_configured": bool(runtime.provider.key()), "configured": bool(runtime.provider.key())}, "spend_usd": runtime.store.spend(),
            "errors": runtime.errors, "broadcast": runtime.broadcast.snapshot() if runtime.broadcast else None,
            "engineer": runtime.director.snapshot() | {"last_call": runtime.last_call},
            "strategy": strategy_plan(runtime.context()),
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
        if "voice" in values:
            values["voice"] = voice_identity(runtime.store.settings() | values)["voice"]
        if values.get("broadcast_password") == "":
            values.pop("broadcast_password")
        runtime.store.save_settings(values)
        return await settings()

    @app.get("/api/driver/profile")
    async def driver_profile():
        return runtime.memory.profile()

    @app.put("/api/driver/profile")
    async def save_driver_profile(body: dict):
        profile = runtime.memory.update(body)
        runtime.director.update_profile(profile)
        return profile

    @app.get("/api/driver/memory")
    async def driver_memory():
        return runtime.memory.context()

    @app.get("/api/strategy")
    async def strategy():
        return strategy_plan(runtime.context())

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
        clips = runtime.store.clips()
        identity = voice_identity(runtime.store.settings())
        for clip in clips:
            for version in clip["versions"]:
                version["matches_voice"] = all(version.get(key) == value for key, value in identity.items())
        return clips

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
        identity = voice_identity(runtime.store.settings())
        emotion = clip_emotion(clip_id)
        data = await runtime.provider.speech(clip["text"], emotion=emotion)
        version_id = uuid.uuid4().hex
        (runtime.store.root / "audio" / (version_id+".wav")).write_bytes(data)
        latest = require_clip(clip_id)
        latest["versions"].append({"id": version_id, "approved": False, "text": clip["text"],
            **identity, "emotion": emotion, "timestamp": time.time(), "url": "/api/audio/"+version_id})
        runtime.store.put_clip(latest)
        return latest

    @app.post("/api/library/{clip_id}/approve")
    async def approve(clip_id: str, body: dict):
        clip = require_clip(clip_id)
        version = next((v for v in clip["versions"] if v["id"] == body.get("version_id")), None)
        if not version:
            raise HTTPException(404, "Version not found")
        if body.get("approved", True):
            identity = voice_identity(runtime.store.settings())
            if any(version.get(key) != value for key, value in identity.items()):
                raise ValueError("Recording uses a previous voice or speech model. Generate the current engineer voice first.")
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
        emotion = "neutral"
        for clip in runtime.store.clips():
            version = next((v for v in clip["versions"] if v["id"] == version_id), None)
            if version:
                emotion = version.get("emotion", clip_emotion(clip["id"]))
                break
        rendered = await asyncio.to_thread(runtime.audio.renderer.render_file, path, emotion)
        return FileResponse(rendered, media_type="audio/wav")

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
    async def fuel_recommendation(duration_s: float = 900, reserve_l: float = 1,
                                  car_model: str = "", track: str = ""):
        from .engineering import fuel_projection
        if not 0 < duration_s <= 86400 or not 0 <= reserve_l <= 100:
            raise ValueError("Invalid race duration or fuel reserve")
        car_model = car_model or (runtime.telemetry or {}).get("car_model", "")
        track = track or (runtime.telemetry or {}).get("track", "")
        if not car_model or not track:
            return {"status": "unknown", "reason": "Choose a car and track before using historical fuel measurements."}
        samples = [s for s in runtime.store.recent("fuel_sample", 500)
                   if s.get("car_model") == car_model and s.get("track") == track][:10]
        result = fuel_projection(0, [s["fuel_l"] for s in samples],
            [s["lap_s"] for s in samples], duration_s, reserve_l)
        return result | {"car_model": car_model, "track": track,
                         "note": "Measured laps from this car and track only. Conditions and driving style can change consumption."}

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

    @app.post("/api/radio/prepare")
    async def prepare_recognizer():
        from .controller import LocalTranscriber, TranscriptionError
        if runtime.radio["state"] != "idle":
            raise ValueError("Finish or cancel the current recording before preparing speech recognition.")
        if runtime.radio.get("stt_state") == "preparing":
            raise ValueError("Speech recognition is already preparing.")
        if runtime.transcriber is None:
            runtime.transcriber = LocalTranscriber(cache_dir=runtime.store.root / "stt-models")
        runtime.radio["stt_state"] = "preparing"
        try:
            await asyncio.to_thread(runtime.transcriber.prepare)
        except TranscriptionError as exc:
            runtime.radio["stt_state"] = "error"
            runtime.radio["stt_error_category"] = exc.category
            runtime.radio["last_error"] = str(exc)
            runtime.error(str(exc))
            raise ValueError(str(exc)) from None
        runtime.radio["stt_state"] = "ready"
        runtime.radio.pop("stt_error_category", None)
        runtime.radio.pop("last_error", None)
        return runtime.transcriber.status()

    @app.post("/api/voice/test")
    async def test_voice():
        if runtime.running:
            raise ValueError("Stop the engineer before playing a test voice.")
        text = "Radio check. Engineer online."
        data = await runtime.provider.speech(text)
        version_id = uuid.uuid4().hex
        (runtime.store.root / "audio" / (version_id + ".wav")).write_bytes(data)
        return {"url": "/api/audio/" + version_id, "text": text}

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
