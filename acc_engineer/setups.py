"""Preserving ACC setup editor with explicit, externally validated profiles.

ACC JSON often stores indices rather than engineering units. No built-in mapping
is asserted valid; users must validate paths and conversions with their own car.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import uuid
from pathlib import Path


class SetupManager:
    def __init__(self, root: str | Path, profiles: dict | None = None):
        if not str(root).strip():
            raise ValueError("An explicit ACC setup directory is required")
        self.root = Path(root).resolve()
        self.profiles = copy.deepcopy(profiles or {})

    def _path(self, name: str, must_exist: bool = True) -> Path:
        candidate = (self.root / name).resolve()
        if candidate == self.root or not candidate.is_relative_to(self.root) or candidate.suffix.lower() != ".json":
            raise ValueError("Setup must be a JSON file inside the configured setup directory")
        if must_exist and not candidate.is_file():
            raise ValueError("Setup does not exist")
        return candidate

    def supported_parameters(self) -> dict:
        return copy.deepcopy(self.profiles)

    def inspect(self, name: str) -> dict:
        path = self._path(name)
        if path.stat().st_size > 2_000_000:
            raise ValueError("Setup exceeds 2 MB limit")
        raw = path.read_bytes()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Setup root must be a JSON object")
        return {"name": name, "sha256": hashlib.sha256(raw).hexdigest(), "data": data}

    @staticmethod
    def _target(data: dict, pointer: str):
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            raise ValueError("Profile path must be a JSON pointer")
        parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
        target = data
        try:
            for part in parts[:-1]:
                target = target[int(part)] if isinstance(target, list) else target[part]
            key = int(parts[-1]) if isinstance(target, list) else parts[-1]
            current = target[key]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ValueError(f"Validated path is absent from setup: {pointer}") from exc
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise ValueError("Only existing numeric setup fields are editable")
        return target, key, current

    def create(self, source_name: str, new_name: str, changes: dict,
               profile_name: str | None = None) -> dict:
        if not changes or not isinstance(changes, dict):
            raise ValueError("Supply at least one validated parameter change")
        if profile_name not in self.profiles:
            raise ValueError("Select an explicitly validated setup profile")
        profile = self.profiles[profile_name]
        if profile.get("validated") is not True:
            raise ValueError("Profile must be explicitly marked validated")
        original = self.inspect(source_name)
        data = copy.deepcopy(original["data"])
        for pointer, expected in profile.get("match", {}).items():
            target, key, current = self._target(data, pointer)
            if current != expected:
                raise ValueError("Setup does not match the validated car/profile")
        destination = self._path(new_name, must_exist=False)
        source = self._path(source_name)
        if destination.exists() or destination == source:
            raise ValueError("Choose a new setup filename; existing files are never overwritten")
        result = []
        for parameter, value in changes.items():
            spec = profile.get("parameters", {}).get(parameter)
            if spec is None:
                raise ValueError(f"Unsupported setup parameter: {parameter}")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Parameter must be a finite number: {parameter}")
            if "min" not in spec or "max" not in spec or not spec["min"] <= value <= spec["max"]:
                raise ValueError(f"Parameter outside validated bounds: {parameter}")
            target, key, previous = self._target(data, spec["path"])
            scale, offset = spec.get("scale", 1.0), spec.get("offset", 0.0)
            raw = value * scale + offset
            if not math.isfinite(raw):
                raise ValueError("Invalid profile conversion")
            if spec.get("integer", isinstance(previous, int)):
                if abs(raw - round(raw)) > 1e-7:
                    raise ValueError(f"Value is not representable by the validated raw index: {parameter}")
                raw = int(round(raw))
            target[key] = raw
            result.append({"parameter": parameter, "path": spec["path"], "before": previous,
                           "after": raw, "requested": value, "unit": spec.get("unit", "raw")})
        # Recheck source identity before writing so the backup matches the reviewed input.
        raw_source = source.read_bytes()
        if hashlib.sha256(raw_source).hexdigest() != original["sha256"]:
            raise ValueError("Source changed during editing; inspect it again")
        backup_dir = self.root / ".backups"
        if backup_dir.resolve().parent != self.root:
            raise ValueError("Backup directory must stay inside setup root")
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f"{source.stem}-{uuid.uuid4().hex}.json"
        backup.write_bytes(raw_source)
        # Exclusive creation prevents overwrite even if another writer races us.
        payload = json.dumps(data, indent=2, allow_nan=False) + "\n"
        with destination.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        verified = self.inspect(new_name)
        return {"name": new_name, "backup": str(backup.relative_to(self.root)),
                "changes": result, "sha256": verified["sha256"], "written": True,
                "active_in_game": False, "message": "File verified. Load this setup manually in ACC; active settings are not verified."}
