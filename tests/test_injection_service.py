"""
InjectionService 测试。

覆盖以下场景：
- 目标捕获
- 注入执行（各种模式）
- 流式注入逻辑
- 焦点变化处理
- 权限检查
"""
import asyncio
import logging

import pytest

from doubaoime_asr.agent.config import (
    AgentConfig,
    INJECTION_POLICY_DIRECT_ONLY,
    INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
    STREAMING_TEXT_MODE_OVERLAY_ONLY,
    STREAMING_TEXT_MODE_SAFE_INLINE,
)
from doubaoime_asr.agent.injection_manager import InjectionResult
from doubaoime_asr.agent.injection_service import InjectionService, InjectionSessionState, Mode
from doubaoime_asr.agent.input_injector import FocusChangedError, FocusTarget


# ===== 测试 Fixture =====


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("injection-service-test")


@pytest.fixture
def config() -> AgentConfig:
    return AgentConfig(
        mode="inject",
        injection_policy=INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
        streaming_text_mode=STREAMING_TEXT_MODE_SAFE_INLINE,
    )


@pytest.fixture
def injection_service(logger: logging.Logger, config: AgentConfig) -> InjectionService:
    return InjectionService(logger, config)


@pytest.fixture
def terminal_target() -> FocusTarget:
    return FocusTarget(
        hwnd=1,
        process_name="WindowsTerminal.exe",
        window_class="CASCADIA_HOSTING_WINDOW_CLASS",
        is_terminal=True,
        terminal_kind="windows_terminal",
    )


@pytest.fixture
def editor_target() -> FocusTarget:
    return FocusTarget(
        hwnd=2,
        process_name="notepad.exe",
        window_class="Notepad",
        is_terminal=False,
        text_input_profile="plain_editor",
    )


@pytest.fixture
def elevated_target() -> FocusTarget:
    return FocusTarget(
        hwnd=3,
        process_name="cmd.exe",
        is_terminal=True,
        terminal_kind="console",
        is_elevated=True,
    )


@pytest.fixture
def browser_target() -> FocusTarget:
    return FocusTarget(
        hwnd=4,
        process_name="chrome.exe",
        window_class="Chrome_WidgetWin_1",
        focus_class="Chrome_RenderWidgetHostHWND",
        is_terminal=False,
        text_input_profile="browser_editable",
    )


# ===== InjectionSessionState 测试 =====


def test_session_state_begin_and_clear():
    """测试会话状态开始和清除。"""
    state = InjectionSessionState()
    target = FocusTarget(hwnd=1)

    state.begin(target, "inject", inline_streaming_enabled=True)
    assert state.target == target
    assert state.mode == "inject"
    assert state.inline_streaming_enabled is True
    assert state.final_injection_blocked is False

    state.clear()
    assert state.target is None
    assert state.mode == "inject"
    assert state.inline_streaming_enabled is False
    assert state.final_injection_blocked is False


def test_session_state_block_injection():
    """测试注入阻止。"""
    state = InjectionSessionState()
    target = FocusTarget(hwnd=1)

    state.begin(target, "inject", inline_streaming_enabled=True)
    assert state.inline_streaming_enabled is True

    state.block_injection()
    assert state.inline_streaming_enabled is False
    assert state.final_injection_blocked is True
    assert state.target is None


# ===== 目标管理测试 =====


def test_capture_target_returns_none_when_no_foreground_window(
    injection_service: InjectionService,
    monkeypatch,
):
    """测试无前景窗口时捕获返回 None。"""
    monkeypatch.setattr(
        "doubaoime_asr.agent.input_injector.get_foreground_window",
        lambda: 0,
    )
    result = injection_service.capture_target()
    assert result is None


def test_capture_target_returns_target_when_foreground_exists(
    injection_service: InjectionService,
    monkeypatch,
):
    """测试有前景窗口时返回 FocusTarget。"""
    monkeypatch.setattr(
        "doubaoime_asr.agent.input_injector.get_foreground_window",
        lambda: 12345,
    )
    monkeypatch.setattr(
        "doubaoime_asr.agent.input_injector.get_focus_hwnd",
        lambda hwnd: 12346,
    )
    monkeypatch.setattr(
        "doubaoime_asr.agent.input_injector.get_window_process_id",
        lambda hwnd: 1000,
    )
    monkeypatch.setattr(
        "doubaoime_asr.agent.input_injector.get_process_name",
        lambda pid: "notepad.exe",
    )
    monkeypatch.setattr(
        "doubaoime_asr.agent.input_injector.get_window_class_name",
        lambda hwnd: "Notepad",
    )
    monkeypatch.setattr(
        "doubaoime_asr.agent.input_injector.classify_focus_target",
        lambda pn, wc, fc: (False, None),
    )
    monkeypatch.setattr(
        "doubaoime_asr.agent.input_injector.get_process_elevation",
        lambda pid: False,
    )

    result = injection_service.capture_target()
    assert result is not None
    assert result.hwnd == 12345
    assert result.focus_hwnd == 12346
    assert result.process_name == "notepad.exe"


