"""Explicit, foreground-ACC-only screenshot capture for optional vision models."""
from __future__ import annotations

import base64
import io
import os


def _acc_window_region():
    """Return (foreground HWND, primary-monitor client rect) for ACC, else None."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    hwnd = user32.GetForegroundWindow()
    if not hwnd or user32.IsIconic(hwnd):
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return False
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        query = kernel32.QueryFullProcessImageNameW
        if not query(handle, 0, buf, ctypes.byref(size)):
            return False
        if os.path.basename(buf.value).casefold() not in {"ac2-win64-shipping.exe", "acc.exe"}:
            return None
    finally:
        kernel32.CloseHandle(handle)

    # Restrict capture to a single primary monitor and to the game's visible
    # client area. Never fall back to a full-screen grab.
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    top_left, bottom_right = wintypes.POINT(rect.left, rect.top), wintypes.POINT(rect.right, rect.bottom)
    if not user32.ClientToScreen(hwnd, ctypes.byref(top_left)) or not user32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
        return None
    left, top, right, bottom = top_left.x, top_left.y, bottom_right.x, bottom_right.y
    width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    if (width <= 0 or height <= 0 or user32.GetSystemMetrics(80) != 1
            or left < 0 or top < 0 or right > width or bottom > height or right <= left or bottom <= top):
        return None
    return hwnd, (left, top, right, bottom)


def capture_data_url() -> str | None:
    """Capture a bounded JPEG only when explicitly called with ACC foreground.

    No background capture, persistence, or upload is performed here. The caller
    must make any subsequent model request explicit.
    """
    if os.name != "nt":
        return None
    initial = _acc_window_region()
    if initial is None:
        return None
    try:
        import dxcam
        from PIL import Image
        camera = dxcam.create(output_color="RGB")
        try:
            frame = camera.grab(region=initial[1])
        finally:
            camera.stop()
        current = _acc_window_region()
        if current is None or current[0] != initial[0]:
            return None
        if frame is None:
            return None
        image = Image.fromarray(frame)
        image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=82, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")
    except (ImportError, OSError, RuntimeError, ValueError):
        return None
