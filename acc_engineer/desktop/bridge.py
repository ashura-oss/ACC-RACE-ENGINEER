"""Private backend ownership and nonblocking native-to-engine communication."""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import secrets

from PySide6.QtCore import QObject, QTimer, Signal, QUrl, QByteArray
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply


class NativeBackend(QObject):
    ready = Signal(str)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, data_dir=None, parent=None):
        super().__init__(parent)
        self.data_dir = data_dir
        self._thread = None
        self._server = None
        self._loop = None
        self._closing = threading.Event()
        self.access_token = secrets.token_urlsafe(32)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._closing.clear()
        self._thread = threading.Thread(target=self._worker, name="ACC engine", daemon=True)
        self._thread.start()

    def _worker(self):
        try:
            asyncio.run(self._serve())
        except Exception:
            self.failed.emit("The local engineer could not start. Check application data permissions and restart.")
        finally:
            self.stopped.emit()

    async def _serve(self):
        import uvicorn
        from ..app import create_app
        self._loop = asyncio.get_running_loop()
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Every app instance owns its server; never reuse an unrelated process
            # listening on a fixed port or ask the user to manage a browser server.
            server_socket.bind(("127.0.0.1", 0))
            server_socket.setblocking(False)
            port = server_socket.getsockname()[1]
            application = create_app(self.data_dir, api_token=self.access_token)
            config = uvicorn.Config(application, log_config=None, access_log=False,
                                    timeout_graceful_shutdown=10)
            self._server = uvicorn.Server(config)
            serving = asyncio.create_task(self._server.serve(sockets=[server_socket]))
            while not self._server.started and not serving.done():
                if self._closing.is_set():
                    self._server.should_exit = True
                await asyncio.sleep(.02)
            if self._server.started and not self._closing.is_set():
                self.ready.emit(f"http://127.0.0.1:{port}")
            await serving
        finally:
            server_socket.close()
            self._loop = None

    def close(self):
        self._closing.set()
        if self._server:
            self._server.should_exit = True

    def wait(self, timeout=15):
        if self._thread:
            self._thread.join(timeout)
        return not self._thread or not self._thread.is_alive()


class EngineerAPI(QObject):
    status_changed = Signal(dict)
    notify = Signal(str)
    connection_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.base_url = ""
        self.access_token = ""
        self.status = {}
        self.manager = QNetworkAccessManager(self)
        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.poll)
        self._polling = False
        self._replies = set()

    def connect_backend(self, base_url):
        self.base_url = base_url
        self.poll()
        self.timer.start()

    def poll(self):
        if self._polling or not self.base_url:
            return
        self._polling = True

        def received(data):
            self.status = data
            self.connection_changed.emit(True)
            self.status_changed.emit(data)
        self.request("GET", "/api/status", callback=received, quiet=True,
                     finished=lambda: setattr(self, "_polling", False))

    def request(self, method, path, body=None, callback=None, quiet=False, finished=None):
        if not self.base_url:
            if not quiet:
                self.notify.emit("The engineer is starting. Please wait.")
            if finished:
                finished()
            return None
        if not path.startswith("/api/") or "://" in path:
            raise ValueError("Only local engineer API paths are supported")
        request = QNetworkRequest(QUrl(self.base_url + path))
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        if self.access_token:
            request.setRawHeader(QByteArray(b"X-ACC-Token"), QByteArray(self.access_token.encode()))
        # Speech and local first-run transcription may take longer than ordinary UI requests.
        request.setTransferTimeout(120000)
        payload = QByteArray(json.dumps(body).encode()) if body is not None else QByteArray()
        reply = self.manager.sendCustomRequest(request, method.upper().encode(), payload)
        self._replies.add(reply)

        def complete():
            try:
                status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
                raw = bytes(reply.readAll())
                try:
                    data = json.loads(raw) if raw else {}
                except (ValueError, UnicodeDecodeError):
                    data = {}
                if reply.error() != QNetworkReply.NoError or not status or status >= 400:
                    if not quiet:
                        detail = data.get("detail", "Local engine request failed. Please retry.") if isinstance(data, dict) else "Local engine request failed."
                        if isinstance(detail, list):
                            detail = "; ".join(str(error.get("msg", "Invalid setting")) for error in detail)
                        self.notify.emit(str(detail))
                    self.connection_changed.emit(False if not status else True)
                elif callback:
                    callback(data)
            except Exception:
                self.notify.emit("Could not display the engineer response. Please retry.")
            finally:
                self._replies.discard(reply)
                if finished:
                    finished()
                reply.deleteLater()
        reply.finished.connect(complete)
        return reply

    def stop(self):
        self.timer.stop()
        for reply in list(self._replies):
            reply.abort()