def test_get_current_target_returns_session_target(injection_service: InjectionService):
    """测试获取当前会话目标。"""
    target = FocusTarget(hwnd=1)
    injection_service.begin_session(target, "inject")

    result = injection_service.get_current_target()
    assert result == target

    injection_service.end_session()
    assert injection_service.get_current_target() is None


def test_get_current_mode_returns_session_mode(injection_service: InjectionService):
    """测试获取当前会话模式。"""
    injection_service.begin_session(FocusTarget(hwnd=1), "recognize")
    assert injection_service.get_current_mode() == "recognize"

    injection_service.end_session()
    assert injection_service.get_current_mode() == "inject"


# ===== 会话管理测试 =====


def test_begin_session_without_inline_streaming(
    injection_service: InjectionService,
    editor_target: FocusTarget,
):
    """测试不启用流式上屏时开始会话。"""
    composition = injection_service.begin_session(
        editor_target,
        "inject",
        inline_streaming_enabled=False,
    )
    assert composition is None
    assert injection_service.get_composition() is None
    assert injection_service.is_inline_streaming_enabled() is False


def test_begin_session_with_inline_streaming(
    injection_service: InjectionService,
    editor_target: FocusTarget,
):
    """测试启用流式上屏时开始会话，创建 CompositionSession。"""
    composition = injection_service.begin_session(
        editor_target,
        "inject",
        inline_streaming_enabled=True,
    )
    assert composition is not None
    assert injection_service.get_composition() == composition
    assert injection_service.is_inline_streaming_enabled() is True


def test_end_session_clears_state(injection_service: InjectionService, editor_target: FocusTarget):
    """测试结束会话清除状态。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=True)
    assert injection_service.get_current_target() is not None

    injection_service.end_session()
    assert injection_service.get_current_target() is None
    assert injection_service.get_composition() is None
    assert injection_service.is_inline_streaming_enabled() is False


# ===== 注入执行测试 =====


@pytest.mark.asyncio
async def test_inject_final_skipped_in_recognize_mode(
    injection_service: InjectionService,
    editor_target: FocusTarget,
):
    """测试识别模式下跳过注入。"""
    injection_service.begin_session(editor_target, "recognize")
    result = await injection_service.inject_final("你好")
    assert result is None


@pytest.mark.asyncio
async def test_inject_final_direct_success(
    injection_service: InjectionService,
    editor_target: FocusTarget,
    monkeypatch,
):
    """测试直接注入成功。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=False)

    async def fake_inject_text(target, text):
        return InjectionResult(method="sendinput_text", target_profile="editor")

    monkeypatch.setattr(injection_service._manager, "inject_text", fake_inject_text)

    result = await injection_service.inject_final("你好")
    assert result is not None
    assert result.method == "sendinput_text"


@pytest.mark.asyncio
async def test_inject_final_blocked_when_target_none(
    injection_service: InjectionService,
):
    """测试无目标时注入被阻止。"""
    injection_service.begin_session(None, "inject")
    result = await injection_service.inject_final("你好")
    assert result is None


@pytest.mark.asyncio
async def test_inject_final_blocked_when_injection_blocked(
    injection_service: InjectionService,
    editor_target: FocusTarget,
):
    """测试注入被阻止标志时跳过注入。"""
    injection_service.begin_session(editor_target, "inject")
    injection_service.handle_focus_changed()

    result = await injection_service.inject_final("你好")
    assert result is None


