"""Small, explicit driver memory stored through the application's records API."""
from __future__ import annotations

import re
import hashlib
import json
import time


_PROFILE_KIND = "driver_profile"
_SESSION_KIND = "driver_session_summary"
_MAX_PROFILE_HISTORY_SCAN = 20
_MAX_SESSION_HISTORY_SCAN = 200
_MAX_CONTEXT_BYTES = 4_000
_MAX_SESSIONS = 5
_MAX_INDEX_TEXT = 4_000
_MAX_EXCERPT = 360
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_ -]?key|password|passwd|secret|access[_ -]?token|authorization)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-(?:or-v1-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)
_QUERY_TOKENS = re.compile(r"[^\W_]+", re.UNICODE)

_DEFAULT_PROFILE = {
    "driver_name": "",
    "encouragement": "brief",
    "profanity": False,
    "driving_preferences": [],
}
_PREFERENCES = {
    "smooth inputs", "late braking", "early braking", "likes detail", "concise radio",
    "aggressive driving", "conservative driving",
}
_NAME_RE = re.compile(r"[\w][\w '\u2019-]{0,39}", re.UNICODE)
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,80}\Z")
_SUMMARY_TEXT_RE = re.compile(r"[\w][\w .,'’()&+/-]{0,79}\Z", re.UNICODE)


def _clean_profile(value):
    """Return a fixed, validated schema, never arbitrary persisted fields."""
    profile = dict(_DEFAULT_PROFILE)
    if not isinstance(value, dict):
        return profile
    name = value.get("driver_name")
    if isinstance(name, str) and _NAME_RE.fullmatch(name.strip()):
        profile["driver_name"] = name.strip()
    encouragement = value.get("encouragement")
    if isinstance(encouragement, str) and encouragement in {"none", "brief", "high"}:
        profile["encouragement"] = encouragement
    if isinstance(value.get("profanity"), bool):
        profile["profanity"] = value["profanity"]
    preferences = value.get("driving_preferences")
    if isinstance(preferences, list):
        profile["driving_preferences"] = list(dict.fromkeys(
            item for item in preferences[:7]
            if isinstance(item, str) and item in _PREFERENCES
        ))
    return profile


