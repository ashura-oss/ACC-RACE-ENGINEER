"""Native Qt application entry point. No embedded browser or browser launcher."""
from __future__ import annotations

import sys
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QLockFile
from PySide6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
    QVBoxLayout, QLabel, QListWidget, QStackedWidget, QFrame, QMessageBox)

from .bridge import EngineerAPI, NativeBackend
from .pages import create_pages


STYLE = """
QWidget { background: #101315; color: #e8edf0; font-family: 'Segoe UI'; font-size: 10pt; }
QMainWindow { background: #101315; }
QFrame#sidebar { background: #151a1d; border-right: 1px solid #2b3338; }
QLabel#brand { font-size: 18pt; font-weight: 800; color: #ff7849; }
QLabel#subtitle { color: #849198; font-size: 9pt; }
QLabel#pageTitle { font-size: 24pt; font-weight: 700; }
QLabel#engineState { color: #cce15b; padding: 8px; }
QListWidget { border: none; background: #151a1d; outline: none; }
QListWidget::item { padding: 14px 12px; margin: 3px; border-radius: 5px; }
QListWidget::item:selected { background: #3c2a22; color: #ff976f; border-left: 3px solid #ff7849; }
QListWidget::item:hover { background: #242d32; }
QPushButton { background: #ff7849; color: #111315; border: none; border-radius: 5px; padding: 10px 15px; font-weight: 600; min-height: 18px; }
QPushButton:hover { background: #ff986f; }
QPushButton:disabled { background: #333c40; color: #829095; }
QPushButton[secondary="true"] { background: #252e33; color: #e8edf0; border: 1px solid #39454b; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #191f23; border: 1px solid #38434a; border-radius: 4px; padding: 7px; selection-background-color: #734128; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus { border-color: #ff7849; }
QGroupBox { border: 1px solid #303a40; border-radius: 7px; margin-top: 17px; padding: 18px 12px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 7px; color: #d8e0e4; }
QTableWidget, QTreeWidget, QListView { background: #151a1d; alternate-background-color: #1c2327; border: 1px solid #303a40; gridline-color: #2c353b; selection-background-color: #46332b; }
QHeaderView::section { background: #252e33; padding: 8px; border: none; color: #abb8bf; font-weight: 600; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #171d20; width: 10px; }
QScrollBar::handle:vertical { background: #46545c; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QStatusBar { background: #151a1d; color: #a6b6be; border-top: 1px solid #2b3338; }
QCheckBox { spacing: 8px; padding: 5px; }
QTabBar::tab { background: #20292e; padding: 10px; }
QTabBar::tab:selected { color: #ff976f; }
QToolTip { background: #29343a; color: #fff; border: 1px solid #53646f; }
"""


class EngineerWindow(QMainWindow):
    def __init__(self, api, backend=None):
        super().__init__()
        self.api = api
        self.backend = backend
        self._closing = False
        self.setWindowTitle("ACC Race Engineer")
        self.setMinimumSize(850, 620)
        self.resize(1280, 820)
        self.setStyleSheet(STYLE)
        self.setWindowIcon(application_icon())
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 28, 16, 16)
        brand = QLabel("ACC  ENGINEER")
        brand.setObjectName("brand")
        sub = QLabel("YOUR SECOND PAIR OF EYES")
        sub.setObjectName("subtitle")
        side.addWidget(brand)
        side.addWidget(sub)
        side.addSpacing(28)
        self.navigation = QListWidget()
        self.navigation.setAccessibleName("Engineer pages")
        side.addWidget(self.navigation, 1)
        self.connection = QLabel("Starting local engine…")
        self.connection.setWordWrap(True)
        side.addWidget(self.connection)
        native = QLabel("WINDOWS DESKTOP  •  V0.2")
        native.setObjectName("subtitle")
        side.addWidget(native)
        layout.addWidget(sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(28, 24, 28, 18)
        self.heading = QLabel("Race control")
        self.heading.setObjectName("pageTitle")
        self.engine_state = QLabel("Engineer stopped • no ACC connection")
        self.engine_state.setObjectName("engineState")
        main.addWidget(self.heading)
        main.addWidget(self.engine_state)
        self.stack = QStackedWidget()
        self.pages = create_pages(api)
        for name, page in self.pages.items():
            self.navigation.addItem(name)
            self.stack.addWidget(page)
        main.addWidget(self.stack, 1)
        layout.addLayout(main, 1)
        self.navigation.currentRowChanged.connect(self.select_page)
        self.navigation.setCurrentRow(0)
        api.status_changed.connect(self.update_status)
        api.notify.connect(self.show_notice)
        api.connection_changed.connect(lambda connected: self.connection.setText(
            "● Local engine connected" if connected else "Connecting to local engine…"))
        self.statusBar().showMessage("Local processing • credentials stored in Windows Credential Manager")
        if backend:
            backend.ready.connect(api.connect_backend)
            backend.ready.connect(lambda _: self.refresh_current())
            backend.failed.connect(self.show_notice)

    def select_page(self, index):
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        self.heading.setText(self.navigation.item(index).text())
        self.refresh_current()

    def refresh_current(self):
        page = self.stack.currentWidget()
        if self.api.base_url and page and hasattr(page, "refresh"):
            page.refresh()

    def update_status(self, status):
        running = status.get("running", False)
        age = status.get("telemetry_age_s")
        live = status.get("source") == "acc" and age is not None and age < 2
        self.engine_state.setText(("ENGINEER ACTIVE" if running else "ENGINEER STOPPED") +
            ("   •   ACC LIVE" if live else "   •   WAITING FOR ACC") +
            ("   •   MICROPHONE RECORDING" if status.get("radio", {}).get("state") == "recording" else ""))

    def show_notice(self, message):
        self.statusBar().showMessage(str(message), 15000)
        # Persistent, visible error feedback without modal dialogs blocking driving.
        self.connection.setText(str(message))

    def closeEvent(self, event):
        if self._closing or not self.backend:
            event.accept()
            return
        event.ignore()
        self._closing = True
        self.setEnabled(False)
        self.engine_state.setText("Closing radio and local engineer…")
        self.api.stop()
        self.backend.close()
        # Keep the native event loop responsive while lifespan cancels microphone,
        # telemetry and provider requests and closes its private server.
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setInterval(100)
        self._shutdown_timer.timeout.connect(self._finish_close)
        self._shutdown_timer.start()

    def _finish_close(self):
        if self.backend.wait(0):
            self._shutdown_timer.stop()
            self.close()


def application_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#161b1f"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#ff7849"))
    font = QFont("Segoe UI", 23, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "AE")
    painter.end()
    return QIcon(pixmap)


def run():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("ACC Race Engineer")
    app.setOrganizationName("ACC Race Engineer")
    app.setStyle("Fusion")
    data_root = Path(os.getenv("ACC_ENGINEER_DATA", Path(os.getenv("LOCALAPPDATA", ".")) / "ACC Race Engineer"))
    data_root.mkdir(parents=True, exist_ok=True)
    instance_lock = QLockFile(str(data_root / "desktop.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(0):
        QMessageBox.information(None, "ACC Race Engineer", "ACC Race Engineer is already open. Select its window from the taskbar.")
        return 0
    api = EngineerAPI()
    backend = NativeBackend()
    api.access_token = backend.access_token
    window = EngineerWindow(api, backend)
    app.setWindowIcon(window.windowIcon())
    window.show()
    backend.start()
    app.aboutToQuit.connect(api.stop)
    app.aboutToQuit.connect(backend.close)
    result = app.exec()
    backend.close()
    backend.wait()
    instance_lock.unlock()
    return result
