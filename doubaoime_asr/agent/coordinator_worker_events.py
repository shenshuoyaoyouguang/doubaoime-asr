"""
Coordinator Worker Events 模块。

提供 Worker 事件处理功能,由 coordinator.py 委托处理各类 Worker 事件。
"""
from __future__ import annotations

import time

from .config import POLISH_MODE_OFF
from .events import (
    AudioLevelEvent,
    ErrorEvent,
    FallbackRequiredEvent,
    FinalResultEvent,
    FinishedEvent,
    InterimResultEvent,
    ReadyEvent,
    ServiceResolvedFinalEvent,
    StreamingStartedEvent,
    VoiceInputEvent,
    WorkerReadyEvent,
    WorkerStatusEvent,
)
from .session_manager import WorkerSessionState
from .text_polisher import PolishResult


# ===== 主入口函数 =====


async def _handle_worker_event(coordinator: "VoiceInputCoordinator", event: VoiceInputEvent) -> None:
    """处理 Worker 发送的事件(主入口)。"""
    session = coordinator.session_manager.get_session()
    if session is None:
        return

    event_type = event.event_type
    coordinator.logger.info("worker_event=%s", event_type)

    # 分发到具体处理函数
    if isinstance(event, WorkerReadyEvent):
        await _handle_worker_ready_event(coordinator, session, event)
    elif isinstance(event, ReadyEvent):
        await _handle_ready_event(coordinator, session, event)
    elif isinstance(event, StreamingStartedEvent):
        await _handle_streaming_started_event(coordinator, session, event)
    elif isinstance(event, WorkerStatusEvent):
        await _handle_worker_status_event(coordinator, session, event)
    elif isinstance(event, AudioLevelEvent):
        await _handle_audio_level_event(coordinator, session, event)
    elif isinstance(event, InterimResultEvent):
        await _handle_interim_result_event(coordinator, session, event)
    elif isinstance(event, FinalResultEvent):
        await _handle_final_result_event(coordinator, session, event)
    elif isinstance(event, ErrorEvent):
        await _handle_error_event(coordinator, session, event)
    elif isinstance(event, ServiceResolvedFinalEvent):
        await _handle_service_resolved_final_event(coordinator, session, event)
    elif isinstance(event, FallbackRequiredEvent):
        await _handle_fallback_required_event(coordinator, session, event)
    elif isinstance(event, FinishedEvent):
        await _handle_finished_event(coordinator, session, event)


# ===== 各事件类型处理函数 =====


async def _handle_worker_ready_event(coordinator: "VoiceInputCoordinator", session, event: WorkerReadyEvent) -> None:
    """处理 WorkerReadyEvent。"""
    session.process_ready = True


async def _handle_ready_event(coordinator: "VoiceInputCoordinator", session, event: ReadyEvent) -> None:
    """处理 ReadyEvent。"""
    if session.state == WorkerSessionState.STREAMING:
        session.ready = True
        coordinator.set_status("录音中,等待说话")
        await coordinator._send_stop_if_needed()


async def _handle_streaming_started_event(coordinator: "VoiceInputCoordinator", session, event: StreamingStartedEvent) -> None:
    """处理 StreamingStartedEvent。"""
    if session.state == WorkerSessionState.STREAMING:
        session.streaming_started = True
    coordinator.logger.info(
        "worker_streaming_started chunks=%s bytes=%s",
        event.chunks,
        event.bytes,
    )
    await coordinator._send_stop_if_needed()


async def _handle_worker_status_event(coordinator: "VoiceInputCoordinator", session, event: WorkerStatusEvent) -> None:
    """处理 WorkerStatusEvent。"""
    if event.message:
        coordinator.set_status(event.message)


async def _handle_audio_level_event(coordinator: "VoiceInputCoordinator", session, event: AudioLevelEvent) -> None:
    """处理 AudioLevelEvent。"""
    if session.state == WorkerSessionState.STREAMING:
        coordinator._record_audio_level(event.level)
        await coordinator.overlay_service.update_microphone_level(event.level)