class DriverMemory:
    """Persist explicit personalization and compact, verified session summaries.

    Explicit profile and verified session records remain the source of truth.
    A local SQLite FTS index supports selective retrieval across those records
    and text conversations; provider traffic and raw audio are never indexed.
    """

    def __init__(self, store):
        self.store = store
        self._fts_available = False
        self._initialize_index()

    def _initialize_index(self):
        """Create the local index and idempotently import existing safe records."""
        db = self.store.db
        with self.store.lock:
            db.execute("""CREATE TABLE IF NOT EXISTS driver_memory_documents (
                doc_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                source_label TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(source_kind, source_id))""")
            existing = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='driver_memory_fts'").fetchone()
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS driver_memory_fts USING fts5(content, tokenize='unicode61')")
                self._fts_available = True
                if not existing:
                    for row in db.execute("SELECT doc_id, content FROM driver_memory_documents"):
                        db.execute("INSERT INTO driver_memory_fts(rowid, content) VALUES(?,?)", row)
            except Exception:
                # SQLite builds without FTS5 still get a local escaped-LIKE index.
                self._fts_available = False
            # Older builds indexed every profile revision. Keep only the
            # current projection; original explicit records stay intact.
            if self._fts_available:
                db.execute("DELETE FROM driver_memory_fts WHERE rowid IN (SELECT doc_id FROM driver_memory_documents WHERE source_kind='profile' AND source_id!='current')")
            db.execute("DELETE FROM driver_memory_documents WHERE source_kind='profile' AND source_id!='current'")
            db.commit()
        self._reindex_existing()

    @staticmethod
    def _safe_text(value, maximum=_MAX_INDEX_TEXT):
        if not isinstance(value, str):
            return ""
        text = value[:maximum]
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub("[redacted]", text)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
        return text.strip()

    def _put_document(self, source_kind, source_id, session_id, label, content, created_at=None):
        content = self._safe_text(content)
        if not content or content == "[redacted]":
            return False
        db = self.store.db
        old = db.execute("SELECT doc_id, content FROM driver_memory_documents WHERE source_kind=? AND source_id=?",
                         (source_kind, str(source_id))).fetchone()
        created_at = float(created_at or time.time())
        if old and old[1] == content:
            return False
        if old:
            doc_id = old[0]
            db.execute("UPDATE driver_memory_documents SET session_id=?, source_label=?, content=?, created_at=? WHERE doc_id=?",
                       (str(session_id or "")[:80], label, content, created_at, doc_id))
        else:
            cursor = db.execute("""INSERT INTO driver_memory_documents
                (source_kind, source_id, session_id, source_label, content, created_at)
                VALUES(?,?,?,?,?,?)""", (source_kind, str(source_id), str(session_id or "")[:80], label,
                                            content, created_at))
            doc_id = cursor.lastrowid
        if self._fts_available:
            db.execute("DELETE FROM driver_memory_fts WHERE rowid=?", (doc_id,))
            db.execute("INSERT INTO driver_memory_fts(rowid, content) VALUES(?,?)", (doc_id, content))
        return True

    def _reindex_existing(self):
        with self.store.lock:
            db = self.store.db
            rows = db.execute("SELECT id, kind, timestamp, data FROM records WHERE kind IN (?, ?, 'conversation') ORDER BY id",
                              (_PROFILE_KIND, _SESSION_KIND)).fetchall()
            for record_id, kind, timestamp, raw in rows:
                try:
                    data = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(data, dict):
                    continue
                self._index_record(record_id, kind, timestamp, data)
            db.commit()

    def _index_record(self, record_id, kind, timestamp, data):
        if kind == _PROFILE_KIND:
            profile = _clean_profile(data)
            pieces = ["Explicit driver profile"]
            if profile["driver_name"]:
                pieces.append("Driver name: " + profile["driver_name"])
            pieces.append("Encouragement: " + profile["encouragement"])
            pieces.append("Profanity preference: " + ("allowed" if profile["profanity"] else "off"))
            if profile["driving_preferences"]:
                pieces.append("Driving preferences: " + ", ".join(profile["driving_preferences"]))
            return self._put_document("profile", "current", "", "Current explicit driver profile", ". ".join(pieces), timestamp)
        if kind == _SESSION_KIND and data.get("verified_final") is True:
            allowed = {key: data[key] for key in ("session_id", "track", "car", "laps", "position") if key in data}
            content = "Verified final session summary: " + json.dumps(allowed, ensure_ascii=False, sort_keys=True)
            return self._put_document("session", record_id, data.get("session_id", ""),
                                      "Verified session summary", content, timestamp)
        if kind == "conversation":
            return self._index_conversation_record(record_id, timestamp, data)
        return False

    def _add_and_index(self, kind, data):
        """Write a trusted memory record and its index row under the same lock."""
        with self.store.lock:
            self.store.add(kind, data)
            record = self.store.db.execute(
                "SELECT id, timestamp, data FROM records WHERE id=last_insert_rowid()").fetchone()
            if record:
                self._index_record(record[0], kind, record[1], json.loads(record[2]))
            self.store.db.commit()

    def _index_conversation_record(self, record_id, timestamp, data):
        driver = self._safe_text(data.get("driver", ""), 1_500)
        engineer = self._safe_text(data.get("engineer", ""), 1_500)
        if not driver and not engineer:
            return
        session_id = data.get("session_id", "")
        if not isinstance(session_id, str):
            session_id = ""
        session_id = session_id[:80]
        parts = []
        if driver:
            parts.append("Driver said: " + driver)
        if engineer:
            parts.append("Engineer said (unverified assistant content, not telemetry): " + engineer)
        content = "\n".join(parts)[:_MAX_INDEX_TEXT]
        source_id = self._conversation_id(session_id, driver, engineer)
        return self._put_document("conversation", source_id, session_id,
                                  "Conversation note; assistant claims are unverified, not telemetry", content, timestamp)

    @staticmethod
    def _conversation_id(session_id, driver, engineer):
        return hashlib.sha256((str(session_id) + "\0" + driver + "\0" + engineer).encode("utf-8")).hexdigest()

    def remember_conversation(self, driver, engineer, session_id=""):
        """Index one bounded text exchange locally, without sending it anywhere."""
        driver = self._safe_text(driver, 1_500)
        engineer = self._safe_text(engineer, 1_500)
        if not driver and not engineer:
            return False
        data = {"driver": driver, "engineer": engineer,
                "session_id": str(session_id or "")[:80]}
        with self.store.lock:
            changed = self._index_conversation_record("", time.time(), data)
            self.store.db.commit()
        return changed

    def manifest(self):
        """Expose only index availability and counts, never retrieved content."""
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT source_kind, COUNT(*) FROM driver_memory_documents GROUP BY source_kind").fetchall()
        return {"available": True, "kinds": {kind: count for kind, count in rows},
                "record_count": sum(count for _, count in rows)}

    @staticmethod
    def _excerpt(content, terms):
        content = str(content)
        if len(content) <= _MAX_EXCERPT:
            return content
        folded = content.casefold()
        positions = [folded.find(term.casefold()) for term in terms if folded.find(term.casefold()) >= 0]
        start = max(0, (min(positions) if positions else 0) - 80)
        end = min(len(content), start + _MAX_EXCERPT)
        excerpt = content[start:end]
        return ("…" if start else "") + excerpt + ("…" if end < len(content) else "")

    def search(self, query, limit=5):
        """Return a small relevance-ranked set of local memory excerpts."""
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError("Query must be text up to 200 characters.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8:
            raise ValueError("Limit must be an integer from 1 to 8.")
        terms = list(dict.fromkeys(token.casefold() for token in _QUERY_TOKENS.findall(query)))[:12]
        if not terms:
            return []
        with self.store.lock:
            db = self.store.db
            rows = []
            if self._fts_available:
                safe_match = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
                try:
                    rows = db.execute("""SELECT d.doc_id, d.source_kind, d.source_id, d.session_id,
                        d.source_label, d.content, d.created_at, bm25(driver_memory_fts) AS score
                        FROM driver_memory_fts f JOIN driver_memory_documents d ON d.doc_id=f.rowid
                        WHERE driver_memory_fts MATCH ? ORDER BY score ASC, d.created_at DESC, d.doc_id DESC LIMIT ?""",
                                      (safe_match, limit)).fetchall()
                except Exception:
                    rows = []
                if not rows and len(terms) > 1:
                    # Natural queries often contain a word the note did not
                    # use ("preferred" versus "want"). Rank partial lexical
                    # matches instead of pretending the memory is absent.
                    partial_match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
                    rows = db.execute("""SELECT d.doc_id, d.source_kind, d.source_id, d.session_id,
                        d.source_label, d.content, d.created_at, bm25(driver_memory_fts) AS score
                        FROM driver_memory_fts f JOIN driver_memory_documents d ON d.doc_id=f.rowid
                        WHERE driver_memory_fts MATCH ? ORDER BY score ASC, d.created_at DESC, d.doc_id DESC LIMIT ?""",
                                      (partial_match, min(24, limit * 3))).fetchall()
                    rows = [row for row in rows if sum(
                        term in set(token.casefold() for token in _QUERY_TOKENS.findall(row[5]))
                        for term in terms) >= 2][:limit]
            if not rows:
                escaped = [term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for term in terms]
                clause = " AND ".join("content LIKE ? ESCAPE '\\' COLLATE NOCASE" for _ in escaped)
                rows = db.execute(f"""SELECT doc_id, source_kind, source_id, session_id, source_label,
                    content, created_at, 0 AS score FROM driver_memory_documents WHERE {clause}
                    ORDER BY created_at DESC, doc_id DESC LIMIT ?""",
                                   tuple(f"%{term}%" for term in escaped) + (limit,)).fetchall()
        return [{"kind": kind, "id": source_id, "session_id": session_id or None,
                 "source": label, "excerpt": self._excerpt(content, terms),
                 "timestamp": created_at}
                for _, kind, source_id, session_id, label, content, created_at, _ in rows]

    def profile(self):
        rows = self.store.recent(_PROFILE_KIND, _MAX_PROFILE_HISTORY_SCAN)
        if rows:
            return _clean_profile(rows[0])
        return dict(_DEFAULT_PROFILE)

    def update(self, values):
        """Merge a partial profile update; reject unknown or invalid fields."""
        if not isinstance(values, dict):
            raise ValueError("Profile update must be an object.")
        unknown = set(values) - set(_DEFAULT_PROFILE)
        if unknown:
            raise ValueError("Unknown driver profile field.")
        merged = self.profile()
        for key, value in values.items():
            if key == "driver_name":
                if not isinstance(value, str) or len(value.strip()) > 40 or (value.strip() and not _NAME_RE.fullmatch(value.strip())):
                    raise ValueError("Driver name must be plain text up to 40 characters.")
                merged[key] = value.strip()
            elif key == "encouragement":
                if not isinstance(value, str) or value not in {"none", "brief", "high"}:
                    raise ValueError("Encouragement must be none, brief, or high.")
                merged[key] = value
            elif key == "profanity":
                if not isinstance(value, bool):
                    raise ValueError("Profanity preference must be true or false.")
                merged[key] = value
            elif key == "driving_preferences":
                if (not isinstance(value, list) or len(value) > 7 or
                        any(not isinstance(item, str) or item not in _PREFERENCES for item in value)):
                    raise ValueError("Driving preferences must use the supported preference names.")
                merged[key] = list(dict.fromkeys(value))
        clean = _clean_profile(merged)
        self._add_and_index(_PROFILE_KIND, clean)
        return clean

    def observe(self, message):
        """Learn only narrowly phrased, explicit first-person preferences.

        This deliberately ignores free-form instructions, quoted statements,
        claims about other people, and inferred behavior.
        """
        if not isinstance(message, str) or len(message) > 300:
            return self.profile()
        text = message.strip()
        profile = self.profile()

        name_match = re.fullmatch(r"(?:my name is|call me) ([\w][\w '\u2019-]{0,39})[.!]?", text, re.IGNORECASE)
        if name_match and _NAME_RE.fullmatch(name_match.group(1)):
            profile["driver_name"] = name_match.group(1)

        folded = text.casefold()
        if re.fullmatch(r"i (?:prefer|like) (?:occasional )?(?:profanity|swearing)[.!]?", folded):
            profile["profanity"] = True
        elif re.fullmatch(r"i (?:prefer|want) (?:no|zero) profanity[.!]?|i do not like profanity[.!]?|i don't like profanity[.!]?", folded):
            profile["profanity"] = False

        encouragement = re.fullmatch(r"i (?:prefer|want) (no|brief|high) encouragement[.!]?", folded)
        if encouragement:
            profile["encouragement"] = {"no": "none", "brief": "brief", "high": "high"}[encouragement.group(1)]

        preference = re.fullmatch(r"i (?:prefer|like) (smooth inputs|late braking|early braking|detailed radio|concise radio|aggressive driving|conservative driving)[.!]?", folded)
        if preference:
            canonical = "likes detail" if preference.group(1) == "detailed radio" else preference.group(1)
            if canonical not in profile["driving_preferences"]:
                profile["driving_preferences"] = (profile["driving_preferences"] + [canonical])[:7]

        if profile != self.profile():
            self._add_and_index(_PROFILE_KIND, profile)
        return profile

    def record_session(self, summary):
        """Save a small final-session aggregate exactly once per session id."""
        if not isinstance(summary, dict) or summary.get("verified_final") is not True:
            return False
        session_id = summary.get("session_id")
        if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
            return False
        # Bounded scan means old rows cannot cause unbounded work. A repeated
        # recent session is a no-op, including when called concurrently by the
        # single application process.
        if any(row.get("session_id") == session_id for row in self.store.recent(_SESSION_KIND, _MAX_SESSION_HISTORY_SCAN)):
            return False

        clean = {"session_id": session_id, "verified_final": True}
        for key in ("track", "car"):
            value = summary.get(key)
            if isinstance(value, str) and len(value) <= 80 and _SUMMARY_TEXT_RE.fullmatch(value):
                clean[key] = value
        for key in ("laps", "position"):
            value = summary.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000:
                if key != "position" or value >= 1:
                    clean[key] = value
        self._add_and_index(_SESSION_KIND, clean)
        return True

    def context(self):
        """Return a compact prompt-safe data object with recent aggregates."""
        sessions = []
        for row in self.store.recent(_SESSION_KIND, _MAX_SESSIONS):
            if row.get("verified_final") is True:
                sessions.append({key: row[key] for key in ("session_id", "track", "car", "laps", "position") if key in row})
        result = {"profile": self.profile(), "recent_sessions": sessions}
        # Profile fields are individually bounded; this final cap protects
        # against corrupt or hand-edited records in the store.
        import json
        while len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > _MAX_CONTEXT_BYTES and sessions:
            sessions.pop()
        return result
