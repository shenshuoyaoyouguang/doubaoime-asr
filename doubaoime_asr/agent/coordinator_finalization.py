"""
Coordinator Finalization 模块。

提供最终结果提交、润色、文本注入等功能。
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .config import (
    FINAL_COMMIT_SOURCE_RAW,
    POLISH_MODE_OLLAMA,
)
from .input_injector import FocusChangedError
from .text_polisher import PolishResult

if TYPE_CHECKING:
    from .coordinator import VoiceInputCoordinator

__all__ = [
    "_inject_final",
    "_resolve_final_text",
    "_resolve_committed_text",
    "_status_for_final_result",
    "_status_for_error",
]


# ===== 最终结果提交 =====


async def _inject_final(coordinator: VoiceInputCoordinator, text: str) -> None:
    """执行最终文本注入。"""
    if not text:
        return
    session = coordinator.session_manager.get_session()
    if session is None:
        return
    if session.mode != "inject":
        coordinator.logger.info("inject_skipped reason=recognize_mode text_length=%s", len(text))
        return
    if coordinator.injection_service.is_injection_blocked():
        return

    inject_started_at = time.perf_counter()
    try:
        result = await coordinator.injection_service.inject_final(text)
        if result:
            finished_to_inject_ms = None
            stop_to_inject_ms = None
            if coordinator._finished_event_started_at is not None:
                finished_to_inject_ms = int((inject_started_at - coordinator._finished_event_started_at) * 1000)
            if session.stop_sent_at is not None:
                stop_to_inject_ms = int((inject_started_at - session.stop_sent_at) * 1000)
            coordinator.logger.info(
                "inject_success method=%s stop_to_inject_ms=%s finished_to_inject_ms=%s",
                result.method,
                stop_to_inject_ms,
                finished_to_inject_ms,
            )
    except FocusChangedError:
        coordinator.injection_service.handle_focus_changed()
        coordinator.logger.warning("inject_focus_changed")
        coordinator.set_status("焦点已变化,仅保留识别")
    except Exception:
        coordinator.logger.exception("inject_final_failed")
        coordinator.set_status("注入失败,仅保留识别")


async def _resolve_final_text(coordinator: VoiceInputCoordinator, raw_text: str) -> PolishResult:
    """润色最终文本。"""
    resolve_started_at = time.perf_counter()
    if coordinator.config.polish_mode == POLISH_MODE_OLLAMA and raw_text.strip():
        coordinator.set_status("润色中…")
    result = await coordinator.text_polisher.polish(raw_text)
    resolved_source = "raw" if result.text == raw_text else "polished"
    coordinator.logger.info(
        "final_text_resolved mode=%s latency_ms=%d raw_digest=%s resolved_digest=%s resolved_source=%s",
        result.applied_mode,
        int((time.perf_counter() - resolve_started_at) * 1000),
        coordinator._text_digest(raw_text),
        coordinator._text_digest(result.text),
        resolved_source,
    )
    return result


def _resolve_committed_text(
    coordinator: VoiceInputCoordinator,
    raw_text: str,
    result: PolishResult,
) -> tuple[str, str]:
    """解析提交的文本。"""
    if coordinator.config.final_commit_source == FINAL_COMMIT_SOURCE_RAW:
        return raw_text, "raw"
    committed_text = result.text or raw_text
    committed_source = "raw" if committed_text == raw_text else "polished"
    return committed_text, committed_source


# ===== 状态生成 =====


def _status_for_final_result(
    coordinator: VoiceInputCoordinator,
    result: PolishResult,
    raw_text: str,
    *,
    committed_text: str | None = None,
    committed_source: str | None = None,
) -> str:
    """生成最终结果状态。"""
    resolved_committed_text = committed_text if committed_text is not None else result.text
    resolved_committed_source = (
        committed_source
        if committed_source is not None
        else ("raw" if resolved_committed_text == raw_text else "polished")
    )
    if result.applied_mode != "raw_fallback":
        if resolved_committed_source == "raw" and result.text != raw_text:
            return f"最终提交原文: {resolved_committed_text[-24:]}"
        return f"最终结果: {resolved_committed_text[-24:]}"
    excerpt = raw_text[-18:]
    fallback_messages = {
        "timeout": f"润色超时,已使用原文: {excerpt}",
        "unavailable": f"润色不可用,已使用原文: {excerpt}",
        "no_model": f"未配置润色模型,已使用原文: {excerpt}",
        "invalid_response": f"润色结果无效,已使用原文: {excerpt}",
        "bad_prompt": f"润色提示词无效,已使用原文: {excerpt}",
    }
    return fallback_messages.get(result.fallback_reason or "", f"最终结果: {result.text[-24:]}")


def _status_for_error(coordinator: VoiceInputCoordinator, message: str) -> str:
    """生成错误状态。"""
    LOW_INPUT_STATUS = "未检测到有效麦克风输入,请检查麦克风静音/增益,或在设置中切换麦克风"
    if coordinator._should_warn_low_input():
        return LOW_INPUT_STATUS
    return f"识别失败: {message}"