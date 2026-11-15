"""Small durable store. Credentials belong in Windows Credential Manager, never SQLite."""
import json
import sqlite3
import threading
import time
from pathlib import Path


DEFAULT_ENGINEER_VOICE = "bf322df2096a46f18c579d0baa36f41d"

DEFAULTS = {
    "model": "openai/gpt-6-luna", "speech_model": "fish-audio/drama-3",
    "voice": DEFAULT_ENGINEER_VOICE, "weekly_budget_usd": 5.0, "request_limit_usd": 0.10,
    "speech_speed": 1.0, "volume": 0.8, "commentary_frequency": "normal",
    "radio_button": "R3", "keyboard_binding": "<f9>", "follow_up_listening": False,
    "recording_timeout_s": 30, "stale_after_s": 2.0,
    "setup_root": "", "setup_profiles": {}, "thresholds": {},
    "broadcast_port": 9000, "broadcast_password": "", "broadcast_enabled": False,
}


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "audio").mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "engineer.sqlite3", check_same_thread=False)
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, data TEXT);
        CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL, timestamp REAL NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS clips (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        """)
        self.db.commit()

    def settings(self):
        with self.lock:
            row = self.db.execute("SELECT data FROM settings WHERE id=1").fetchone()
            return DEFAULTS | (json.loads(row[0]) if row else {})

    def save_settings(self, values):
        with self.lock:
            data = self.settings() | values
            self.db.execute("INSERT OR REPLACE INTO settings VALUES(1,?)", (json.dumps(data),))
            self.db.commit()
            return data

    def add(self, kind, data):
        with self.lock:
            self.db.execute("INSERT INTO records(kind,timestamp,data) VALUES(?,?,?)",
                            (kind, time.time(), json.dumps(data, allow_nan=False)))
            self.db.commit()

    def recent(self, kind, limit=100):
        with self.lock:
            rows = self.db.execute("SELECT timestamp,data FROM records WHERE kind=? ORDER BY id DESC LIMIT ?",
                                   (kind, limit)).fetchall()
            return [{"timestamp": t, **json.loads(data)} for t, data in rows]

    def spend(self):
        start = time.time() - 7 * 86400
        with self.lock:
            return sum(json.loads(r[0]).get("cost_usd", 0) for r in self.db.execute(
                "SELECT data FROM records WHERE kind='usage' AND timestamp>=?", (start,)))

    def clips(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM clips ORDER BY id")]

    def clip(self, clip_id):
        return next((c for c in self.clips() if c["id"] == clip_id), None)

    def put_clip(self, clip):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO clips VALUES(?,?)", (clip["id"], json.dumps(clip)))
            self.db.commit()

    def close(self):
        self.db.close()
