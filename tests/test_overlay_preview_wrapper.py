import logging

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

    def show(self, text: str, *, seq: int = 0, kind: str = "interim") -> None:
        self.calls.append(("show", (text, seq, kind)))

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
    preview.show("你好")

    assert legacy_backend.calls[0][0] == "start"
    assert legacy_backend.calls[1][0] == "configure"
    assert legacy_backend.calls[2] == ("show", ("你好", 0, "interim"))
