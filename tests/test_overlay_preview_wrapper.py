import logging

import pytest

from doubaoime_asr.agent import overlay_preview


class _BrokenNative:
    def __init__(self, *, logger=None):
        self.logger = logger

    def start(self) -> None:
        raise RuntimeError("boom")


class _LegacyBackend:
    def __init__(self):
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def start(self) -> None:
        self.calls.append(("start", ()))

    def configure(self, config) -> None:
        self.calls.append(("configure", (config,)))

    def show(
        self,
        text: str,
        *,
        seq: int = 0,
        kind: str = "interim",
        stable_prefix_utf16_len: int = 0,
        show_microphone: bool = False,
        level: float = 0.0,
    ) -> None:
        self.calls.append(("show", (text, seq, kind, stable_prefix_utf16_len, show_microphone, level)))

    def hide(self, reason: str = "") -> None:
        self.calls.append(("hide", (reason,)))

    def stop(self) -> None:
        self.calls.append(("stop", ()))


def test_overlay_preview_falls_back_to_tk(monkeypatch):
    legacy_backend = _LegacyBackend()
    monkeypatch.setattr(overlay_preview, "OverlayPreviewCpp", _BrokenNative)
    monkeypatch.setattr(overlay_preview, "TkOverlayPreview", lambda: legacy_backend)

    preview = overlay_preview.OverlayPreview(logging.getLogger("overlay-test"))
    preview.start()
    preview.show("hello")

    assert legacy_backend.calls[0][0] == "start"
    assert legacy_backend.calls[1][0] == "configure"
    assert legacy_backend.calls[2] == ("show", ("hello", 0, "interim", 0, False, 0.0))


def test_overlay_preview_forwards_recording_hud_to_tk_fallback(monkeypatch):
    legacy_backend = _LegacyBackend()
    monkeypatch.setattr(overlay_preview, "OverlayPreviewCpp", _BrokenNative)
    monkeypatch.setattr(overlay_preview, "TkOverlayPreview", lambda: legacy_backend)

    preview = overlay_preview.OverlayPreview(logging.getLogger("overlay-test"))
    preview.start()
    preview.show("正在聆听…", kind="listening", show_microphone=True, level=0.25)

    assert legacy_backend.calls[-1] == ("show", ("正在聆听…", 0, "listening", 0, True, 0.25))


class _BrokenNativeCall:
    def __init__(self, *, logger=None):
        self.logger = logger

    def start(self) -> None:
        return None

    def configure(self, config) -> None:
        return None

    def show(
        self,
        text: str,
        *,
        seq: int = 0,
        kind: str = "interim",
        stable_prefix_utf16_len: int = 0,
        show_microphone: bool = False,
        level: float = 0.0,
    ) -> None:
        raise RuntimeError("native show failed")

    def stop(self) -> None:
        return None


class _ConfigureFailNative:
    instances: list["_ConfigureFailNative"] = []

    def __init__(self, *, logger=None):
        self.logger = logger
        self.stop_calls = 0
        type(self).instances.append(self)

    def start(self) -> None:
        return None

    def configure(self, config) -> None:
        raise RuntimeError("configure failed")

    def stop(self) -> None:
        self.stop_calls += 1


class _BrokenLegacyBackend:
    def start(self) -> None:
        raise RuntimeError("legacy start failed")

    def configure(self, config) -> None:
        return None

    def show(
        self,
        text: str,
        *,
        seq: int = 0,
        kind: str = "interim",
        stable_prefix_utf16_len: int = 0,
        show_microphone: bool = False,
        level: float = 0.0,
    ) -> None:
        return None

    def stop(self) -> None:
        return None


def test_overlay_preview_logs_fallback_failures(monkeypatch, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(overlay_preview, "OverlayPreviewCpp", _BrokenNativeCall)
    monkeypatch.setattr(overlay_preview, "TkOverlayPreview", _BrokenLegacyBackend)

    preview = overlay_preview.OverlayPreview(logging.getLogger("overlay-test"))
    preview.start()
    preview.show("hello")

    assert "overlay_fallback_failed method=show" in caplog.text


def test_overlay_preview_stops_native_backend_after_configure_failure(monkeypatch):
    _ConfigureFailNative.instances.clear()
    legacy_backend = _LegacyBackend()
    monkeypatch.setattr(overlay_preview, "OverlayPreviewCpp", _ConfigureFailNative)
    monkeypatch.setattr(overlay_preview, "TkOverlayPreview", lambda: legacy_backend)

    preview = overlay_preview.OverlayPreview(logging.getLogger("overlay-test"))
    preview.start()
    preview.show("hello")

    assert _ConfigureFailNative.instances[0].stop_calls == 1
    assert legacy_backend.calls[0][0] == "start"
    assert legacy_backend.calls[2] == ("show", ("hello", 0, "interim", 0, False, 0.0))