async def _handle_interim_result_event(coordinator: "VoiceInputCoordinator", session, event: InterimResultEvent) -> None:
    """处理 InterimResultEvent。"""
    if session.state != WorkerSessionState.STREAMING:
        return
    text = coordinator._record_session_text(event, event.text, is_final=False)
    if text:
        coordinator._session_received_transcript = True
    if coordinator.console:
        print(f"\r[识别中] {text}", end="", flush=True)

    # TIP 处理
    if coordinator._tip_primary_active and coordinator._tip_session_id is not None:
        tip_result = await coordinator._tip_gateway.submit_interim(
            session_id=coordinator._tip_session_id,
            text=text,
        )
        if tip_result.success:
            coordinator.logger.info("tip_interim_applied session_id=%s", coordinator._tip_session_id)
            coordinator.set_status(f"识别中: {text[-24:]}")
            return
        fallback_ready = await coordinator._activate_tip_fallback(reason=tip_result.reason or "tip_interim_failed")
        if not fallback_ready:
            # 回退失败,阻止本地注入
            return

    # 提交临时结果快照
    seq = await coordinator._submit_interim_snapshot(text)
    coordinator.logger.info(
        "interim_snapshot_queued seq=%s segment_index=%s len=%s digest=%s inline_enabled=%s text_profile=%s",
        seq,
        getattr(event, "segment_index", None),
        len(text),
        coordinator._text_digest(text),
        coordinator.injection_service.is_inline_streaming_enabled(),
        coordinator._current_target_profile(),
    )
    coordinator.set_status(f"识别中: {text[-24:]}")


async def _handle_final_result_event(coordinator: "VoiceInputCoordinator", session, event: FinalResultEvent) -> None:
    """处理 FinalResultEvent。"""
    if session.state != WorkerSessionState.STREAMING:
        return
    await coordinator._flush_interim_dispatcher(reason="final_result")
    raw_text = coordinator._record_session_text(event, event.text, is_final=True)
    if raw_text:
        coordinator._session_received_transcript = True
    coordinator._last_displayed_raw_final_text = raw_text
    coordinator.logger.info(
        "final_result_received segment_index=%s len=%s digest=%s inline_enabled=%s text_profile=%s",
        getattr(event, "segment_index", None),
        len(raw_text),
        coordinator._text_digest(raw_text),
        coordinator.injection_service.is_inline_streaming_enabled(),
        coordinator._current_target_profile(),
    )
    if not coordinator._tip_primary_active:
        await coordinator.overlay_service.submit_final(raw_text, kind="final_raw")
        await coordinator.injection_service.prepare_final_text(raw_text)
    if not session.stop_sent:
        coordinator.set_status(f"识别中: {raw_text[-24:]}")


async def _handle_error_event(coordinator: "VoiceInputCoordinator", session, event: ErrorEvent) -> None:
    """处理 ErrorEvent。"""
    coordinator._asr_preflight.invalidate()
    if coordinator._tip_primary_active:
        await coordinator._cancel_tip_session("worker_error")
    await coordinator.overlay_service.hide("error")
    coordinator.set_status(coordinator._status_for_error(event.message))
    await coordinator._clear_active_session()


async def _handle_service_resolved_final_event(
    coordinator: "VoiceInputCoordinator", session, event: ServiceResolvedFinalEvent
) -> None:
    """处理 ServiceResolvedFinalEvent。"""
    coordinator._pending_service_resolved_final = event


async def _handle_fallback_required_event(coordinator: "VoiceInputCoordinator", session, event: FallbackRequiredEvent) -> None:
    """处理 FallbackRequiredEvent。"""
    coordinator._pending_service_fallback_reason = event.reason or None


