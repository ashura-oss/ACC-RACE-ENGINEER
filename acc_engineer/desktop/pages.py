"""Native Qt pages for the ACC Race Engineer desktop application.

All backend access goes through the shell supplied API bridge. Pages never
embed a browser and never fetch provider data merely because they were opened.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from urllib.parse import quote, urlencode

from PySide6.QtCore import QUrl, Qt
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget, QLayout,
)


DEFAULT_ENGINEER_VOICE_ID = "bf322df2096a46f18c579d0baa36f41d"


def _pretty(value):
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _time(value):
    try:
        return datetime.fromtimestamp(float(value)).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "—"


def _items(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return value.get("data", value.get("items", [])) or []
    return []


def _table(headers, rows, parent=None):
    table = QTableWidget(0, len(headers), parent)
    table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    for row in rows:
        idx = table.rowCount()
        table.insertRow(idx)
        for col, value in enumerate(row):
            item = QTableWidgetItem("—" if value is None else str(value))
            item.setToolTip(item.text())
            table.setItem(idx, col, item)
    table.resizeColumnsToContents()
    table.horizontalHeader().setStretchLastSection(True)
    return table


class Page(QWidget):
    def __init__(self, api, title, subtitle=""):
        super().__init__()
        self.api = api
        self.status = {}
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(22, 20, 22, 22)
        self.root.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        self.root.addWidget(heading)
        if subtitle:
            desc = QLabel(subtitle)
            desc.setObjectName("pageSubtitle")
            desc.setWordWrap(True)
            self.root.addWidget(desc)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        self.root.addLayout(self.body, 1)
        self.api.status_changed.connect(self._status_changed)

    def _status_changed(self, value):
        self.status = value or {}
        self.on_status()

    def on_status(self):
        pass

    def request(self, method, path, body=None, callback=None, finished=None):
        self.api.request(method, path, body, callback, finished=finished)

    def refresh(self):
        pass

    def button(self, label, callback, secondary=False):
        button = QPushButton(label)
        if secondary:
            button.setProperty("secondary", True)
        button.clicked.connect(callback)
        return button

    def group(self, title, layout=None):
        box = QGroupBox(title)
        if layout is None:
            layout = QVBoxLayout(box)
        else:
            box.setLayout(layout)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(10)
        self.body.addWidget(box)
        return box, layout

    def status_label(self, text="Waiting for data"):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("mutedText")
        return label


class OverviewPage(Page):
    def __init__(self, api):
        super().__init__(api, "Race overview", "Live local status and quick controls. Telemetry updates arrive from the ACC integration.")
        self.summary = QLabel("Connecting to the local race engineer…")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("statusCard")
        self.body.addWidget(self.summary)
        self.critical_audio = QLabel()
        self.critical_audio.setWordWrap(True)
        self.critical_audio.setStyleSheet("color: #ff9e73;")
        self.body.addWidget(self.critical_audio)
        _, chat = self.group("Ask the engineer")
        chat_row = QHBoxLayout()
        self.chat_input = QLineEdit(); self.chat_input.setPlaceholderText("Ask about the current race…")
        self.chat_output = QLabel("Responses appear here. This sends a request to the configured provider.")
        self.chat_output.setTextFormat(Qt.TextFormat.PlainText)
        self.chat_output.setWordWrap(True)
        chat_row.addWidget(self.chat_input, 1)
        chat_row.addWidget(self.button("Send", self._chat))
        chat.addLayout(chat_row); chat.addWidget(self.chat_output)
        _, actions = self.group("Engineer")
        row = QHBoxLayout()
        self.start_btn = self.button("Start engineer", lambda: self._command("start"))
        self.stop_btn = self.button("Stop engineer", lambda: self._command("stop"), True)
        self.mute_btn = self.button("Mute audio", lambda: self._mute(), True)
        row.addWidget(self.start_btn); row.addWidget(self.stop_btn); row.addWidget(self.mute_btn)
        actions.addLayout(row)
        _, details = self.group("Current session")
        self.details = QLabel("—")
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        details.addWidget(self.details)
        self.body.addStretch(1)

    def _command(self, action):
        self.request("POST", f"/api/engineer/{action}", {}, lambda _data: self.refresh())

    def _mute(self):
        self.request("POST", "/api/engineer/mute", {"muted": not bool(self.status.get("muted"))}, lambda _data: self.refresh())

    def _chat(self):
        message = self.chat_input.text().strip()
        if not message: return
        self.chat_input.clear()
        self.request("POST", "/api/chat", {"message": message}, lambda d: self.chat_output.setText((d or {}).get("text", "No response text returned.")))

    def on_status(self):
        s = self.status; t = s.get("telemetry") or {}; age = s.get("telemetry_age_s")
        engineer = s.get("engineer") or {}
        last_call = engineer.get("last_call")
        if isinstance(last_call, dict):
            last_call = last_call.get("text") or last_call.get("response") or last_call.get("call")
        missing = (s.get("audio_readiness") or {}).get("missing_critical", [])
        self.critical_audio.setText("Critical warnings need review for this voice: " + ", ".join(missing) if missing else "")
        fresh = s.get("source") == "acc" and t.get("status") == "live" and age is not None and age < 2
        self.summary.setText(f"{'ENGINEER RUNNING' if s.get('running') else 'ENGINEER STOPPED'}   ·   {s.get('source', 'disconnected')} source   ·   {'LIVE' if fresh else 'NO FRESH TELEMETRY'}")
        self.start_btn.setEnabled(not s.get("running", False))
        self.stop_btn.setEnabled(bool(s.get("running")))
        self.mute_btn.setText("Unmute audio" if s.get("muted") else "Mute audio")
        self.details.setText(
            (f"Session: {t.get('session_id', '—')}    |    Data age: {age:.2f} s" if isinstance(age, (int, float)) else f"Session: {t.get('session_id', '—')}    |    Data age: —")
            + f"\nSpeed: {t.get('speed_kmh', '—')} km/h    ·    Fuel: {t.get('fuel_l', '—')} L    ·    Lap: {t.get('completed_laps', '—')}"
            + f"\nModel: {((s.get('provider') or {}).get('model', '—'))}    ·    7 day spend: ${s.get('spend_usd', 0):.4f}"
            + (f"\nEngineer: {engineer.get('mood') or 'steady'}" +
               (f"    ·    Last call: {last_call[:100]}" if isinstance(last_call, str) and last_call else ""))
        )

    def refresh(self):
        self.request("GET", "/api/status", callback=lambda d: self._status_changed(d))


class PersonalEngineerPage(Page):
    """Readable driver preferences, engineer state, and measured race plan."""
    def __init__(self, api):
        super().__init__(api, "Personal engineer", "Set how your engineer talks to you and review remembered verified sessions.")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        scroll.setWidget(inner)
        self.body.addWidget(scroll, 1)
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(0, 0, 10, 0)
        self.body.setSpacing(12)
        self.body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        _, preferences = self.group("Your radio preferences")
        form = QFormLayout()
        self.driver_name = QLineEdit()
        self.driver_name.setMaxLength(40)
        self.driver_name.setMinimumHeight(36)
        self.driver_name.setPlaceholderText("Name to use on radio")
        self.encouragement = QComboBox()
        self.encouragement.setMinimumHeight(36)
        self.encouragement.addItems(["none", "brief", "high"])
        self.profanity = QCheckBox("Allow occasional celebratory profanity")
        self.profanity.setMinimumHeight(30)
        self.driving_preferences = QTextEdit()
        self.driving_preferences.setPlaceholderText("One preference per line, such as smooth inputs or late braking")
        self.driving_preferences.setMaximumHeight(92)
        self.driving_preferences.setMinimumHeight(80)
        form.addRow("Driver name", self.driver_name)
        form.addRow("Encouragement", self.encouragement)
        form.addRow("Language", self.profanity)
        form.addRow("Driving preferences", self.driving_preferences)
        preferences.addLayout(form)
        preferences.addWidget(self.button("Save preferences", self.save_preferences))

        _, personal = self.group("Engineer today")
        self.engineer_state = QLabel("Engineer status is not available yet.")
        self.engineer_state.setTextFormat(Qt.TextFormat.PlainText)
        self.engineer_state.setWordWrap(True)
        personal.addWidget(self.engineer_state)

        _, strategy = self.group("Measured strategy")
        self.strategy_state = QLabel("No strategy plan available.")
        self.strategy_state.setTextFormat(Qt.TextFormat.PlainText)
        self.strategy_state.setWordWrap(True)
        strategy.addWidget(self.strategy_state)

        _, history = self.group("Recent verified sessions")
        self.sessions = QLabel("No verified sessions remembered yet.")
        self.sessions.setTextFormat(Qt.TextFormat.PlainText)
        self.sessions.setWordWrap(True)
        history.addWidget(self.sessions)
        self.body.addStretch(1)

    def refresh(self):
        self.request("GET", "/api/driver/profile", callback=self._profile_loaded)
        self.request("GET", "/api/driver/memory", callback=self._memory_loaded)
        self.request("GET", "/api/strategy", callback=self._strategy_loaded)
        self.request("GET", "/api/status", callback=lambda d: self._status_changed(d))

    def _profile_loaded(self, data):
        profile = data if isinstance(data, dict) else {}
        self.driver_name.setText(str(profile.get("driver_name") or ""))
        encouragement = profile.get("encouragement", "brief")
        self.encouragement.setCurrentText(encouragement if encouragement in {"none", "brief", "high"} else "brief")
        self.profanity.setChecked(profile.get("profanity") is True)
        values = profile.get("driving_preferences") or []
        if not isinstance(values, list):
            values = []
        self.driving_preferences.setPlainText("\n".join(str(value) for value in values if isinstance(value, str)))

    def _memory_loaded(self, data):
        rows = data.get("recent_sessions", []) if isinstance(data, dict) else []
        lines = []
        if isinstance(rows, list):
            for row in rows[:5]:
                if not isinstance(row, dict):
                    continue
                details = [str(row[key]) for key in ("track", "car") if row.get(key)]
                if isinstance(row.get("laps"), int):
                    details.append(f"{row['laps']} laps")
                if isinstance(row.get("position"), int):
                    details.append(f"P{row['position']}")
                if details:
                    lines.append(" · ".join(details))
        self.sessions.setText("\n".join(lines) if lines else "No verified sessions remembered yet.")

    @staticmethod
    def _strategy_text(data):
        if not isinstance(data, dict):
            return "No strategy plan available."
        plan = data.get("plan") if isinstance(data.get("plan"), dict) else data
        status = plan.get("status", data.get("status", "measured"))
        lines = [f"Plan: {str(status).replace('_', ' ')}"]
        unknown = plan.get("unknown_reason") or plan.get("reason") or data.get("unknown_reason")
        if unknown and str(status).casefold() in {"unknown", "unavailable", "insufficient_data"}:
            lines.append("Unknown: " + str(unknown)[:220])
        actions = plan.get("actions") or plan.get("recommendations") or []
        if isinstance(actions, list):
            for item in actions[:5]:
                if isinstance(item, str):
                    lines.append("• " + item[:200])
                elif isinstance(item, dict):
                    action = item.get("action") or item.get("call") or item.get("title") or item.get("recommendation")
                    detail = item.get("reason") or item.get("rationale")
                    if action:
                        lines.append("• " + str(action)[:180] + (" — " + str(detail)[:180] if detail else ""))
        limits = plan.get("limits") or data.get("limits")
        if limits:
            lines.append("Limits: " + str(limits)[:220])
        if len(lines) == 1 and status == "unknown" and unknown:
            lines.append("Unknown: " + str(unknown)[:220])
        elif len(lines) == 1 and not unknown:
            lines.append("No measured actions are available.")
        return "\n".join(lines)

    def _strategy_loaded(self, data):
        self.strategy_state.setText(self._strategy_text(data))

    def save_preferences(self):
        name = self.driver_name.text().strip()
        preferences = [line.strip() for line in self.driving_preferences.toPlainText().splitlines() if line.strip()]
        values = {
            "driver_name": name,
            "encouragement": self.encouragement.currentText(),
            "profanity": self.profanity.isChecked(),
            "driving_preferences": preferences,
        }
        self.request("PUT", "/api/driver/profile", values,
                     lambda data: self._profile_loaded(data if isinstance(data, dict) else values))

    def on_status(self):
        engineer = self.status.get("engineer") or {}
        mood = engineer.get("mood") or "steady"
        call = engineer.get("last_call")
        if isinstance(call, dict):
            call = call.get("text") or call.get("response") or call.get("summary")
        concern = engineer.get("pending_concern")
        if isinstance(concern, dict):
            concern = concern.get("text") or concern.get("summary")
        parts = [f"Engineer: {str(mood).replace('_', ' ')}"]
        if concern and isinstance(concern, str):
            parts.append("Concern: " + concern[:180])
        if call and isinstance(call, str):
            parts.append("Last call: " + call[:220])
        self.engineer_state.setText("\n".join(parts))


class RadioPage(Page):
    def __init__(self, api):
        super().__init__(api, "Driver radio", "Push to talk is explicit. The microphone opens only when you start a recording. Press again to submit.")
        _, box = self.group("Radio controls")
        self.state = QLabel("Radio idle")
        self.state.setObjectName("statusCard")
        box.addWidget(self.state)
        row = QHBoxLayout()
        row.addWidget(self.button("Toggle radio / submit", lambda: self.request("POST", "/api/radio/toggle", {}, lambda _d: self.refresh())))
        row.addWidget(self.button("Cancel recording", lambda: self.request("POST", "/api/radio/cancel", {}, lambda _d: self.refresh()), True))
        box.addLayout(row)
        self.body.addStretch(1)

    def on_status(self):
        r = self.status.get("radio") or {}
        self.state.setText(f"Radio: {r.get('state', 'idle')}    ·    Controller: {r.get('controller', 'status unavailable')}")

    def refresh(self):
        self.request("GET", "/api/status", callback=lambda d: self._status_changed(d))


class LibraryPage(Page):
    def __init__(self, api):
        super().__init__(api, "Radio library", "Review scripts and generated speech. A recording must be approved before use.")
        bar = QHBoxLayout()
        self.count = QLabel("— scripts")
        bar.addWidget(self.button("Refresh library", self.refresh, True)); bar.addStretch(1); bar.addWidget(self.count)
        self.body.addLayout(bar)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.list = QTableWidget(0, 4)
        self.list.setHorizontalHeaderLabels(["Clip", "Category", "Priority", "Versions"])
        self.list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.list.verticalHeader().setVisible(False)
        self.list.itemSelectionChanged.connect(self._select)
        split.addWidget(self.list)
        right = QWidget(); layout = QVBoxLayout(right)
        self.clip_title = QLabel("Select a script")
        self.clip_title.setObjectName("sectionTitle")
        layout.addWidget(self.clip_title)
        self.script = QTextEdit(); self.script.setMaximumHeight(110); self.script.setPlaceholderText("Radio script text")
        layout.addWidget(self.script)
        row = QHBoxLayout()
        row.addWidget(self.button("Save script", self._save)); row.addWidget(self.button("Generate speech", self._generate, True))
        layout.addLayout(row)
        self.versions = QTableWidget(0, 5); self.versions.setHorizontalHeaderLabels(["Version", "Status", "Voice ID", "Speech model", "Created"])
        self.versions.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.versions.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.versions.itemSelectionChanged.connect(self._select_version)
        layout.addWidget(self.versions, 1)
        actions = QHBoxLayout(); actions.addWidget(self.button("Preview selected", self._play, True)); self.approve_button = self.button("Approve / reject", self._toggle_approval); actions.addWidget(self.approve_button); actions.addWidget(self.button("Delete version", self._delete_version, True)); layout.addLayout(actions)
        self.audio_output = QAudioOutput(self); self.player = QMediaPlayer(self); self.player.setAudioOutput(self.audio_output)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        self.body.addWidget(split, 1)
        self.clips = []; self.current = None; self.version = None

    def refresh(self):
        self.request("GET", "/api/library", callback=self._loaded)

    def _loaded(self, data):
        selected_clip_id = self.current.get("id") if self.current else None
        selected_version_id = self.version.get("id") if self.version else None
        self.clips = _items(data); self.count.setText(f"{len(self.clips)} scripts")
        self.list.setRowCount(0)
        for clip in self.clips:
            row = self.list.rowCount(); self.list.insertRow(row)
            for col, value in enumerate((clip.get("id"), clip.get("category"), clip.get("priority"), len(clip.get("versions", [])))):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, clip); self.list.setItem(row, col, item)
        self.list.resizeColumnsToContents(); self.current = None; self.version = None; self.versions.setRowCount(0)
        selected_row = next((row for row, clip in enumerate(self.clips)
                             if clip.get("id") == selected_clip_id), None)
        if selected_row is not None:
            self.list.selectRow(selected_row)
            if selected_version_id:
                for row in range(self.versions.rowCount()):
                    version = self.versions.item(row, 0).data(Qt.ItemDataRole.UserRole)
                    if version.get("id") == selected_version_id:
                        self.versions.selectRow(row)
                        break
        elif self.list.rowCount():
            self.list.selectRow(0)
        else:
            self.clip_title.setText("Select a script")
            self.script.clear()

    def _select(self):
        rows = self.list.selectedItems()
        if not rows:
            self.current = None
            self.version = None
            self.clip_title.setText("Select a script")
            self.script.clear()
            self.versions.setRowCount(0)
            return
        clip = rows[0].data(Qt.ItemDataRole.UserRole); self.current = clip
        self.clip_title.setText(f"{clip.get('id')}  ·  {clip.get('category')}  ·  P{clip.get('priority')}")
        self.script.setPlainText(clip.get("text", "")); self.versions.setRowCount(0)
        for version in reversed(clip.get("versions", [])):
            row = self.versions.rowCount(); self.versions.insertRow(row)
            matches_voice = version.get("matches_voice", True)
            if not matches_voice:
                status = "Previous voice · approved" if version.get("approved") else "Previous voice"
            else:
                status = "Approved" if version.get("approved") else "Needs review"
            voice_id = version.get("voice", "")
            speech_model = version.get("speech_model", "")
            values = (version.get("id", "")[:12], status, voice_id, speech_model,
                      _time(version.get("timestamp")))
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, version); self.versions.setItem(row, col, item)
                if col in (2, 3) and value:
                    item.setToolTip(str(value))
        if self.versions.rowCount(): self.versions.selectRow(0)

    def _select_version(self):
        rows = self.versions.selectedItems(); self.version = rows[0].data(Qt.ItemDataRole.UserRole) if rows else None
        if hasattr(self, "approve_button"):
            enabled = bool(self.version and self.version.get("matches_voice", True))
            self.approve_button.setEnabled(enabled)
            self.approve_button.setToolTip("Generate a version for the current voice before approving this recording." if self.version and not enabled else "")

    def _clip_path(self, suffix=""):
        return f"/api/library/{quote(str(self.current['id']), safe='')}{suffix}"

    def _after_clip(self, _data): self.refresh()

    def _save(self):
        if not self.current: return
        text = self.script.toPlainText()
        if not 1 <= len(text) <= 500:
            self.api.notify.emit("Script must contain 1–500 characters."); return
        self.request("PUT", self._clip_path(), {"text": text}, self._after_clip)

    def _generate(self):
        if self.current: self.request("POST", self._clip_path("/generate"), {}, self._after_clip)

    def _toggle_approval(self):
        if self.current and self.version and self.version.get("matches_voice", True):
            self.request("POST", self._clip_path("/approve"), {"version_id": self.version["id"], "approved": not self.version.get("approved", False)}, self._after_clip)

    def _delete_version(self):
        if self.current and self.version:
            answer = QMessageBox.question(self, "Delete recording", "Permanently delete this audio version?")
            if answer == QMessageBox.StandardButton.Yes:
                self.request("DELETE", self._clip_path(f"/versions/{quote(self.version['id'], safe='')}"), callback=self._after_clip)

    def _play(self):
        if not self.version: return
        if self.status.get("running"):
            self.api.notify.emit("Stop the engineer before previewing library audio.")
            return
        url = self.api.base_url.rstrip("/") + "/api/audio/" + quote(self.version["id"], safe="")
        token = getattr(self.api, "access_token", "")
        if token:
            url += "?" + urlencode({"access_token": token})
        self.player.setSource(QUrl(url))
        self.player.play()

    def on_status(self):
        if self.status.get("running"):
            self.player.stop()


class TelemetryPage(Page):
    def __init__(self, api):
        super().__init__(api, "Live telemetry", "Values are shown from the latest ACC frame. Stale data is labelled and never presented as live.")
        self.freshness = QLabel("Waiting for ACC telemetry")
        self.freshness.setObjectName("statusCard"); self.body.addWidget(self.freshness)
        self.values = QTextEdit(); self.values.setReadOnly(True); self.values.setObjectName("jsonView")
        self.body.addWidget(self.values, 1)
        self.competitors = QTableWidget(0, 5); self.competitors.setHorizontalHeaderLabels(["Position", "Driver", "Car", "Last lap ms", "Gap ms"])
        self.body.addWidget(self.competitors, 1)

    def on_status(self):
        s = self.status; frame = s.get("telemetry") or {}; age = s.get("telemetry_age_s")
        fresh = s.get("source") == "acc" and frame.get("status") == "live" and isinstance(age, (int, float)) and age < 2
        self.freshness.setText(f"{'FRESH' if fresh else 'STALE / UNAVAILABLE'}  ·  source {s.get('source', 'disconnected')}  ·  age {age:.2f} s" if isinstance(age, (float, int)) else f"STALE / UNAVAILABLE  ·  source {s.get('source', 'disconnected')}  ·  age —")
        shown = frame.copy()
        if not fresh and shown: shown["display_note"] = "Frame is stale; values are historical, not live."
        self.values.setPlainText(_pretty(shown) if shown else "No telemetry frame received.")
        competitors = ((s.get("broadcast") or {}).get("competitors") or [])
        self.competitors.setRowCount(0)
        for c in competitors:
            drivers = c.get("drivers") or []
            driver = ", ".join(d.get("short_name") or " ".join(filter(None, (d.get("first_name"), d.get("last_name")))) for d in drivers) or c.get("current_driver", "—")
            vals = (c.get("position"), driver, c.get("race_number"), (c.get("last_lap") or {}).get("time_ms"), c.get("delta_ms"))
            row = self.competitors.rowCount(); self.competitors.insertRow(row)
            for col, val in enumerate(vals): self.competitors.setItem(row, col, QTableWidgetItem("—" if val is None else str(val)))

    def refresh(self): self.request("GET", "/api/status", callback=lambda d: self._status_changed(d))


class RecordsPage(Page):
    def __init__(self, api, kind):
        title = "AI activity" if kind == "activity" else "Event logs"
        super().__init__(api, title, "Inspect locally recorded backend entries. Sensitive credentials are not displayed.")
        self.kind = kind
        self.table = QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(["Time", "Type", "Summary", "Details"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._inspect)
        self.body.addWidget(self.table, 2)
        self.raw = QTextEdit(); self.raw.setReadOnly(True); self.raw.setObjectName("jsonView"); self.body.addWidget(self.raw, 1)
        self.body.addWidget(self.button("Refresh", self.refresh, True))
        self.records = []

    def refresh(self): self.request("GET", "/api/" + self.kind, callback=self._loaded)

    def _loaded(self, data):
        self.records = _items(data); self.table.setRowCount(0)
        for entry in self.records:
            row = self.table.rowCount(); self.table.insertRow(row)
            summary = entry.get("text") or entry.get("request") or entry.get("response") or entry.get("message") or entry.get("kind") or "Record"
            vals = (_time(entry.get("timestamp")), entry.get("kind", entry.get("type", self.kind)), str(summary)[:120], _pretty(entry)[:1200])
            for col, val in enumerate(vals): self.table.setItem(row, col, QTableWidgetItem(str(val)))
        self.table.resizeColumnsToContents()
        if self.records: self.table.selectRow(0)
        else: self.raw.setPlainText("No records yet.")

    def _inspect(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.records): self.raw.setPlainText(_pretty(self.records[row]))


class ControlsPage(Page):
    def __init__(self, api):
        super().__init__(api, "Controller & keybinds", "Set the configured Xbox button and keyboard toggle, then check for conflicts in ACC manually.")
        self.settings = {}
        form = QFormLayout(); self.button_combo = QComboBox(); self.button_combo.addItems(["R3", "L3", "A", "B", "X", "Y", "LB", "RB", "START", "BACK"])
        self.key_combo = QComboBox(); self.key_combo.addItems([f"<f{i}>" for i in range(1, 25)])
        form.addRow("Controller button", self.button_combo); form.addRow("Keyboard alternative", self.key_combo)
        _, box = self.group("Radio input")
        box.addLayout(form)
        row = QHBoxLayout(); row.addWidget(self.button("Save controls", self.save)); row.addWidget(self.button("Test / toggle radio", lambda: self.request("POST", "/api/radio/toggle", {}, lambda _d: self.refresh()), True)); row.addWidget(self.button("Cancel recording", lambda: self.request("POST", "/api/radio/cancel", {}, lambda _d: self.refresh()), True)); box.addLayout(row)
        self.device_info = QTextEdit(); self.device_info.setReadOnly(True); self.device_info.setMaximumHeight(180); box.addWidget(self.device_info)
        self.body.addStretch(1)

    def refresh(self):
        self.request("GET", "/api/settings", callback=self._settings_loaded)
        self.request("GET", "/api/devices", callback=lambda d: self.device_info.setPlainText(_pretty(d)))

    def _settings_loaded(self, data):
        self.settings = data or {}
        self.button_combo.setCurrentText(self.settings.get("radio_button", "R3"))
        self.key_combo.setCurrentText(self.settings.get("keyboard_binding", "<f9>"))

    def save(self):
        values = {k: v for k, v in self.settings.items() if k in {"model", "speech_model", "voice", "weekly_budget_usd", "request_limit_usd", "speech_speed", "volume", "commentary_frequency", "radio_button", "radio_effect", "keyboard_binding", "follow_up_listening", "recording_timeout_s", "stale_after_s", "setup_root", "setup_profiles", "thresholds", "broadcast_port", "broadcast_password", "broadcast_enabled"}}
        values.update(radio_button=self.button_combo.currentText(), keyboard_binding=self.key_combo.currentText())
        self.request("PUT", "/api/settings", values, lambda _d: self.refresh())


class SettingsPage(Page):
    def __init__(self, api):
        super().__init__(api, "AI & system settings", "Credentials are submitted only when you save them. The key field is cleared after each attempt and never shown again.")
        scroll = QScrollArea(); scroll.setWidgetResizable(True); self.body.addWidget(scroll, 1)
        inner = QWidget(); scroll.setWidget(inner); self.form = QFormLayout(inner); self.form.setContentsMargins(4, 4, 12, 12); self.form.setSpacing(10)
        self.model = QLineEdit(); self.speech_model = QLineEdit(); self.voice = QLineEdit()
        self.budget = self._number(0, 1000, .01, 5); self.limit = self._number(0, 10, .01, .1)
        self.speed = self._number(.5, 2, .05, 1); self.volume = self._number(0, 1, .05, .8)
        self.frequency = QComboBox(); self.frequency.addItems(["off", "low", "normal", "high"])
        self.radio_effect = QComboBox(); self.radio_effect.addItems(["dry", "light", "gritty", "strong"])
        self.follow_up_listening = QCheckBox("Listen for 5 seconds after the engineer asks a question")
        self.timeout = QSpinBox(); self.timeout.setRange(3, 30)
        self.stale = self._number(.1, 10, .1, 2)
        self.port = QSpinBox(); self.port.setRange(1024, 65535)
        self.broadcast_enabled = QCheckBox("Enable ACC broadcasting integration")
        self.broadcast_password = QLineEdit(); self.broadcast_password.setEchoMode(QLineEdit.EchoMode.Password); self.broadcast_password.setPlaceholderText("Leave blank to keep the existing password")
        self.setup_root = QLineEdit(); browse = QPushButton("Browse…"); browse.clicked.connect(self._browse_setup); root_row = QHBoxLayout(); root_row.addWidget(self.setup_root); root_row.addWidget(browse)
        self.thresholds = QTextEdit(); self.thresholds.setMaximumHeight(120)
        self.profiles = QTextEdit(); self.profiles.setMaximumHeight(140)
        self.form.addRow("Reasoning model", self.model); self.form.addRow("Speech model", self.speech_model); self.form.addRow("Engineer voice ID", self.voice)
        self.form.addRow("Weekly budget (USD)", self.budget); self.form.addRow("Per-request limit (USD)", self.limit); self.form.addRow("Speech speed", self.speed); self.form.addRow("Playback volume", self.volume)
        self.form.addRow("Commentary frequency", self.frequency); self.form.addRow("Radio effect", self.radio_effect); self.form.addRow("Recording timeout (seconds)", self.timeout); self.form.addRow("Telemetry stale threshold (seconds)", self.stale)
        self.form.addRow("Follow-up listening", self.follow_up_listening)
        self.form.addRow("Broadcast port", self.port); self.form.addRow("Broadcast password", self.broadcast_password); self.form.addRow("Broadcast", self.broadcast_enabled)
        self.form.addRow("ACC setup directory", root_row); self.form.addRow("Event thresholds (JSON)", self.thresholds); self.form.addRow("Setup profiles (JSON)", self.profiles)
        buttons = QHBoxLayout(); buttons.addWidget(QPushButton("Save settings")); buttons.addWidget(QPushButton("Save OpenRouter key")); self.form.addRow("", buttons)
        buttons.itemAt(0).widget().clicked.connect(self.save); buttons.itemAt(1).widget().clicked.connect(self.save_key)
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.EchoMode.Password); self.key.setPlaceholderText("sk-or-…"); self.form.insertRow(3, "OpenRouter API key", self.key)
        self.api_state = QLabel("Key status: checking…"); self.form.addRow("Credential status", self.api_state)
        self.settings = {}
        self.voice.setPlaceholderText("Blank uses fixed Adrian voice")
        self.voice_test_player = QMediaPlayer(self)
        self.voice_test_output = QAudioOutput(self)
        self.voice_test_player.setAudioOutput(self.voice_test_output)
        self.voice_test_button = QPushButton("Test engineer voice")
        self.prepare_speech_button = QPushButton("Prepare speech recognition")
        self.voice_test_button.clicked.connect(self._test_voice)
        self.prepare_speech_button.clicked.connect(self._prepare_speech)
        voice_actions = QHBoxLayout(); voice_actions.addWidget(self.voice_test_button); voice_actions.addWidget(self.prepare_speech_button)
        self.form.addRow("Speech tools", voice_actions)
        voice_hint = QLabel(f"Blank voice ID uses fixed Fish Official Adrian ({DEFAULT_ENGINEER_VOICE_ID}); speech identity stays stable across generated clips. Voice test uses saved settings. Save settings before testing changes.")
        voice_hint.setWordWrap(True)
        voice_hint.setObjectName("mutedText")
        self.form.addRow("", voice_hint)
        self.speech_status = QLabel("Speech recognition status unavailable")
        self.speech_status.setWordWrap(True)
        self.form.addRow("Speech recognition", self.speech_status)

    @staticmethod
    def _number(minimum, maximum, step, value):
        spin = QDoubleSpinBox(); spin.setRange(minimum, maximum); spin.setSingleStep(step); spin.setDecimals(3); spin.setValue(value); return spin

    def _browse_setup(self):
        path = QFileDialog.getExistingDirectory(self, "Choose ACC setup directory", self.setup_root.text())
        if path: self.setup_root.setText(path)

    def refresh(self): self.request("GET", "/api/settings", callback=self._loaded)

    def _loaded(self, data):
        self.settings = data or {}
        s = self.settings
        for widget, key in ((self.model,"model"),(self.speech_model,"speech_model"),(self.voice,"voice"),(self.budget,"weekly_budget_usd"),(self.limit,"request_limit_usd"),(self.speed,"speech_speed"),(self.volume,"volume"),(self.frequency,"commentary_frequency"),(self.radio_effect,"radio_effect"),(self.timeout,"recording_timeout_s"),(self.stale,"stale_after_s"),(self.port,"broadcast_port"),(self.setup_root,"setup_root")):
            value = s.get(key)
            if value is not None:
                widget.setValue(value) if isinstance(widget, (QSpinBox, QDoubleSpinBox)) else widget.setCurrentText(value) if isinstance(widget, QComboBox) else widget.setText(str(value))
        self.broadcast_enabled.setChecked(bool(s.get("broadcast_enabled")))
        self.follow_up_listening.setChecked(s.get("follow_up_listening") is True)
        self.broadcast_password.clear()
        self.thresholds.setPlainText(_pretty(s.get("thresholds", {})))
        self.profiles.setPlainText(_pretty(s.get("setup_profiles", {})))
        configured = (self.api.status.get("provider") or {}).get("key_configured")
        self.api_state.setText("Key configured" if configured else "No key configured")
        self.on_status()

    def _test_voice(self):
        if self.status.get("running"):
            self.api.notify.emit("Stop the engineer before testing voice playback.")
            return
        self.voice_test_button.setEnabled(False)
        self.api.notify.emit("Generating engineer voice test…")
        self.request("POST", "/api/voice/test", {}, self._voice_ready,
                     finished=lambda: self.voice_test_button.setEnabled(True))

    def _voice_ready(self, data):
        url = (data or {}).get("url")
        if not url or not url.startswith("/api/audio/"):
            self.api.notify.emit("Voice test did not return playable audio.")
            return
        full_url = self.api.base_url.rstrip("/") + url
        token = getattr(self.api, "access_token", "")
        if token:
            full_url += "?" + urlencode({"access_token": token})
        self.voice_test_player.setSource(QUrl(full_url))
        self.voice_test_player.play()
        self.api.notify.emit("Playing engineer voice test.")

    def _prepare_speech(self):
        self.prepare_speech_button.setEnabled(False)
        self.api.notify.emit("Preparing speech recognition model. The first download may take a while…")
        self.request("POST", "/api/radio/prepare", {}, self._speech_prepared,
                     finished=lambda: self.prepare_speech_button.setEnabled(True))

    def _speech_prepared(self, data):
        model = (data or {}).get("model", "speech model")
        if (data or {}).get("ready"):
            self.api.notify.emit(f"Speech recognition is ready ({model}). No microphone was opened.")
            self.speech_status.setText(f"Ready · {model}")
        else:
            self.api.notify.emit("Speech recognition preparation did not complete.")

    def on_status(self):
        if self.status.get("running"):
            self.voice_test_player.stop()
        radio = self.status.get("radio") or {}
        state = radio.get("stt_state")
        if state:
            model = radio.get("stt_model") or radio.get("model")
            detail = {"ready": "Ready", "preparing": "Preparing speech recognition model…",
                      "error": "Preparation failed", "not_prepared": "Not prepared"}.get(state, str(state))
            if state == "error" and radio.get("last_error"):
                detail += f" · {radio['last_error']}"
            if model:
                detail += f" · {model}"
            self.speech_status.setText(detail)

    def _values(self):
        return {"model":self.model.text().strip(),"speech_model":self.speech_model.text().strip(),"voice":self.voice.text().strip() or DEFAULT_ENGINEER_VOICE_ID,
            "weekly_budget_usd":self.budget.value(),"request_limit_usd":self.limit.value(),"speech_speed":self.speed.value(),"volume":self.volume.value(),
            "commentary_frequency":self.frequency.currentText(),"radio_effect":self.radio_effect.currentText(),"follow_up_listening":self.follow_up_listening.isChecked(),"recording_timeout_s":self.timeout.value(),"stale_after_s":self.stale.value(),
            "broadcast_port":self.port.value(),"broadcast_enabled":self.broadcast_enabled.isChecked(),"setup_root":self.setup_root.text().strip(),
            "thresholds":json.loads(self.thresholds.toPlainText() or "{}"),"setup_profiles":json.loads(self.profiles.toPlainText() or "{}"),
            "broadcast_password":self.broadcast_password.text()}

    def save(self):
        try: values = self._values()
        except (ValueError, TypeError) as exc:
            self.api.notify.emit(f"Settings JSON is invalid: {exc}"); return
        if not values["broadcast_password"]: values.pop("broadcast_password")
        self.request("PUT", "/api/settings", values, lambda _d: self.refresh())

    def save_key(self):
        key = self.key.text(); self.key.clear()
        if not key:
            self.api.notify.emit("Enter an OpenRouter key first."); return
        self.request("POST", "/api/credentials", {"api_key": key}, lambda _d: self._key_saved())

    def _key_saved(self):
        self.api_state.setText("Key configured"); self.key.clear(); self.refresh()


class SetupsPage(Page):
    def __init__(self, api):
        super().__init__(api, "Setup manager", "Inspect ACC JSON, then create a new validated copy. Original files are preserved; load the result manually in ACC.")
        top = QHBoxLayout(); self.directory = QLabel("Setup directory not configured"); self.directory.setWordWrap(True); top.addWidget(self.directory, 1); top.addWidget(self.button("Refresh files", self.refresh, True)); self.body.addLayout(top)
        self.files = QComboBox(); self.files.currentIndexChanged.connect(self._file_selected)
        _, box = self.group("Source setup")
        box.addWidget(self.files); box.addWidget(self.button("Inspect selected setup", self.inspect))
        self.inspection = QTextEdit(); self.inspection.setReadOnly(True); self.inspection.setObjectName("jsonView"); box.addWidget(self.inspection, 1)
        _, edit = self.group("Create a copy from an explicitly validated profile")
        self.profile = QComboBox(); self.changes = QTextEdit(); self.changes.setPlainText("{}")
        self.new_name = QLineEdit(); self.new_name.setPlaceholderText("For example: my_setup_v2.json")
        form = QFormLayout(); form.addRow("Validated profile", self.profile); form.addRow("New filename", self.new_name); form.addRow("Parameter changes (JSON)", self.changes); edit.addLayout(form)
        edit.addWidget(self.status_label("Only profiles marked validated by you are accepted. The source must match the profile. Existing files cannot be overwritten."))
        edit.addWidget(self.button("Create verified copy", self.create))
        self.profiles = {}; self.paths = []

    def refresh(self):
        self.request("GET", "/api/settings", callback=self._settings)
        self.request("GET", "/api/setups", callback=self._paths)

    def _settings(self, data):
        s = data or {}; self.directory.setText("ACC setup directory: " + (s.get("setup_root") or "not configured")); self.profiles = s.get("setup_profiles") or {}
        self.profile.clear(); self.profile.addItems(list(self.profiles.keys()))

    def _paths(self, data):
        self.paths = _items(data); old = self.files.currentText(); self.files.clear(); self.files.addItems(self.paths)
        if old in self.paths: self.files.setCurrentText(old)

    def _file_selected(self, _index):
        if self.files.currentText(): self.new_name.setText(self.files.currentText().rsplit(".",1)[0] + "_copy.json")

    def inspect(self):
        path = self.files.currentText()
        if not path: self.api.notify.emit("No setup file available. Configure the ACC setup directory first."); return
        self.request("POST", "/api/setups/inspect", {"path":path}, lambda d:self.inspection.setPlainText(_pretty(d)))

    def create(self):
        try: changes = json.loads(self.changes.toPlainText() or "{}")
        except ValueError as exc: self.api.notify.emit(f"Changes JSON is invalid: {exc}"); return
        name = self.new_name.text().strip()
        if name and not name.lower().endswith(".json"): name += ".json"
        payload = {"source":self.files.currentText(),"name":name,"changes":changes,"profile_name":self.profile.currentText()}
        self.request("POST", "/api/setups/create", payload, lambda d:self.inspection.setPlainText(_pretty(d)))


class DebugPage(Page):
    def __init__(self, api):
        super().__init__(api, "Debug & replay", "Simulated events are labelled as tests. Telemetry replay is accepted only while the engineer is stopped.")
        _, tests = self.group("Local event tests")
        for kind, label in (("yellow","Yellow flag"),("damage","Damage"),("fuel","Low fuel"),("lap","Personal best lap")):
            tests.addWidget(self.button("Inject test: " + label, lambda _checked=False, k=kind:self.inject_event(k), True))
        _, replay = self.group("Telemetry replay")
        self.frames = QTextEdit(); self.frames.setPlaceholderText('Paste an array of telemetry frame objects, e.g. [{"speed_kmh": 0, "fuel_l": 50}]')
        self.frames.setPlainText("[]"); replay.addWidget(self.frames)
        replay.addWidget(self.button("Run isolated replay", self.run_replay))
        self.result = QTextEdit(); self.result.setReadOnly(True); self.result.setObjectName("jsonView"); self.body.addWidget(self.result, 1)
        self.body.addWidget(self.button("Refresh backend status", self.refresh, True))

    def inject_event(self, kind):
        self.request("POST", "/api/debug/event", {"kind":kind}, lambda d:self.result.setPlainText(_pretty(d)))

    def run_replay(self):
        try: frames = json.loads(self.frames.toPlainText())
        except ValueError as exc: self.api.notify.emit(f"Frame JSON is invalid: {exc}"); return
        if not isinstance(frames, list) or not frames:
            self.api.notify.emit("Provide at least one telemetry frame."); return
        self.request("POST", "/api/debug/replay", {"frames":frames}, lambda d:self.result.setPlainText(_pretty(d)))

    def refresh(self): self.request("GET", "/api/status", callback=lambda d:self._status_changed(d))


def create_pages(api):
    """Return native tab widgets keyed by labels used by the desktop shell."""
    return {
        "Overview": OverviewPage(api),
        "Personal engineer": PersonalEngineerPage(api),
        "Radio": RadioPage(api),
        "Radio library": LibraryPage(api),
        "Live telemetry": TelemetryPage(api),
        "AI activity": RecordsPage(api, "activity"),
        "Event logs": RecordsPage(api, "events"),
        "Controller & keybinds": ControlsPage(api),
        "AI settings": SettingsPage(api),
        "Setup manager": SetupsPage(api),
        "Debug & replay": DebugPage(api),
    }
