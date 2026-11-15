"""Shared Qt application lifetime for every headless UI test module."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


_SESSION_QAPP = None


@pytest.fixture(scope="session")
def qapp():
    """Keep exactly one QApplication alive until all Qt tests have torn down."""
    global _SESSION_QAPP
    if _SESSION_QAPP is None:
        _SESSION_QAPP = QApplication.instance() or QApplication([])
    return _SESSION_QAPP