@pytest.mark.asyncio
async def test_inject_final_raises_focus_changed_error(
    injection_service: InjectionService,
    editor_target: FocusTarget,
    monkeypatch,
):
    """测试注入时焦点变化抛出异常。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=False)

    async def fake_inject_text(target, text):
        raise FocusChangedError("focus changed")

    monkeypatch.setattr(injection_service._manager, "inject_text", fake_inject_text)

    with pytest.raises(FocusChangedError):
        await injection_service.inject_final("你好")


@pytest.mark.asyncio
async def test_inject_final_inline_composition(
    injection_service: InjectionService,
    editor_target: FocusTarget,
    monkeypatch,
):
    """测试流式上屏模式的最终注入。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=True)
    composition = injection_service.get_composition()

    # 先渲染中间文本
    calls = []
    monkeypatch.setattr(
        composition.injector,
        "replace_text",
        lambda target, prev, new: calls.append((prev, new)),
    )
    monkeypatch.setattr(composition.injector, "ensure_target", lambda target: None)

    await injection_service.apply_inline_interim("你好")
    assert calls[-1] == ("", "你好")

    result = await injection_service.inject_final("你好啊")
    assert result is not None
    assert result.method == "inline_composition"
    assert calls[-1] == ("你好", "你好啊")


@pytest.mark.asyncio
async def test_inject_final_inline_composition_skips_when_already_final(
    injection_service: InjectionService,
    editor_target: FocusTarget,
    monkeypatch,
):
    """最终文本已一致时跳过重复 finalize。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=True)
    composition = injection_service.get_composition()
    assert composition is not None

    calls = []
    monkeypatch.setattr(
        composition.injector,
        "replace_text",
        lambda target, prev, new: calls.append((prev, new)),
    )
    monkeypatch.setattr(composition.injector, "ensure_target", lambda target: None)

    await injection_service.prepare_final_text("最终文本")
    assert calls == [("", "最终文本")]

    result = await injection_service.inject_final("最终文本")
    assert result is not None
    assert result.method == "inline_composition_skipped"
    assert calls == [("", "最终文本")]


@pytest.mark.asyncio
async def test_inject_final_inline_falls_back_to_direct_when_not_rendered(
    injection_service: InjectionService,
    editor_target: FocusTarget,
    monkeypatch,
):
    """inline 最终注入失败且尚未上屏时回退到 direct。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=True)

    async def fake_direct(text: str):
        return InjectionResult(method="sendinput_text", target_profile="editor")

    monkeypatch.setattr(injection_service, "_inject_final_direct", fake_direct)
    monkeypatch.setattr(injection_service, "_handle_inline_failure", lambda log_tag: False)

    class _BrokenComposition:
        rendered_text = ""
        final_text = ""

        def finalize(self, text: str) -> None:
            raise RuntimeError("boom")

    injection_service._session.composition = _BrokenComposition()

    result = await injection_service.inject_final("最终文本")

    assert result is not None
    assert result.method == "sendinput_text"
    assert injection_service.is_inline_streaming_enabled() is False
    assert injection_service.get_composition() is None


@pytest.mark.asyncio
async def test_inject_final_blocked_for_elevated_target(
    injection_service: InjectionService,
    elevated_target: FocusTarget,
):
    """测试管理员目标时注入被阻止。"""
    injection_service.set_process_elevated(False)
    injection_service.begin_session(elevated_target, "inject", inline_streaming_enabled=False)

    result = await injection_service.inject_final("你好")
    assert result is None


# ===== 流式注入逻辑测试 =====


def test_should_enable_inline_streaming_for_editor(
    injection_service: InjectionService,
    editor_target: FocusTarget,
):
    """测试编辑器目标应启用流式上屏。"""
    injection_service.begin_session(editor_target, "inject")
    assert injection_service.should_enable_inline_streaming(editor_target) is True


def test_should_not_enable_inline_streaming_for_terminal(
    injection_service: InjectionService,
    terminal_target: FocusTarget,
):
    """测试终端目标不应启用流式上屏。"""
    injection_service.begin_session(terminal_target, "inject")
    assert injection_service.should_enable_inline_streaming(terminal_target) is False


def test_should_not_enable_inline_streaming_in_recognize_mode(
    injection_service: InjectionService,
    editor_target: FocusTarget,
):
    """测试识别模式不应启用流式上屏。"""
    config = AgentConfig(
        mode="recognize",
        streaming_text_mode=STREAMING_TEXT_MODE_SAFE_INLINE,
    )
    service = InjectionService(logging.getLogger("test"), config)
    service.begin_session(editor_target, "recognize")
    assert service.should_enable_inline_streaming(editor_target) is False


