import asyncio
import logging

import pytest

from doubaoime_asr.agent.config import (
    INJECTION_POLICY_DIRECT_ONLY,
    INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
)
from doubaoime_asr.agent.injection_manager import TextInjectionManager
from doubaoime_asr.agent.input_injector import FocusTarget


def test_direct_only_injection_avoids_clipboard(monkeypatch):
    manager = TextInjectionManager(logging.getLogger("inject-test"), policy=INJECTION_POLICY_DIRECT_ONLY)
    typed: list[tuple[FocusTarget, str]] = []

    monkeypatch.setattr(manager.injector, "ensure_target", lambda target: None)
    monkeypatch.setattr(manager.injector, "type_text", lambda target, text: typed.append((target, text)))
    monkeypatch.setattr("doubaoime_asr.agent.injection_manager.capture_clipboard_text", lambda: pytest.fail("clipboard should not be used"))

    result = asyncio.run(manager.inject_text(FocusTarget(hwnd=1), "你好"))

    assert typed == [(FocusTarget(hwnd=1), "你好")]
    assert result.method == "sendinput_text"
    assert result.clipboard_touched is False


def test_direct_then_clipboard_fallback_uses_clipboard(monkeypatch):
    manager = TextInjectionManager(
        logging.getLogger("inject-test"),
        policy=INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
    )

    monkeypatch.setattr(manager.injector, "ensure_target", lambda target: None)
    monkeypatch.setattr(manager.injector, "type_text", lambda target, text: (_ for _ in ()).throw(RuntimeError("direct failed")))
    monkeypatch.setattr(manager, "_uia_clipboard_paste", lambda target, text: asyncio.sleep(0, result=True))

    result = asyncio.run(manager.inject_text(FocusTarget(hwnd=1), "hello"))

    assert result.method == "uia_clipboard"
    assert result.clipboard_touched is True
    assert result.restored_clipboard is True


def test_terminal_injection_uses_terminal_profile(monkeypatch):
    manager = TextInjectionManager(
        logging.getLogger("inject-test"),
        policy=INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
    )
    terminal_target = FocusTarget(
        hwnd=1,
        process_name="WindowsTerminal.exe",
        window_class="CASCADIA_HOSTING_WINDOW_CLASS",
        is_terminal=True,
        terminal_kind="windows_terminal",
    )

    monkeypatch.setattr(manager.injector, "ensure_target", lambda target: None)
    monkeypatch.setattr(manager.injector, "type_text", lambda target, text: (_ for _ in ()).throw(RuntimeError("direct failed")))
    monkeypatch.setattr(manager, "_send_ctrl_shift_v_paste", lambda target, text: asyncio.sleep(0, result=True))

    result = asyncio.run(manager.inject_text(terminal_target, "hello"))

    assert result.method == "terminal_ctrl_shift_v"
    assert result.target_profile == "terminal"
