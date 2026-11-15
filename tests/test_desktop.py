"""Headless smoke and interaction tests for the native Qt desktop shell."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QSplitter, QWidget

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


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    api = FakeAPI()
    instance = EngineerWindow(api)
    yield instance, api
    instance.close()
    qapp.processEvents()


def test_desktop_has_ten_real_native_pages(window):
    instance, api = window
    assert api.calls == []  # Building widgets must not inject debug events or call the backend.
    assert len(instance.pages) == 10
    assert len({id(page) for page in instance.pages.values()}) == 10
    assert all(isinstance(page, QWidget) for page in instance.pages.values())
    assert instance.navigation.count() == 10
    assert instance.stack.count() == 10


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
    (audio_dir / "abc123.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 403
        assert client.get("/api/status", params={"access_token": token}).status_code == 403
        assert client.get("/api/status", headers={"X-ACC-Token": token}).status_code == 200
        assert client.get("/api/audio/abc123", params={"access_token": token}).status_code == 200
        assert client.get("/api/audio/abc123").status_code == 403

