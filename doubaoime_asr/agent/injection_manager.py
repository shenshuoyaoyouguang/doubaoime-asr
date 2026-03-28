from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from pywinauto import Desktop
from pywinauto.keyboard import send_keys

from .clipboard_fallback import capture_clipboard_text, restore_clipboard_text, set_clipboard_text
from .input_injector import (
    FocusChangedError,
    FocusTarget,
    WindowsTextInjector,
    send_ctrl_v,
    send_wm_paste,
)


@dataclass(slots=True)
class InjectionResult:
    method: str
    restored_clipboard: bool | None = None


class TextInjectionManager:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.injector = WindowsTextInjector()

    def capture_target(self) -> FocusTarget | None:
        return self.injector.capture_target()

    async def inject_text(self, target: FocusTarget, text: str) -> InjectionResult:
        self.injector.ensure_target(target)

        ui_error: Exception | None = None
        try:
            restored = await self._uia_clipboard_paste(target, text)
            self.logger.info("inject_method=uia_clipboard")
            return InjectionResult(method="uia_clipboard", restored_clipboard=restored)
        except Exception as exc:
            ui_error = exc
            self.logger.exception("inject_uia_clipboard_failed")

        try:
            restored = await self._wm_paste(target, text)
            self.logger.info("inject_method=wm_paste")
            return InjectionResult(method="wm_paste", restored_clipboard=restored)
        except Exception:
            self.logger.exception("inject_wm_paste_failed")

        try:
            restored = await self._sendinput_paste(target, text)
            self.logger.info("inject_method=sendinput_paste")
            return InjectionResult(method="sendinput_paste", restored_clipboard=restored)
        except Exception:
            self.logger.exception("inject_sendinput_paste_failed")

        self.injector.type_text(target, text)
        self.logger.info("inject_method=sendinput_text")
        if ui_error is not None:
            self.logger.debug("first_uia_error=%r", ui_error)
        return InjectionResult(method="sendinput_text")

    async def _uia_clipboard_paste(self, target: FocusTarget, text: str) -> bool:
        snapshot = capture_clipboard_text()
        sequence = set_clipboard_text(text)
        try:
            hwnd = target.focus_hwnd or target.hwnd
            wrapper = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
            try:
                wrapper.set_focus()
            except Exception:
                pass
            try:
                wrapper.type_keys("^v", set_foreground=False)
            except Exception:
                send_keys("^v", with_spaces=True, pause=0.01)
        finally:
            return await restore_clipboard_text(snapshot, expected_sequence=sequence)

    async def _wm_paste(self, target: FocusTarget, text: str) -> bool:
        snapshot = capture_clipboard_text()
        sequence = set_clipboard_text(text)
        try:
            hwnd = target.focus_hwnd or target.hwnd
            send_wm_paste(hwnd)
        finally:
            return await restore_clipboard_text(snapshot, expected_sequence=sequence)

    async def _sendinput_paste(self, target: FocusTarget, text: str) -> bool:
        self.injector.ensure_target(target)
        snapshot = capture_clipboard_text()
        sequence = set_clipboard_text(text)
        try:
            send_ctrl_v()
        finally:
            return await restore_clipboard_text(snapshot, expected_sequence=sequence)
