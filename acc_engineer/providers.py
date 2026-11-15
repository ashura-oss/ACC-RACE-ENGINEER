"""OpenRouter requests with a serialized, conservative local expenditure gate."""
import asyncio
import os
import time
import math
import io
import wave
from email.message import Message

import httpx
from .storage import DEFAULT_ENGINEER_VOICE
from .personality import SYSTEM_PROMPT, relevant_context


def voice_identity(settings):
    return {"voice": (settings.get("voice") or "").strip() or DEFAULT_ENGINEER_VOICE,
            "speech_model": settings.get("speech_model", "fish-audio/drama-3")}

SYSTEM = SYSTEM_PROMPT


class ProviderError(RuntimeError):
    pass


def _web_search_enabled(model, payload):
    """Whether this request can invoke OpenRouter's separately billed search."""
    if str(model).endswith(":online"):
        return True
    if not isinstance(payload, dict):
        return False

    plugins = payload.get("plugins", [])
    if isinstance(plugins, (dict, list)):
        plugin_items = plugins.values() if isinstance(plugins, dict) else plugins
        for plugin in plugin_items:
            if isinstance(plugin, str):
                label = plugin
            elif isinstance(plugin, dict):
                label = " ".join(str(plugin.get(key, "")) for key in ("id", "name", "type"))
            else:
                continue
            if "web" in label.lower() or "search" in label.lower():
                return True

    # OpenRouter may expose search as a tool rather than a plugin. Look only at
    # tool declarations, not arbitrary user prompt text.
    tools = payload.get("tools", [])
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict):
                function = tool.get("function", {})
                label = " ".join(str(tool.get(key, "")) for key in ("type", "name"))
                if isinstance(function, dict):
                    label += " " + str(function.get("name", ""))
                labels = label.lower().split()
                if any(x.startswith("web_search") or x in {"web", "browser_search"} for x in labels):
                    return True
    return False