def test_should_not_enable_inline_streaming_in_overlay_only_mode(
    injection_service: InjectionService,
    editor_target: FocusTarget,
):
    """测试 overlay_only 模式不应启用流式上屏。"""
    config = AgentConfig(
        mode="inject",
        streaming_text_mode=STREAMING_TEXT_MODE_OVERLAY_ONLY,
    )
    service = InjectionService(logging.getLogger("test"), config)
    service.begin_session(editor_target, "inject")
    assert service.should_enable_inline_streaming(editor_target) is False


def test_should_not_enable_inline_streaming_for_browser_profile(
    injection_service: InjectionService,
    browser_target: FocusTarget,
):
    """测试浏览器类控件默认不启用流式上屏。"""
    injection_service.begin_session(browser_target, "inject")
    assert injection_service.should_enable_inline_streaming(browser_target) is False


@pytest.mark.asyncio
async def test_apply_inline_interim_updates_composition(
    injection_service: InjectionService,
    editor_target: FocusTarget,
    monkeypatch,
):
    """测试流式中间结果更新 CompositionSession。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=True)
    composition = injection_service.get_composition()

    calls = []
    monkeypatch.setattr(
        composition.injector,
        "replace_text",
        lambda target, prev, new: calls.append((prev, new)),
    )
    monkeypatch.setattr(composition.injector, "ensure_target", lambda target: None)

    await injection_service.apply_inline_interim("你好")
    assert calls == [("", "你好")]

    await injection_service.apply_inline_interim("你好啊")
    assert calls == [("", "你好"), ("你好", "你好啊")]


@pytest.mark.asyncio
async def test_apply_inline_interim_skipped_when_disabled(
    injection_service: InjectionService,
    editor_target: FocusTarget,
):
    """测试流式上屏禁用时跳过中间结果应用。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=False)
    await injection_service.apply_inline_interim("你好")
    # 无异常，无操作


@pytest.mark.asyncio
async def test_apply_inline_interim_handles_focus_changed(
    injection_service: InjectionService,
    editor_target: FocusTarget,
    monkeypatch,
):
    """测试流式中间结果应用时焦点变化处理。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=True)
    composition = injection_service.get_composition()

    monkeypatch.setattr(
        composition.injector,
        "replace_text",
        lambda target, prev, new: (_ for _ in ()).throw(FocusChangedError("changed")),
    )
    monkeypatch.setattr(composition.injector, "ensure_target", lambda target: None)

    await injection_service.apply_inline_interim("你好")
    assert injection_service.is_injection_blocked() is True


@pytest.mark.asyncio
async def test_prepare_final_text_finalizes_composition(
    injection_service: InjectionService,
    editor_target: FocusTarget,
    monkeypatch,
):
    """测试准备最终文本调用 CompositionSession.finalize。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=True)
    composition = injection_service.get_composition()

    calls = []
    monkeypatch.setattr(
        composition.injector,
        "replace_text",
        lambda target, prev, new: calls.append((prev, new)),
    )
    monkeypatch.setattr(composition.injector, "ensure_target", lambda target: None)

    await injection_service.prepare_final_text("你好啊")
    assert calls == [("", "你好啊")]
    assert composition.final_text == "你好啊"


@pytest.mark.asyncio
async def test_prepare_final_text_handles_focus_changed(
    injection_service: InjectionService,
    editor_target: FocusTarget,
    monkeypatch,
):
    """测试准备最终文本时焦点变化处理。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=True)
    composition = injection_service.get_composition()

    monkeypatch.setattr(
        composition.injector,
        "replace_text",
        lambda target, prev, new: (_ for _ in ()).throw(FocusChangedError("changed")),
    )
    monkeypatch.setattr(composition.injector, "ensure_target", lambda target: None)

    await injection_service.prepare_final_text("你好")
    assert injection_service.is_injection_blocked() is True


# ===== 焦点变化处理测试 =====


def test_handle_focus_changed_blocks_injection(
    injection_service: InjectionService,
    editor_target: FocusTarget,
):
    """测试焦点变化处理后注入被阻止。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=True)
    assert injection_service.is_injection_blocked() is False

    injection_service.handle_focus_changed()
    assert injection_service.is_injection_blocked() is True
    assert injection_service.get_current_target() is None


