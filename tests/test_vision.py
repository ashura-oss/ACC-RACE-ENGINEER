import sys
from types import ModuleType, SimpleNamespace

import acc_engineer.vision as vision


def test_rejects_non_acc_foreground_before_capture(monkeypatch):
    monkeypatch.setattr(vision.os, "name", "nt")
    monkeypatch.setattr(vision, "_acc_window_region", lambda: None)
    monkeypatch.setattr(vision, "_capture", lambda *a, **k: (_ for _ in ()).throw(AssertionError("capture attempted")), raising=False)
    assert vision.capture_data_url() is None


def test_focus_switch_discards_frame(monkeypatch):
    monkeypatch.setattr(vision.os, "name", "nt")
    regions = iter([(10, (1, 2, 101, 102)), (11, (1, 2, 101, 102))])
    monkeypatch.setattr(vision, "_acc_window_region", lambda: next(regions))
    camera = SimpleNamespace(grab=lambda **kw: object(), stop=lambda: None)
    dxcam = ModuleType("dxcam")
    dxcam.create = lambda **kw: camera
    pil = ModuleType("PIL")
    pil_image = ModuleType("PIL.Image")
    pil_image.Resampling = SimpleNamespace(LANCZOS=1)
    pil_image.fromarray = lambda frame: (_ for _ in ()).throw(AssertionError("focus-switched frame encoded"))
    pil.Image = pil_image
    monkeypatch.setitem(sys.modules, "dxcam", dxcam)
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", pil_image)
    assert vision.capture_data_url() is None


def test_capture_uses_only_client_region(monkeypatch):
    monkeypatch.setattr(vision.os, "name", "nt")
    monkeypatch.setattr(vision, "_acc_window_region", lambda: (42, (10, 20, 210, 120)))
    calls = {}

    class Camera:
        def grab(self, **kwargs):
            calls["grab"] = kwargs
            return object()
        def stop(self):
            calls["stopped"] = True

    class Image:
        Resampling = SimpleNamespace(LANCZOS=1)
        def thumbnail(self, *a): pass
        def save(self, out, **kwargs): out.write(b"jpeg")

    dxcam = ModuleType("dxcam")
    dxcam.create = lambda **kwargs: Camera()
    pil = ModuleType("PIL")
    pil_image = ModuleType("PIL.Image")
    pil_image.fromarray = lambda frame: Image()
    pil_image.Resampling = SimpleNamespace(LANCZOS=1)
    pil.Image = pil_image
    monkeypatch.setitem(sys.modules, "dxcam", dxcam)
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", pil_image)
    result = vision.capture_data_url()
    assert calls["grab"] == {"region": (10, 20, 210, 120)}
    assert calls["stopped"]
    assert result.startswith("data:image/jpeg;base64,")
