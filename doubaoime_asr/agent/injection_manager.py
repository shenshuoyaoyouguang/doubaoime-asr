from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from pywinauto import Desktop
from pywinauto.keyboard import send_keys

from .config import (
    INJECTION_POLICY_DIRECT_ONLY,
    INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
)
from .clipboard_fallback import capture_clipboard_text, restore_clipboard_text, set_clipboard_text
from .input_injector import (
    FocusChangedError,
    FocusTarget,
    WindowsTextInjector,
    send_ctrl_shift_v,
    send_ctrl_v,
    send_shift_insert,
    send_wm_paste,
)


@dataclass(slots=True)
class InjectionResult:
    method: str
    target_profile: str = "editor"
    clipboard_touched: bool = False
    restored_clipboard: bool | None = None


class TextInjectionManager:
    def __init__(
        self,
        logger: logging.Logger,
        *,
        policy: str = INJECTION_POLICY_DIRECT_ONLY,
    ) -> None:
        self.logger = logger
        self.injector = WindowsTextInjector()
        self.policy = policy

    def capture_target(self) -> FocusTarget | None:
        return self.injector.capture_target()

    def set_policy(self, policy: str) -> None:
        self.policy = policy

    async def inject_text(self, target: FocusTarget, text: str) -> InjectionResult:
        self.injector.ensure_target(target)
        target_profile = "terminal" if target.is_terminal else "editor"
        self.logger.info(
            "inject_target profile=%s process=%s window_class=%s focus_class=%s terminal_kind=%s",
            target_profile,
            target.process_name,
            target.window_class,
            target.focus_class,
            target.terminal_kind,
        )

        try:
            self.injector.type_text(target, text)
            self.logger.info("inject_method=sendinput_text policy=%s", self.policy)
            return InjectionResult(method="sendinput_text", target_profile=target_profile)
        except FocusChangedError:
            raise
        except Exception:
            if self.policy == INJECTION_POLICY_DIRECT_ONLY:
                raise
            self.logger.exception("inject_sendinput_text_failed")

        if self.policy != INJECTION_POLICY_DIRECT_THEN_CLIPBOARD:
            raise RuntimeError(f"unsupported injection policy: {self.policy}")

        if target.is_terminal:
            return await self._inject_terminal(target, text)

        first_error: Exception | None = None
        try:
            restored = await self._uia_clipboard_paste(target, text)
            self.logger.info("inject_method=uia_clipboard")
            return InjectionResult(
                method="uia_clipboard",
                target_profile=target_profile,
                clipboard_touched=True,
                restored_clipboard=restored,
            )
        except Exception as exc:
            first_error = exc
            self.logger.exception("inject_uia_clipboard_failed")

        try:
            restored = await self._wm_paste(target, text)
            self.logger.info("inject_method=wm_paste")
            return InjectionResult(
                method="wm_paste",
                target_profile=target_profile,
                clipboard_touched=True,
                restored_clipboard=restored,
            )
        except Exception:
            self.logger.exception("inject_wm_paste_failed")

        try:
            restored = await self._sendinput_paste(target, text)
            self.logger.info("inject_method=sendinput_paste")
            return InjectionResult(
                method="sendinput_paste",
                target_profile=target_profile,
                clipboard_touched=True,
                restored_clipboard=restored,
            )
        except Exception:
            self.logger.exception("inject_sendinput_paste_failed")

        if first_error is not None:
            self.logger.debug("first_clipboard_error=%r", first_error)
        raise RuntimeError("all injection methods failed")

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

    async def _inject_terminal(self, target: FocusTarget, text: str) -> InjectionResult:
        if target.terminal_kind == "windows_terminal":
            methods = (
                ("terminal_ctrl_shift_v", self._send_ctrl_shift_v_paste),
                ("sendinput_paste", self._sendinput_paste),
            )
        else:
            methods = (
                ("terminal_shift_insert", self._shift_insert_paste),
                ("sendinput_paste", self._sendinput_paste),
            )

        first_error: Exception | None = None
        for method_name, handler in methods:
            try:
                restored = await handler(target, text)
                self.logger.info("inject_method=%s", method_name)
                return InjectionResult(
                    method=method_name,
                    target_profile="terminal",
                    clipboard_touched=True,
                    restored_clipboard=restored,
                )
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                self.logger.exception("inject_terminal_failed method=%s", method_name)

        if first_error is not None:
            self.logger.debug("first_terminal_error=%r", first_error)
        raise RuntimeError("all terminal injection methods failed")

    async def _send_ctrl_shift_v_paste(self, target: FocusTarget, text: str) -> bool:
        self.injector.ensure_target(target)
        snapshot = capture_clipboard_text()
        sequence = set_clipboard_text(text)
        try:
            send_ctrl_shift_v()
        finally:
            return await restore_clipboard_text(snapshot, expected_sequence=sequence)

    async def _shift_insert_paste(self, target: FocusTarget, text: str) -> bool:
        self.injector.ensure_target(target)
        snapshot = capture_clipboard_text()
        sequence = set_clipboard_text(text)
        try:
            send_shift_insert()
        finally:
            return await restore_clipboard_text(snapshot, expected_sequence=sequence)