def test_is_injection_blocked_after_handle_focus_changed(
    injection_service: InjectionService,
):
    """测试焦点变化后注入阻止标志。"""
    assert injection_service.is_injection_blocked() is False
    injection_service.handle_focus_changed()
    assert injection_service.is_injection_blocked() is True


# ===== 权限检查测试 =====


def test_target_requires_admin_when_elevated_and_process_not_elevated(
    injection_service: InjectionService,
    elevated_target: FocusTarget,
):
    """测试目标为管理员且进程非管理员时需要管理员权限。"""
    injection_service.set_process_elevated(False)
    assert injection_service.target_requires_admin(elevated_target) is True


def test_target_requires_admin_not_when_process_elevated(
    injection_service: InjectionService,
    elevated_target: FocusTarget,
):
    """测试进程为管理员时不需要额外权限。"""
    injection_service.set_process_elevated(True)
    assert injection_service.target_requires_admin(elevated_target) is False


def test_target_requires_admin_not_for_normal_target(
    injection_service: InjectionService,
    editor_target: FocusTarget,
):
    """测试普通目标不需要管理员权限。"""
    injection_service.set_process_elevated(False)
    assert injection_service.target_requires_admin(editor_target) is False


def test_target_requires_admin_none_target(injection_service: InjectionService):
    """测试无目标时不需要管理员权限。"""
    injection_service.set_process_elevated(False)
    assert injection_service.target_requires_admin(None) is False


# ===== 配置更新测试 =====


def test_configure_updates_policy(
    injection_service: InjectionService,
):
    """测试配置更新改变注入策略。"""
    assert injection_service.get_injection_policy() == INJECTION_POLICY_DIRECT_THEN_CLIPBOARD

    new_config = AgentConfig(injection_policy=INJECTION_POLICY_DIRECT_ONLY)
    injection_service.configure(new_config)

    assert injection_service.get_injection_policy() == INJECTION_POLICY_DIRECT_ONLY


def test_configure_updates_streaming_text_mode(
    injection_service: InjectionService,
    editor_target: FocusTarget,
):
    """测试配置更新改变流式文本模式。"""
    assert injection_service.get_streaming_text_mode() == STREAMING_TEXT_MODE_SAFE_INLINE

    new_config = AgentConfig(
        injection_policy=INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
        streaming_text_mode=STREAMING_TEXT_MODE_OVERLAY_ONLY,
    )
    injection_service.configure(new_config)

    assert injection_service.get_streaming_text_mode() == STREAMING_TEXT_MODE_OVERLAY_ONLY
    injection_service.begin_session(editor_target, "inject")
    assert injection_service.should_enable_inline_streaming(editor_target) is False


# ===== 边界条件测试 =====


@pytest.mark.asyncio
async def test_inject_final_empty_text_skipped(
    injection_service: InjectionService,
    editor_target: FocusTarget,
):
    """测试空文本跳过注入（在识别模式下不记录日志）。"""
    injection_service.begin_session(editor_target, "recognize")
    result = await injection_service.inject_final("")
    assert result is None


@pytest.mark.asyncio
async def test_apply_inline_interim_same_text_skipped(
    injection_service: InjectionService,
    editor_target: FocusTarget,
    monkeypatch,
):
    """测试相同中间文本跳过更新。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=True)
    composition = injection_service.get_composition()

    calls = []
    monkeypatch.setattr(
        composition.injector,
        "replace_text",
        lambda target, prev, new: calls.append((prev, new)),
    )
    monkeypatch.setattr(composition.injector, "ensure_target", lambda target: None)

    await injection_service.apply_inline_interim("你好")
    assert len(calls) == 1

    # 相同文本不触发更新
    await injection_service.apply_inline_interim("你好")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_prepare_final_text_same_text_skipped(
    injection_service: InjectionService,
    editor_target: FocusTarget,
    monkeypatch,
):
    """测试相同最终文本跳过准备。"""
    injection_service.begin_session(editor_target, "inject", inline_streaming_enabled=True)
    composition = injection_service.get_composition()

    calls = []
    monkeypatch.setattr(
        composition.injector,
        "replace_text",
        lambda target, prev, new: calls.append((prev, new)),
    )
    monkeypatch.setattr(composition.injector, "ensure_target", lambda target: None)

    await injection_service.prepare_final_text("你好")
    assert len(calls) == 1
    assert composition.final_text == "你好"

    # 相同文本不触发更新
    await injection_service.prepare_final_text("你好")
    assert len(calls) == 1