async def _handle_finished_event(coordinator: "VoiceInputCoordinator", session, event: FinishedEvent) -> None:
    """处理 FinishedEvent - 最复杂的处理逻辑。"""
    coordinator._finished_event_started_at = time.perf_counter()
    await coordinator._flush_interim_dispatcher(reason="finished")
    raw_text = coordinator._aggregate_session_text()

    if raw_text:
        # 处理最终文本
        if raw_text != coordinator._last_displayed_raw_final_text and not coordinator._tip_primary_active:
            await coordinator.overlay_service.submit_final(raw_text, kind="final_raw")
            coordinator._last_displayed_raw_final_text = raw_text

        if session.mode == "inject":
            coordinator.set_status("正在准备上屏…")

        # 解析最终文本(使用 Service、Polisher 或原始文本)
        if coordinator._pending_service_resolved_final is not None:
            service_final = coordinator._pending_service_resolved_final
            fallback_reason = service_final.fallback_reason or coordinator._pending_service_fallback_reason
            result = PolishResult(
                text=service_final.text or raw_text,
                applied_mode=service_final.applied_mode or POLISH_MODE_OFF,
                latency_ms=0,
                fallback_reason=fallback_reason,
            )
            coordinator.logger.info(
                "service_final_resolved_applied raw_digest=%s resolved_digest=%s committed_source=%s",
                coordinator._text_digest(raw_text),
                coordinator._text_digest(result.text),
                service_final.committed_source,
            )
        elif coordinator.config.polish_mode == POLISH_MODE_OFF:
            result = PolishResult(text=raw_text, applied_mode=POLISH_MODE_OFF, latency_ms=0)
            coordinator.logger.info(
                "final_text_resolved mode=%s latency_ms=%d raw_digest=%s resolved_digest=%s resolved_source=%s",
                result.applied_mode,
                0,
                coordinator._text_digest(raw_text),
                coordinator._text_digest(result.text),
                "raw",
            )
        else:
            result = await coordinator._resolve_final_text(raw_text)

        committed_text, committed_source = coordinator._resolve_committed_text(raw_text, result)
        coordinator.logger.info(
            "final_commit_selected configured=%s actual=%s raw_digest=%s resolved_digest=%s committed_digest=%s",
            coordinator.config.final_commit_source,
            committed_source,
            coordinator._text_digest(raw_text),
            coordinator._text_digest(result.text),
            coordinator._text_digest(committed_text),
        )

        # 上屏处理
        if committed_source == "polished" and result.text and result.text != raw_text:
            await coordinator.overlay_service.submit_final(result.text, kind="final_committed")

        coordinator.set_status(
            coordinator._status_for_final_result(
                result,
                raw_text,
                committed_text=committed_text,
                committed_source=committed_source,
            )
        )

        if coordinator.console:
            print(f"\r[最终] {committed_text}          ", flush=True)

        # TIP 提交或本地注入
        if (
            coordinator._tip_primary_active
            and coordinator._tip_session_id is not None
            and result.fallback_reason is None
        ):
            tip_result = await coordinator._tip_gateway.commit_resolved_final(
                session_id=coordinator._tip_session_id,
                text=committed_text,
            )
            if tip_result.success:
                coordinator.logger.info(
                    "tip_success session_id=%s committed_digest=%s",
                    coordinator._tip_session_id,
                    coordinator._text_digest(committed_text),
                )
            else:
                fallback_ready = await coordinator._activate_tip_fallback(
                    reason=tip_result.reason or ("tip_timeout" if tip_result.timeout else "tip_failure")
                )
                if fallback_ready:
                    await coordinator._inject_final(committed_text)
        else:
            if coordinator._tip_primary_active:
                fallback_ready = await coordinator._activate_tip_fallback(
                    reason=result.fallback_reason or "tip_commit_unavailable"
                )
                if fallback_ready:
                    await coordinator._inject_final(committed_text)
            else:
                await coordinator._inject_final(committed_text)

    await coordinator.overlay_service.hide("finished")

    # 空文本处理
    if not raw_text:
        if coordinator._should_warn_low_input():
            coordinator.set_status("未检测到有效麦克风输入,请检查麦克风静音/增益,或在设置中切换麦克风")
        elif not coordinator._status.startswith("识别失败"):
            coordinator.set_status("空闲")

    await coordinator._clear_active_session()
    coordinator._finished_event_started_at = None


__all__ = ["_handle_worker_event"]