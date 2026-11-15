"""Deterministic delivery director for concise, human race engineer calls.

Consumes only fresh live telemetry and deterministic EventEngine outputs. This
module chooses wording, urgency and timing; it does not infer race outcomes from
unobserved events.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any

from .engineering import number


_URGENT_CLIPS = {
    "severe_incident": "damage_severe",
    "flag_red": "red",
    "flag_yellow": "yellow",
    "low_fuel": "fuel_critical",
}
_POSITIVE_ACK = re.compile(r"\b(?:okay|ok|fine|good|alright|all\s+right|yes|yep|yeah)\b", re.I)
_NEGATIVE_ACK = re.compile(r"\b(?:not|no|never|hardly)\b[^.!?]{0,24}\b(?:okay|ok|fine|good|alright|right)\b", re.I)
_CAR_RESPONSE = re.compile(r"\b(?:okay|ok|fine|good|alright|right|yes|yep|yeah|no|not|bad|wrong|damage|damaged|car|handling|steering|pulling|feels?|feel|tyres?|tires?|drive|driving)\b", re.I)


class EngineerDirector:
    """Convert telemetry events into bounded, deterministic radio intents.

    ``process`` should be called on each fresh snapshot after EventEngine has
    processed it. Returned intents are dictionaries with ``text``, ``emotion``,
    ``priority``, ``kind``, ``evidence`` and ``ttl``; urgent intents can also
    include a stable ``clip_id`` for cached audio playback.
    """

    def __init__(self, config: dict | None = None, profile: dict | None = None):
        self.config = {"frequency": "normal", **(config or {})}
        if self.config["frequency"] not in ("off", "low", "normal", "high"):
            raise ValueError("frequency must be off, low, normal or high")
        self.profile = profile or {}
        self.display_name = str(self.profile.get("driver_name") or self.profile.get("display_name") or "").strip()[:40]
        self.profanity = bool(self.profile.get("profanity", False))
        self.encouragement = self.profile.get("encouragement", "brief")
        self.session_id: Any = None
        self.last_timestamp: float | None = None
        self.last_emit: dict[str, float] = {}
        self.variant_index: dict[str, int] = {}
        self.pending_concern: dict | None = None
        self.last_driver_interaction: float | None = None
        self.safe_since: float | None = None
        self.stint_since: float | None = None
        self.last_checkin: float | None = None
        self.strategy_called = False
        self.event_count = 0
        self.intent_count = 0
        self._last_lap: float | None = None
        self._last_position: float | None = None
        self._won: bool | None = None
        self._verified_finish = False
        self._track: str | None = None
        self._car: str | None = None

    def _reset_session(self):
        self.last_emit.clear()
        self.variant_index.clear()
        self.pending_concern = None
        self.safe_since = self.last_checkin = None
        self.stint_since = None
        self.strategy_called = False
        self.event_count = self.intent_count = 0
        self._last_lap = self._last_position = None
        self._won = None
        self._verified_finish = False
        self._track = self._car = None

    @staticmethod
    def _safe(frame: dict) -> bool:
        if frame.get("in_pit") or frame.get("in_pit_lane"):
            return False
        flag = str(frame.get("flag", "none")).casefold()
        if flag not in ("none", "green", ""):
            return False
        speed = number(frame.get("speed_kmh"))
        if speed is None or speed < 40:
            return False
        acc = frame.get("acceleration_g")
        if isinstance(acc, (list, tuple)):
            vals = [number(v) for v in acc]
            if any(v is None for v in vals) or any(abs(v) >= 1.35 for v in vals):
                return False
        return True

    def _intent(self, kind: str, text: str, emotion: str, priority: int,
                evidence: dict, now: float, ttl: float = 8.0,
                clip_id: str | None = None) -> dict | None:
        if now - self.last_emit.get(kind, -math.inf) < self._cooldown(kind):
            return None
        self.last_emit[kind] = now
        result = {"kind": kind, "text": text, "emotion": emotion,
                  "priority": priority, "evidence": evidence,
                  "ttl": ttl, "timestamp": now}
        if clip_id:
            result["clip_id"] = clip_id
        self.intent_count += 1
        return result

    def _cooldown(self, kind: str) -> float:
        if kind == "checkin":
            return 300.0 if self.config["frequency"] == "normal" else 180.0
        if kind in ("fuel_strategy", "finish", "win", "session_started"):
            return 3600.0
        if kind in ("gap_ahead_closing", "gap_behind_closing"):
            return 45.0
        if kind in ("minor_damage", "rain_change", "pit_window_opened"):
            return 60.0
        return 20.0

    def _variant(self, key: str, choices: tuple[str, ...]) -> str:
        idx = self.variant_index.get(key, 0)
        self.variant_index[key] = (idx + 1) % len(choices)
        return choices[idx]

    def note_driver(self, message: str, now: float | None = None) -> bool:
        """Record driver interaction; return whether it acknowledged a concern."""
        now = time.time() if now is None else number(now)
        if now is None:
            return False
        self.last_driver_interaction = now
        msg = str(message or "").casefold()
        negative = bool(_NEGATIVE_ACK.search(msg))
        acknowledged = bool(_POSITIVE_ACK.search(msg)) and not negative
        response_received = bool(_CAR_RESPONSE.search(msg))
        if self.pending_concern and response_received:
            # A reply ends the prompt cycle. Only an explicit positive status
            # clears the concern; a negative answer remains unresolved.
            self.pending_concern["response_received"] = True
            if acknowledged:
                self.pending_concern["acknowledged"] = True
                self.pending_concern = None
        return acknowledged

    def process(self, frame: dict, events: list[dict], projection: dict,
                now: float | None = None) -> list[dict]:
        now = time.time() if now is None else number(now)
        if now is None or not isinstance(frame, dict):
            return []
        stamp = number(frame.get("timestamp"))
        if (stamp is None or stamp > now + 1 or now - stamp > 2.0
                or frame.get("stale") is True or frame.get("status") != "live"
                or frame.get("physics_valid") is False):
            return []
        session = frame.get("session_id", "unknown")
        if session != self.session_id:
            self.session_id = session
            self.last_timestamp = None
            self._reset_session()
        if self.last_timestamp is not None and stamp <= self.last_timestamp:
            return []
        self.last_timestamp = stamp
        if self.stint_since is None:
            self.stint_since = now
        self._last_lap = number(frame.get("completed_laps"))
        self._last_position = number(frame.get("position"))
        self._track = frame.get("track") or self._track
        self._car = frame.get("car_model") or self._car
        intents: list[dict] = []
        events = events if isinstance(events, list) else []
        self.event_count += len(events)

        # A concern gets one delayed check-in, unless the driver acknowledged it.
        if self.pending_concern and not self.pending_concern.get("acknowledged"):
            age = now - self.pending_concern["at"]
            if (12 <= age <= 20 and not self.pending_concern.get("followed_up")
                    and not self.pending_concern.get("response_received")):
                intent = self._intent("concern_followup", "Talk to me. How's the car feeling after that?",
                                      "concerned", 1, self.pending_concern["evidence"], now, 6,
                                      "incident_follow_up")
                if intent:
                    intents.append(intent)
                    self.pending_concern["followed_up"] = True

        for event in events:
            if not isinstance(event, dict):
                continue
            kind = str(event.get("kind", ""))
            source = event.get("source") if isinstance(event.get("source"), dict) else {}
            evidence = {"event": kind, **source}
            if kind == "severe_incident":
                text = "That's a big hit. You alright? Talk to me."
                intent = self._intent(kind, text, "concerned", 0, evidence, now, 8,
                                      _URGENT_CLIPS[kind])
                if intent:
                    intents.append(intent)
                    self.pending_concern = {"at": now, "evidence": evidence,
                                            "acknowledged": False, "followed_up": False,
                                            "response_received": False}
            elif kind in ("flag_red", "flag_yellow"):
                text = "Red flag. Follow race control instructions." if kind == "flag_red" else "Yellow flag. Caution ahead."
                intent = self._intent(kind, text, "urgent", 0, evidence, now, 8,
                                      _URGENT_CLIPS.get(kind))
                if intent:
                    intents.append(intent)
            elif kind == "damage":
                texts = ("We've got damage. Tell me how the car feels.",
                         "Damage registered. How's the handling?")
                intent = self._intent(kind, self._variant(kind, texts), "concerned", 1, evidence, now)
                if intent:
                    intent["dynamic"] = True
                    intents.append(intent)
                    self.pending_concern = {"at": now, "evidence": evidence,
                                            "acknowledged": False, "followed_up": False,
                                            "response_received": False}
            elif kind == "personal_best":
                if self.config["frequency"] == "off":
                    continue
                intent = self._intent(kind, self._variant(kind, ("That's your best. Brilliant lap; keep it tidy.",
                                                            "Personal best. Great pace, now repeat it.")),
                                      "excited", 2, evidence, now)
                if intent:
                    intent["dynamic"] = True
                    intents.append(intent)
            elif kind == "low_fuel":
                intent = self._intent(kind, "Fuel is critical. Box if you can.",
                                      "urgent", 0, evidence, now, 8, _URGENT_CLIPS[kind])
                if intent:
                    intents.append(intent)
            elif kind == "fuel_deficit":
                intent = self._fuel_call(frame, projection, evidence, now)
                if intent:
                    intents.append(intent)
            elif kind in ("flag_checkered", "flag_chequered", "session_finished"):
                self._consider_finish(frame, events, kind, evidence, now, intents)
            elif kind == "session_started":
                intent = self._intent(kind, "We're racing. Settle into your rhythm.",
                                      "calm", 1, evidence, now)
                if intent:
                    intents.append(intent)

            elif kind in ("flag_green", "flag_blue", "rain_change", "gap_ahead_closing",
                          "gap_behind_closing", "pit_window_opened", "minor_damage"):
                if self.config["frequency"] == "off" and kind in (
                        "flag_green", "gap_ahead_closing", "gap_behind_closing", "minor_damage"):
                    continue
                details = {
                    "flag_green": ("Green flag. Track clear.", "calm", 1, "green"),
                    "flag_blue": ("Blue flag. Faster car approaching.", "urgent", 1, "blue"),
                    "rain_change": ("Rain conditions have changed. Watch the grip.", "firm", 1, "rain_change"),
                    "gap_ahead_closing": ("You're closing on the car ahead. Keep it clean.", "calm", 2, "gap_ahead_closing"),
                    "gap_behind_closing": ("The car behind is closing. Keep your focus.", "firm", 1, "gap_behind_gaining"),
                    "pit_window_opened": ("The pit window is open.", "calm", 1, "pit_open"),
                    "minor_damage": ("We've picked up a little damage. Keep an eye on the handling.", "concerned", 3, "damage_minor"),
                }
                text, emotion, priority, clip = details[kind]
                intent = self._intent(kind, text, emotion, priority, evidence, now, 8, clip)
                if intent:
                    intents.append(intent)
            elif kind in ("tyres_hot", "brakes_hot", "penalty_received", "lap_invalidated"):
                texts = {"tyres_hot": "Tyres are running hot. Be smooth through the next sector.",
                         "brakes_hot": "Brakes are hot. Give them a clean, measured lap.",
                         "penalty_received": "Penalty noted. Check the race control notice.",
                         "lap_invalidated": "That lap's invalid. Reset and keep it clean."}
                intent = self._intent(kind, texts[kind], "firm", 1, evidence, now)
                if intent:
                    intents.append(intent)

        if projection.get("status") == "estimated" and number(projection.get("surplus_l")) is not None and projection["surplus_l"] < 0 and not self.strategy_called:
            intent = self._fuel_call(frame, projection, {"projection": projection}, now)
            if intent:
                intents.append(intent)
        self._checkin(frame, now, intents)
        return sorted(intents, key=lambda item: item["priority"])

    def _fuel_call(self, frame: dict, projection: dict, evidence: dict, now: float):
        if projection.get("status") != "estimated":
            return None
        short = number(projection.get("surplus_l"))
        if short is None or short >= 0:
            return None
        self.strategy_called = True
        text = f"Projection is {abs(short):.1f} litres short. Start saving fuel now."
        laps = number(projection.get("laps_to_finish"))
        pit_closed = frame.get("pitlane_open") is False or frame.get("pit_lane_open") is False or frame.get("pitlane_closed") is True
        if (frame.get("pit_window_open") is True and not pit_closed
                and not frame.get("in_pit_lane") and laps is not None and laps > 1):
            text += " Box when the window allows."
        intent = self._intent("fuel_strategy", text, "firm", 1, evidence, now, 10)
        if intent:
            intent["dynamic"] = True
        return intent

    def _consider_finish(self, frame, events, kind, evidence, now, intents):
        explicit_finish = (frame.get("session") == "race"
                           and (frame.get("phase") in ("finished", "complete")
                                or frame.get("race_status") in ("finished", "complete")
                                or any(isinstance(e, dict) and e.get("kind") in ("flag_checkered", "flag_chequered") for e in events)))
        if not explicit_finish:
            return
        position = number(frame.get("position"))
        won = position == 1
        if self._won is not None:
            return
        self._won = won
        self._verified_finish = True
        text = "Booyah! P1! That's a fantastic drive. Bring it home." if won else "Chequered flag. Bring it home."
        if won and self.display_name:
            text = f"P1, {self.display_name}! That's a fantastic drive. Bring it home."
        if won and self.profanity:
            text = f"P1! That's good shit, {self.display_name or 'driver'}. Bring it home."
        label = "win" if won else "finish"
        intent = self._intent(label, text, "excited", 1,
                              {**evidence, "position": position, "race": True}, now, 30,
                              "race_win" if won and not (self.display_name or self.profanity)
                              else ("race_finish" if not won else None))
        if intent:
            intent["dynamic"] = bool(won and (self.display_name or self.profanity))
            intents.append(intent)

    def _checkin(self, frame, now, intents):
        frequency = self.config["frequency"]
        if frequency not in ("normal", "high") or self.encouragement == "none":
            self.safe_since = None
            return
        if not self._safe(frame):
            self.safe_since = None
            return
        if self.safe_since is None:
            self.safe_since = now
            return
        # Ordinary corners defer a call; they must not restart the entire
        # two-minute cadence and make check-ins impossible on a real circuit.
        if now - self.safe_since < 2 or now - self.stint_since <= 120:
            return
        if self.last_driver_interaction is not None and now - self.last_driver_interaction < 45:
            return
        interval = 180 if frequency == "high" or self.encouragement == "high" else 300
        if self.last_checkin is not None and now - self.last_checkin < interval:
            return
        name = f"{self.display_name}, " if self.display_name else ""
        text = f"{name}how's the car feeling?"
        intent = self._intent("checkin", text, "neutral", 3,
                              {"safe_driving_s": round(now - self.safe_since)}, now, 8)
        if intent:
            if self.display_name:
                intent["dynamic"] = True
            else:
                intent["clip_id"] = "check_in"
            intents.append(intent)
            self.last_checkin = now

    def snapshot(self) -> dict:
        """Small public runtime state for the app status panel."""
        pending = self.pending_concern
        return {"mood": "concerned" if pending else "focused",
                "pending_checkin": bool(pending and not pending.get("acknowledged")
                                         and not pending.get("followed_up")
                                         and not pending.get("response_received")),
                "strategy_called": self.strategy_called,
                "safe_driving_since": self.safe_since,
                "last_driver_interaction": self.last_driver_interaction,
                "calls_issued": self.intent_count,
                "confirmed_win": self._won is True}

    def update_profile(self, profile: dict | None = None):
        """Apply a profile update without discarding session history."""
        profile = profile or {}
        self.profile = profile
        self.display_name = str(profile.get("driver_name") or profile.get("display_name") or "").strip()[:40]
        self.profanity = bool(profile.get("profanity", False))
        self.encouragement = profile.get("encouragement", "brief")

    def session_summary(self) -> dict:
        """Return a small factual session summary suitable for a debrief."""
        laps, position = self._last_lap, self._last_position
        valid = (self._verified_finish and laps is not None and position is not None
                 and laps >= 0 and position >= 1
                 and float(laps).is_integer() and float(position).is_integer())
        return {"verified_final": bool(valid), "session_id": self.session_id,
                "track": self._track or "unknown", "car": self._car or "unknown",
                "laps": int(laps) if valid else 0,
                "position": int(position) if valid else 0,
                "confirmed_win": self._won is True}

    def summary(self) -> dict:
        """Compatibility alias used by the app's session-close hook."""
        return self.session_summary()
