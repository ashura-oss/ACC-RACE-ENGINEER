"""Headless smoke and interaction tests for the native Qt desktop shell."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea, QSplitter, QWidget

from acc_engineer.desktop.shell import EngineerWindow


class FakeAPI(QObject):
    status_changed = Signal(dict)
    notify = Signal(str)
    connection_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.base_url = ""
        self.status = {}
        self.calls = []
        self.stopped = False

    def request(self, method, path, body=None, callback=None, **_kwargs):
        self.calls.append((method, path, body, callback))

    def stop(self):
        self.stopped = True

    def connect_backend(self, base_url):
        self.base_url = base_url


class FakeBackend(QObject):
    ready = Signal(str)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.closed = False

    def close(self):
        self.closed = True

    def wait(self, _timeout=15):
        return True


@pytest.fixture
def window(qapp):
    api = FakeAPI()
    instance = EngineerWindow(api)
    yield instance, api
    instance.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    instance.close()
    qapp.processEvents()


def test_desktop_has_eleven_real_native_pages(window):
    instance, api = window
    assert api.calls == []  # Building widgets must not inject debug events or call the backend.
    assert len(instance.pages) == 11
    assert len({id(page) for page in instance.pages.values()}) == 11
    assert all(isinstance(page, QWidget) for page in instance.pages.values())
    assert instance.navigation.count() == 11
    assert instance.stack.count() == 11


def test_library_splitter_keeps_both_clip_list_and_editor_visible(window, qapp):
    instance, _api = window
    instance.show()
    library_index = instance.navigation.row(instance.navigation.findItems(
        "Radio library", Qt.MatchFlag.MatchExactly)[0])
    instance.navigation.setCurrentRow(library_index)
    qapp.processEvents()
    page = instance.pages["Radio library"]
    splitter = page.findChild(QSplitter)
    assert splitter is not None
    assert splitter.count() == 2
    assert splitter.widget(0).isVisible()
    assert splitter.widget(1).isVisible()
    assert any(button.text() == "Save script" and button.isVisible()
               for button in page.findChildren(QPushButton))


def test_personal_engineer_inputs_keep_minimum_size_in_compact_window(window, qapp):
    instance, _api = window
    instance.resize(1280, 800)
    instance.show()
    personal_index = instance.navigation.row(instance.navigation.findItems(
        "Personal engineer", Qt.MatchFlag.MatchExactly)[0])
    instance.navigation.setCurrentRow(personal_index)
    qapp.processEvents()

    page = instance.pages["Personal engineer"]
    scroll = page.findChild(QScrollArea)
    assert scroll is not None and scroll.isVisible()
    assert page.driver_name.height() >= 32
    assert page.encouragement.height() >= 32
    assert page.profanity.height() >= 30
    assert page.driving_preferences.height() >= 80
    content = scroll.widget()
    assert content is not None and content.layout() is not None
    assert content.layout().sizeConstraint() == content.layout().SizeConstraint.SetMinimumSize
    assert content.minimumHeight() >= content.layout().minimumSize().height()
    assert content.height() >= content.minimumHeight()


def test_overview_controls_send_expected_local_api_requests(window):
    instance, api = window
    page = instance.pages["Overview"]
    api.status_changed.emit({"running": False, "muted": False})
    page.start_btn.click()
    assert api.calls[-1][:3] == ("POST", "/api/engineer/start", {})

    api.status_changed.emit({"running": True, "muted": False})
    page.stop_btn.click()
    assert api.calls[-1][:3] == ("POST", "/api/engineer/stop", {})
    page.mute_btn.click()
    assert api.calls[-1][:3] == ("POST", "/api/engineer/mute", {"muted": True})


def test_settings_page_only_reads_settings_until_user_saves(window):
    instance, api = window
    api.calls.clear()
    api.base_url = "http://127.0.0.1:12345"
    instance.navigation.setCurrentRow(instance.navigation.row(instance.navigation.findItems(
        "AI settings", Qt.MatchFlag.MatchExactly)[0]))
    assert [(method, path) for method, path, _, _ in api.calls] == [("GET", "/api/settings")]

    settings = instance.pages["AI settings"]
    settings.key.setText("sk-or-test-value")
    settings.save_key()
    assert settings.key.text() == ""
    method, path, body, _callback = api.calls[-1]
    assert (method, path) == ("POST", "/api/credentials")
    assert body == {"api_key": "sk-or-test-value"}


def test_settings_voice_test_clarifies_it_uses_saved_settings(window):
    instance, _api = window
    page = instance.pages["AI settings"]
    assert page.voice_test_button.text() == "Test engineer voice"
    assert "Adrian" in page.voice.placeholderText()
    assert any("Save settings before testing changes" in label.text()
               for label in page.findChildren(QWidget) if hasattr(label, "text"))


def test_settings_shows_radio_stt_state_and_error(window):
    instance, api = window
    page = instance.pages["AI settings"]
    api.status_changed.emit({"radio": {"stt_state": "error", "stt_model": "base.en",
                                        "last_error": "Model download failed"}})
    assert "Preparation failed" in page.speech_status.text()
    assert "Model download failed" in page.speech_status.text()
    assert "base.en" in page.speech_status.text()


def test_personal_engineer_profile_memory_and_strategy_use_readable_api_contract(window):
    instance, api = window
    page = instance.pages["Personal engineer"]
    page.refresh()
    assert [(method, path) for method, path, _body, _callback in api.calls[-4:]] == [
        ("GET", "/api/driver/profile"), ("GET", "/api/driver/memory"),
        ("GET", "/api/strategy"), ("GET", "/api/status"),
    ]
    page._profile_loaded({"driver_name": "Ari", "encouragement": "high", "profanity": True,
                          "driving_preferences": ["smooth inputs", "late braking"]})
    assert page.driver_name.text() == "Ari"
    assert page.encouragement.currentText() == "high"
    assert page.profanity.isChecked()
    assert page.driving_preferences.toPlainText() == "smooth inputs\nlate braking"

    page._memory_loaded({"profile": {"driver_name": "Ari"}, "recent_sessions": [
        {"track": "Spa", "car": "GT3", "laps": 18, "position": 4},
    ]})
    assert page.sessions.text() == "Spa · GT3 · 18 laps · P4"
    page._strategy_loaded({"status": "unknown", "unknown_reason": "Not enough current fuel samples."})
    assert "Unknown: Not enough current fuel samples." in page.strategy_state.text()
    assert "{" not in page.strategy_state.text()
    page._strategy_loaded({"status": "measured", "actions": [{"call": "Pit in this lap", "reason": "Fuel margin is below the reserve."}],
                           "limits": "No verified pit-loss model."})
    assert "Pit in this lap" in page.strategy_state.text()
    assert "Fuel margin is below the reserve." in page.strategy_state.text()
    assert "No verified pit-loss model." in page.strategy_state.text()

    page.driver_name.setText("Ari")
    page.encouragement.setCurrentText("brief")
    page.profanity.setChecked(False)
    page.driving_preferences.setPlainText("late braking\nsmooth inputs")
    page.save_preferences()
    method, path, body, _callback = api.calls[-1]
    assert (method, path) == ("PUT", "/api/driver/profile")
    assert body == {"driver_name": "Ari", "encouragement": "brief", "profanity": False,
                    "driving_preferences": ["late braking", "smooth inputs"]}


def test_settings_radio_effect_and_follow_up_values_round_trip(window):
    instance, api = window
    page = instance.pages["AI settings"]
    assert page.follow_up_listening.isChecked() is False
    page._loaded({"model": "openai/gpt-test", "speech_model": "fish-audio/drama-3", "voice": "voice",
                  "radio_effect": "gritty", "follow_up_listening": True})
    assert page.radio_effect.currentText() == "gritty"
    assert page.follow_up_listening.isChecked() is True
    values = page._values()
    assert values["radio_effect"] == "gritty"
    assert values["follow_up_listening"] is True
    page.save()
    method, path, body, _callback = api.calls[-1]
    assert (method, path) == ("PUT", "/api/settings")
    assert body["radio_effect"] == "gritty"
    assert body["follow_up_listening"] is True


def test_controls_save_preserves_radio_effect(window):
    instance, api = window
    page = instance.pages["Controller & keybinds"]
    page._settings_loaded({"radio_button": "R3", "keyboard_binding": "<f9>", "radio_effect": "strong",
                           "follow_up_listening": False})
    page.button_combo.setCurrentText("A")
    page.save()
    method, path, body, _callback = api.calls[-1]
    assert (method, path) == ("PUT", "/api/settings")
    assert body["radio_button"] == "A"
    assert body["radio_effect"] == "strong"
    assert body["follow_up_listening"] is False


def test_library_refresh_preserves_clip_and_version_then_clears_stale_selection(window):
    instance, _api = window
    page = instance.pages["Radio library"]
    clip_a = {"id": "clip-a", "category": "test", "priority": 1, "text": "A",
              "versions": [{"id": "v-a1", "timestamp": 1, "approved": False},
                           {"id": "v-a2", "timestamp": 2, "approved": True}]}
    clip_b = {"id": "clip-b", "category": "test", "priority": 2, "text": "B", "versions": []}
    page._loaded([clip_a, clip_b])
    page.list.selectRow(0)
    page.versions.selectRow(1)
    assert page.current["id"] == "clip-a"
    assert page.version["id"] == "v-a1"
    page._loaded([clip_a, clip_b])
    assert page.current["id"] == "clip-a"
    assert page.version["id"] == "v-a1"

    page.list.clearSelection()
    assert page.current is None
    assert page.version is None
    page._toggle_approval()
    page._delete_version()
    assert not any(path.endswith("/approve") or "/versions/" in path
                   for _method, path, _body, _callback in _api.calls)

    page._loaded([clip_a])
    assert page.current["id"] == "clip-a"
    assert page.version["id"] == "v-a2"  # Most recent version is selected initially.
    page._loaded([clip_a])
    assert page.version["id"] == "v-a2"


def test_native_window_close_stops_api_and_owned_backend(qapp):
    api = FakeAPI()
    backend = FakeBackend()
    instance = EngineerWindow(api, backend)
    instance.show()
    instance.close()
    qapp.processEvents()
    QTest.qWait(120)
    qapp.processEvents()
    assert api.stopped
    assert backend.closed
    assert not instance.isVisible()


def test_overview_live_status_shows_fuel_and_model(window):
    instance, api = window
    api.status_changed.emit({
        "running": True,
        "source": "acc",
        "telemetry_age_s": 0.4,
        "telemetry": {"status": "live", "fuel_l": 42.7},
        "provider": {"model": "openai/gpt-test"},
    })
    details = instance.pages["Overview"].details.text()
    assert "42.7 L" in details
    assert "openai/gpt-test" in details


def test_native_backend_owns_private_authenticated_loopback_server(tmp_path, qapp):
    import httpx
    import time

    from acc_engineer.desktop.bridge import NativeBackend

    backend = NativeBackend(tmp_path)
    ready = []
    failures = []
    backend.ready.connect(ready.append)
    backend.failed.connect(failures.append)
    backend.start()
    try:
        # Yield the GIL as well as pumping Qt; uvicorn starts on a Python thread.
        for _ in range(1000):
            qapp.processEvents()
            if ready or failures:
                break
            time.sleep(0.01)
        assert not failures
        assert len(ready) == 1, f"backend state: thread={backend._thread.is_alive()}, started={getattr(backend._server, 'started', None)}"
        assert ready[0].startswith("http://127.0.0.1:")
        response = httpx.get(ready[0] + "/api/status", headers={"X-ACC-Token": backend.access_token}, timeout=3)
        assert response.status_code == 200
        assert "running" in response.json()
    finally:
        backend.close()
        assert backend.wait(5)


def test_native_token_required_on_api_and_query_auth_is_audio_only(tmp_path):
    from fastapi.testclient import TestClient

    from acc_engineer.app import create_app

    token = "private-native-test-token"
    app = create_app(tmp_path, api_token=token)
    audio_dir = app.state.runtime.store.root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    import wave
    with wave.open(str(audio_dir / "abc123.wav"), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 2400)
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 403
        assert client.get("/api/status", params={"access_token": token}).status_code == 403
        assert client.get("/api/status", headers={"X-ACC-Token": token}).status_code == 200
        assert client.get("/api/audio/abc123", params={"access_token": token}).status_code == 200
        assert client.get("/api/audio/abc123").status_code == 403