class OpenRouter:
    BASE = "https://openrouter.ai/api/v1"

    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        self.catalogue = {}
        self.context_supplier = None
        self.memory_search = None
        self.client = httpx.AsyncClient(base_url=self.BASE, timeout=30)

    def key(self):
        if os.getenv("OPENROUTER_API_KEY"):
            return os.environ["OPENROUTER_API_KEY"].strip()
        try:
            import keyring
            return keyring.get_password("ACC AI Race Engineer", "openrouter") or ""
        except Exception:
            return ""

    def save_key(self, value):
        import keyring
        keyring.set_password("ACC AI Race Engineer", "openrouter", value.strip())

    async def models(self):
        response = await self.client.get("/models")
        response.raise_for_status()
        self.catalogue = {m["id"]: m for m in response.json()["data"]}
        # Speech models may not be included in the default text catalogue.
        model = self.store.settings()["speech_model"]
        if model not in self.catalogue:
            response = await self.client.get(f"/models/{model}/endpoints")
            if response.is_success:
                data = response.json()["data"]
                endpoints = data.get("endpoints", [])
                if endpoints:
                    data["pricing"] = endpoints[0].get("pricing", {})
                    self.catalogue[model] = data
        return list(self.catalogue.values())

    async def _post(self, model, endpoint, payload, max_tokens=0, input_units=0):
        async with self.lock:
            if not self.key():
                raise ProviderError("Configure an OpenRouter key in AI Settings.")
            if model not in self.catalogue:
                await self.models()
            info = self.catalogue.get(model)
            if not info or not info.get("pricing"):
                raise ProviderError("Model or pricing unavailable; request blocked.")
            pricing = info["pricing"]
            try:
                if not isinstance(pricing, dict):
                    raise ValueError
                prompt_rates = [float(pricing["prompt"])]
                completion_rates = [float(pricing["completion"])]
                cache_write_rates = [float(pricing.get("input_cache_write", 0))]
                if not all(math.isfinite(v) and v >= 0 for v in prompt_rates + completion_rates):
                    raise ValueError
                # Luna's catalogue entry has an optional, separately billed web
                # search rate. Accept the metadata only when this request cannot
                # invoke search; search itself has no local usage bound.
                for name, value in pricing.items():
                    if name in {"prompt", "completion", "discount", "input_cache_read", "input_cache_write"}:
                        numeric = float(value or 0)
                        if not math.isfinite(numeric) or numeric < 0:
                            raise ValueError
                    elif name == "web_search":
                        numeric = float(value)
                        if not math.isfinite(numeric) or numeric < 0 or (numeric > 0 and _web_search_enabled(model, payload)):
                            raise ValueError
                    elif name == "overrides":
                        if not isinstance(value, list):
                            raise ValueError
                        for tier in value:
                            if not isinstance(tier, dict) or set(tier) - {
                                "min_prompt_tokens", "prompt", "completion",
                                "input_cache_read", "input_cache_write",
                            }:
                                raise ValueError
                            minimum = tier.get("min_prompt_tokens")
                            if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not math.isfinite(minimum) or minimum < 0:
                                raise ValueError
                            if "prompt" not in tier or "completion" not in tier:
                                raise ValueError
                            tier_prompt = float(tier["prompt"])
                            tier_completion = float(tier["completion"])
                            if not all(math.isfinite(v) and v >= 0 for v in (tier_prompt, tier_completion)):
                                raise ValueError
                            for cache_name in ("input_cache_read", "input_cache_write"):
                                if cache_name in tier:
                                    cache_rate = float(tier[cache_name])
                                    if not math.isfinite(cache_rate) or cache_rate < 0:
                                        raise ValueError
                            cache_write_rates.append(float(tier.get("input_cache_write", 0)))
                            prompt_rates.append(tier_prompt)
                            completion_rates.append(tier_completion)
                    else:
                        raise ValueError
                # Reserve cache writes in addition to uncached input as a safe
                # upper bound even if a provider automatically enables caching.
                prompt_rate = max(prompt_rates) + max(cache_write_rates)
                completion_rate = max(completion_rates)
            except (KeyError, TypeError, ValueError):
                raise ProviderError("Unverified pricing units; request blocked.") from None
            # Token upper bound: UTF-8 bytes plus margin. Speech prompt units are
            # conservatively bounded by bytes as well. Provider billing is authoritative.
            bound = ((input_units + len(str(payload).encode()) + 512) * prompt_rate
                     + max_tokens * completion_rate)
            settings = self.store.settings()
            if bound > settings["request_limit_usd"] or self.store.spend() + bound > settings["weekly_budget_usd"]:
                raise ProviderError("Local expenditure limit reached; cached warnings remain available.")
            started = time.perf_counter()
            # Reserve before dispatch; failed, cancelled and timed-out requests can
            # still be billable. Reservations intentionally remain conservative.
            self.store.add("usage", {"model": model, "cost_usd": bound, "estimated": True,
                                     "outcome": "reserved", "request_id": str(time.time_ns())})
            try:
                response = await self.client.post(endpoint, json=payload,
                    headers={"Authorization": f"Bearer {self.key()}"})
                if response.is_error:
                    # Do not persist provider response bodies, which may echo credentials.
                    raise ProviderError(f"OpenRouter returned HTTP {response.status_code}.")
                usage = response.json().get("usage", {}) if endpoint.endswith("completions") else {}
                actual = usage.get("cost")
                cost = float(actual) if actual is not None else bound
                self.store.add("usage_detail", {"model": model, "cost_usd": cost,
                    "estimated": actual is None, "tokens": usage.get("total_tokens"),
                    "latency_ms": round((time.perf_counter()-started)*1000)})
                return response
            except httpx.HTTPError:
                raise ProviderError("Provider request failed; local warnings remain active.") from None

    async def answer(self, message, context, image=None):
        import json
        settings = self.store.settings()
        model = settings["model"]
        if model not in self.catalogue:
            await self.models()
        content = [{"type": "text", "text": json.dumps({"driver": message, "race": relevant_context(message, context)})}]
        if image:
            modalities = self.catalogue.get(model, {}).get("architecture", {}).get("input_modalities", [])
            if "image" not in modalities:
                raise ProviderError("Selected model does not advertise image support.")
            content.append({"type": "image_url", "image_url": {"url": image}})
        payload = {"model": model, "messages": [{"role": "system", "content": SYSTEM},
                   {"role": "user", "content": content}], "max_tokens": 100, "temperature": 0.4}
        if not image:
            from .tools import TOOL_DEFINITIONS
            payload["tools"] = TOOL_DEFINITIONS
        response = await self._post(model, "/chat/completions", payload, 100)
        reply = response.json()["choices"][0]["message"]
        calls = reply.get("tool_calls", [])
        if calls:
            from .tools import execute
            if len(calls) > 6:
                raise ProviderError("Model requested too many telemetry tools.")
            payload["messages"].append(reply)
            for call in calls:
                fn = call["function"]
                try:
                    tool_context = self.context_supplier() if self.context_supplier else context
                    result = execute(fn["name"], json.loads(fn.get("arguments", "{}")), tool_context,
                                     memory_search=self.memory_search)
                except (ValueError, TypeError, KeyError):
                    result = {"status": "unknown", "reason": "Unsupported tool or arguments."}
                payload["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
                self.store.add("tool", {"name": fn["name"], "result": result})
            payload.pop("tools", None)
            response = await self._post(model, "/chat/completions", payload, 100)
            reply = response.json()["choices"][0]["message"]
        text = reply.get("content")
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("Model returned no radio response.")
        return text

    async def speech(self, text, emotion="neutral"):
        settings = self.store.settings()
        identity = voice_identity(settings)
        payload = {"model": identity["speech_model"], "input": text, "voice": identity["voice"],
                   "response_format": "pcm", "speed": 1.0}
        # Every request pins one speaker. Do not silently retry with a random
        # provider voice if that speaker is unavailable or rejected.
        emotion_instructions = {
            "neutral": "Keep the delivery composed and direct.",
            "excited": "Sound pleased and energized, while staying controlled.",
            "concerned": "Sound attentive and concerned, without panic.",
            "urgent": "Sound urgent and concise, without shouting.",
            "firm": "Sound firm and decisive, without aggression.",
            "calm": "Sound calm and reassuring.",
        }
        if settings["speech_model"].startswith("fish-audio/"):
            mood = emotion_instructions.get(str(emotion).lower(), emotion_instructions["neutral"])
            payload["instructions"] = (
                "Preserve the selected speaker identity. Brisk, clipped team-radio delivery "
                "with a slightly gritty edge. Speak naturally and directly. "
                "No dramatic pauses, narration or drawn-out syllables. " + mood
            )
        if settings["speech_model"] == "fish-audio/drama-3":
            # Drama 3 documents inline directions as its performance control.
            directions = {
                "excited": "excited, triumphant, energetic team radio burst, brisk",
                "concerned": "concerned, alert, checking on your driver, quick, no long pauses",
                "urgent": "urgent, sharp, decisive team radio burst, quick and clear",
                "firm": "firm, controlled frustration, brisk and clear, no insults",
                "calm": "steady, experienced engineer, brisk clipped team radio",
                "neutral": "natural, brisk clipped team radio, slightly gritty",
            }
            direction = directions.get(str(emotion).lower(), directions["neutral"])
            payload["input"] = f"[{direction}] {text}"
        response = await self._post(settings["speech_model"], "/audio/speech", payload)
        return speech_to_wav(response.content, response.headers.get("content-type", ""))


def speech_to_wav(data, content_type):
    """Wrap OpenRouter's PCM16LE using the returned rate/channels, never guessed."""
    if not data or len(data) > 16_000_000:
        raise ProviderError("Speech provider returned empty or oversized audio.")
    if data.startswith(b"RIFF"):
        try:
            with wave.open(io.BytesIO(data), "rb") as source:
                if source.getsampwidth() != 2 or source.getnframes() == 0:
                    raise ValueError
            return data
        except (ValueError, EOFError, wave.Error):
            raise ProviderError("Speech provider returned an invalid WAV recording.") from None
    metadata = Message()
    metadata["content-type"] = content_type
    if metadata.get_content_type() != "audio/pcm":
        raise ProviderError("Speech provider returned an unsupported audio format.")
    try:
        rate = int(metadata.get_param("rate") or 0)
        channels = int(metadata.get_param("channels") or 0)
        if not 8000 <= rate <= 192000 or channels not in (1, 2) or len(data) % (2 * channels):
            raise ValueError
    except (TypeError, ValueError):
        raise ProviderError("Speech PCM metadata is missing or invalid; sample rate was not guessed.") from None
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(data)
    return output.getvalue()
