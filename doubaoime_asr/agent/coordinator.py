"""
VoiceInputCoordinator - 精简协调器。

集成 SessionManager、OverlayService、InjectionService、HotkeyService，
通过委托模式将 Controller 逻辑分散到各 Service。
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Literal

from .config import (
    AgentConfig,
    FINAL_COMMIT_SOURCE_RAW,
    POLISH_MODE_OFF,
    POLISH_MODE_OLLAMA,
    STREAMING_TEXT_MODE_SAFE_INLINE,
    SUPPORTED_CAPTURE_OUTPUT_POLICIES,
    SUPPORTED_FINAL_COMMIT_SOURCES,
    SUPPORTED_INJECTION_POLICIES,
    SUPPORTED_POLISH_MODES,
    SUPPORTED_STREAMING_TEXT_MODES,
)
from .config_update_plan import build_config_update_plan, polisher_config_changed
from .events import (
    AudioLevelEvent,
    ConfigChangeEvent,
    ErrorEvent,
    FallbackRequiredEvent,
    FinalResultEvent,
    FinishedEvent,
    HotkeyPressEvent,
    HotkeyReleaseEvent,
    InterimResultEvent,
    ReadyEvent,
    RestartAsAdminEvent,
    StopEvent,
    StreamingStartedEvent,
    VoiceInputEvent,
    WorkerExitEvent,
    WorkerReadyEvent,
    WorkerStatusEvent,
    ServiceResolvedFinalEvent,
    parse_worker_event,
)
from .asr_preflight import ASRPreflightGate
from .settings_window import SettingsWindowController
from .hotkey_service import HotkeyService
from .injection_service import InjectionService
from .interim_dispatcher import DebouncedInterimDispatcher
from .input_injector import FocusChangedError, FocusTarget
from .overlay_service import OverlayService
from .runtime_logging import setup_named_logger
from .session_manager import Mode, SessionManager, WorkerSession, WorkerSessionState
from .service_session_manager import ServiceSessionManager
from .text_polisher import PolishResult, TextPolisher
from .tip_gateway import NullTipGateway, TipGateway, TipGatewayResult
from .transcript_utils import TranscriptAccumulator
from .win_audio_output import AudioOutputMuteError, SystemOutputMuteGuard
from .win_hotkey import normalize_hotkey, vk_from_hotkey, vk_to_display, vk_to_hotkey
from .win_privileges import is_current_process_elevated, restart_as_admin
from .coordinator_cli import build_arg_parser, build_config_from_args, normalize_cli_hotkey
from .coordinator_config_runtime import (
    apply_config,
    preview_settings_overlay,
    polisher_config_changed_wrapper,
    run_preview_overlay,
)
from .coordinator_tray import start_tray
from .coordinator_privileges import (
    _check_foreground_elevation,
    _clear_elevation_warning,
    _elevation_status_message,
    _handle_restart_as_admin,
    _record_elevation_warning,
    _watch_foreground_target,
)
from .coordinator_transcript_flow import (
    aggregate_session_text,
    close_interim_dispatcher,
    concat_transcript_text_module,
    current_target_profile,
    ensure_interim_dispatcher,
    flush_interim_dispatcher,
    flush_interim_snapshot,
    next_segment_index,
    record_session_text,
    resolve_segment_index,
    submit_interim_snapshot,
    text_digest,
)
from .coordinator_finalization import (
    _inject_final,
    _resolve_final_text,
    _resolve_committed_text,
    _status_for_final_result,
    _status_for_error,
)
from .coordinator_worker_events import _handle_worker_event


Mode = Literal["recognize", "inject"]
FOREGROUND_POLL_INTERVAL_S = 0.5
RECOGNIZE_ONLY_STATUS = "启动识别中（仅识别，不自动上屏）…"
LOW_INPUT_AUDIO_LEVEL_THRESHOLD = 0.01
LOW_INPUT_STATUS = "未检测到有效麦克风输入，请检查麦克风静音/增益，或在设置中切换麦克风"


class VoiceInputCoordinator:
    """语音输入协调器，集成各 Service 并协调事件处理。

    Coordinator 负责：
    1. 事件循环调度
    2. Service 间协调
    3. 配置更新广播
    4. 状态管理

    各 Service 负责具体实现：
    - SessionManager: Worker 进程生命周期
    - OverlayService: 浮层显示控制
    - InjectionService: 文本注入逻辑
    - HotkeyService: 热键监听
    """

    def __init__(
        self,
        config: AgentConfig,
        *,
        mode: Mode | None = None,
        enable_tray: bool = True,
        console: bool = False,
        launch_args: list[str] | None = None,
        tip_gateway: TipGateway | None = None,
    ) -> None:
        """初始化 Coordinator。"""
        self.config = config
        self.mode = mode or config.mode
        self.config.mode = self.mode
        self.enable_tray = enable_tray
        self.console = console
        self.launch_args = list(launch_args or [])

        # 日志
        self.logger = setup_named_logger(
            "doubaoime_asr.agent.coordinator",
            config.default_controller_log_path(),
        )

        # 状态
        self._status = "空闲"
        self._status_lock = threading.Lock()
        self._event_queue: asyncio.Queue[VoiceInputEvent] = asyncio.Queue()
        self._stopping = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._settings_controller: SettingsWindowController | None = None
        self._pending_listener_rebind = False
        self._pending_worker_restart = False
        self._pending_polisher_warmup = False
        self._polisher_warmup_task: asyncio.Task[None] | None = None
        self._foreground_watch_task: asyncio.Task[None] | None = None
        self._preview_lock = threading.Lock()
        self._preview_counter = 0
        self._transcript = TranscriptAccumulator()
        self._session_peak_audio_level = 0.0
        self._session_saw_audio_level = False
        self._session_received_transcript = False
        self._tip_gateway: TipGateway = tip_gateway or NullTipGateway()
        self._tip_session_id: str | None = None
        self._tip_primary_active = False
        self._pending_service_resolved_final: ServiceResolvedFinalEvent | None = None
        self._pending_service_fallback_reason: str | None = None

        # 初始化 Services
        session_backend = os.environ.get("DOUBAO_AGENT_SESSION_BACKEND", "").strip().lower()
        session_manager_cls = ServiceSessionManager if session_backend == "service" else SessionManager
        self.session_manager = session_manager_cls(
            config,
            self.logger,
            on_event=self._forward_session_manager_event,
        )
        self.overlay_service = OverlayService(self.logger, config)
        self.injection_service = InjectionService(self.logger, config)
        self.hotkey_service = HotkeyService(self.logger)
        self.text_polisher = TextPolisher(self.logger, config)
        self._asr_preflight = ASRPreflightGate(self.logger)
        self.capture_output_guard = SystemOutputMuteGuard(
            self.logger,
            policy=config.capture_output_policy,
        )

        # 进程状态
        self._process_elevated = is_current_process_elevated() is True
        self.injection_service.set_process_elevated(self._process_elevated)
        self._elevation_warning_message: str | None = None
        self._last_elevation_warning_key: tuple[int | None, int | None, str | None] | None = None

        # Tray
        self._tray_icon = None
        self._tray_thread: threading.Thread | None = None

        # 会话状态（由 _transcript 持有，兼容属性暴露旧访问路径）
        self._finished_event_started_at: float | None = None
        self._interim_dispatcher: DebouncedInterimDispatcher | None = None
        self._last_interim_flush_seq = 0

    @property
    def _segment_texts(self) -> dict[int, str]:
        """分段文本集合。"""
        return self._transcript.segment_texts

    @_segment_texts.setter
    def _segment_texts(self, value: dict[int, str]) -> None:
        """设置分段文本集合。"""
        self._transcript.segment_texts = value

    @property
    def _finalized_segment_indexes(self) -> set[int]:
        """已完成的分段索引集合。"""
        return self._transcript.finalized_segment_indexes

    @_finalized_segment_indexes.setter
    def _finalized_segment_indexes(self, value: set[int]) -> None:
        """设置已完成的分段索引集合。"""
        self._transcript.finalized_segment_indexes = value

    @property
    def _active_segment_index(self) -> int | None:
        """当前活跃分段索引。"""
        return self._transcript.active_segment_index

    @_active_segment_index.setter
    def _active_segment_index(self, value: int | None) -> None:
        """设置当前活跃分段索引。"""
        self._transcript.active_segment_index = value

    @property
    def _last_displayed_raw_final_text(self) -> str:
        """最后显示的原始最终文本。"""
        return self._transcript.last_displayed_raw_final_text

    @_last_displayed_raw_final_text.setter
    def _last_displayed_raw_final_text(self, value: str) -> None:
        """设置最后显示的原始最终文本。"""
        self._transcript.last_displayed_raw_final_text = value

    # ===== 状态管理 =====

    def set_status(self, value: str) -> None:
        """更新状态显示。"""
        with self._status_lock:
            if self._status == value:
                return
            self._status = value
        if self.console:
            print(value, flush=True)
        self.logger.info("status=%s", value)
        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.update_menu()

    def get_status(self) -> str:
        """获取当前状态。"""
        with self._status_lock:
            return self._status

    # ===== 事件发射 =====

    def _emit(self, event: VoiceInputEvent) -> None:
        """发射事件到队列。"""
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon(self._event_queue.put_nowait, event)
        except RuntimeError:
            pass

    def _emit_threadsafe(self, loop: asyncio.AbstractEventLoop, event: VoiceInputEvent) -> None:
        """线程安全发射事件。"""
        loop.call_soon_threadsafe(self._event_queue.put_nowait, event)

    def _forward_session_manager_event(self, event: VoiceInputEvent) -> None:
        """桥接 SessionManager 事件到协调器事件队列。"""
        if self._loop is not None:
            self._emit_threadsafe(self._loop, event)
            return
        self._emit(event)

    # ===== 主运行循环 =====

    async def run(self) -> int:
        """主事件循环。"""
        if sys.platform != "win32":
            print("当前稳定版仅支持 Windows。", file=sys.stderr)
            return 1

        self.config.save()
        if self.console:
            self._print_startup_info()

        # 启动 Services
        self.overlay_service.start()
        self.set_status("空闲")

        loop = asyncio.get_running_loop()
        self._loop = loop

        # 启动热键监听
        self.hotkey_service.on_press(lambda: self._emit_threadsafe(loop, HotkeyPressEvent()))
        self.hotkey_service.on_release(lambda: self._emit_threadsafe(loop, HotkeyReleaseEvent()))
        self.hotkey_service.start(self.config.effective_hotkey_vk())

        # 前景窗口监控（非管理员时）
        if not self._process_elevated:
            self._foreground_watch_task = asyncio.create_task(
                self._watch_foreground_target(),
                name="doubao-foreground-watch",
            )

        # 润色预热
        self._schedule_polisher_warmup("startup")

        # 设置窗口控制器
        self._settings_controller = SettingsWindowController(
            logger=self.logger,
            get_current_config=lambda: self.config,
            on_save=lambda config: self._emit_threadsafe(loop, ConfigChangeEvent(config=config)),
            on_preview=self._preview_settings_overlay,
        )

        # 启动 Tray
        if self.enable_tray:
            self._start_tray(loop)

        # 预热 Worker
        try:
            await self.session_manager.ensure_worker()
        except Exception:
            self.logger.exception("worker_prewarm_failed")

        # 主事件循环
        try:
            while not self._stopping:
                event = await self._event_queue.get()
                try:
                    await self._handle_event(event)
                except Exception:
                    self.logger.exception("coordinator_event_failed event=%s", event.event_type)
                    self.set_status("协调器异常，请查看 controller.log")
                    await self._terminate_worker()
        except KeyboardInterrupt:
            self.stop()
        finally:
            await self._shutdown()
        return 0

    async def _handle_event(self, event: VoiceInputEvent) -> None:
        """处理单个事件。"""
        event_type = event.event_type

        if isinstance(event, HotkeyPressEvent):
            await self._handle_press()
        elif isinstance(event, HotkeyReleaseEvent):
            await self._handle_release()
        elif isinstance(event, ConfigChangeEvent):
            if event.config is not None:
                if event.preview_only:
                    await self._run_preview_overlay(event.config, preview_id=event.preview_id)
                else:
                    await self._apply_config(event.config)
        elif isinstance(event, RestartAsAdminEvent):
            await self._handle_restart_as_admin()
        elif isinstance(event, StopEvent):
            pass  # 循环会检测 _stopping
        elif isinstance(event, WorkerExitEvent):
            await self._handle_worker_exit(event.session_id, event.exit_code)
        else:
            # Worker 事件通过 SessionManager 处理
            session = self.session_manager.get_session()
            if session is None:
                return
            await self._handle_worker_event(event)

    # ===== 热键处理 =====

    async def _handle_press(self) -> None:
        """处理热键按下。"""
        self.logger.info("hotkey_down")
        preflight = await self._asr_preflight.ensure_available(
            self.config.credential_path,
            auto_rotate_device=self.config.auto_rotate_device
        )
        if not preflight.ok:
            self.set_status(f"ASR 不可用: {preflight.message or preflight.stage}")
            return
        session = await self.session_manager.ensure_worker()
        if session.state == WorkerSessionState.STREAMING:
            return

        # 目标捕获
        target: FocusTarget | None = None
        inline_streaming_enabled = False
        if self.mode == "inject":
            target = self.injection_service.capture_target()
            if target is None:
                self.set_status("未检测到可写入焦点")
                return
            if self.injection_service.target_requires_admin(target):
                self._record_elevation_warning(target, log_tag="press_blocked_elevated_target")
                return
            self._clear_elevation_warning()
            self.logger.info(
                "captured_target hwnd=%s focus_hwnd=%s process=%s terminal=%s text_profile=%s elevated=%s",
                target.hwnd,
                target.focus_hwnd,
                target.process_name,
                target.terminal_kind,
                target.text_input_profile,
                target.is_elevated,
            )
            if self.injection_service.should_enable_inline_streaming(target):
                inline_streaming_enabled = True
        else:
            self.logger.info("inject_skipped reason=recognize_mode phase=session_start")

        self._tip_session_id = None
        self._tip_primary_active = False
        self._pending_service_resolved_final = None
        self._pending_service_fallback_reason = None
        self._reset_session_diagnostics()
        if self.mode == "inject" and self._tip_gateway.is_available():
            try:
                tip_result = await self._tip_gateway.begin_session(session_id=str(session.session_id), target=target)
                if tip_result.success:
                    self._tip_session_id = str(session.session_id)
                    self._tip_primary_active = True
                    self.logger.info("tip_primary_engaged session_id=%s", self._tip_session_id)
                else:
                    self.logger.info(
                        "tip_failure session_id=%s phase=begin_session reason=%s",
                        session.session_id,
                        tip_result.reason,
                    )
            except Exception:
                self.logger.exception("tip_start_failed")
                # TIP 启动失败不影响主流程，继续运行

        # 开始会话
        self.session_manager.begin_session(target, self.mode)
        composition = self.injection_service.begin_session(
            target,
            self.mode,
            inline_streaming_enabled=inline_streaming_enabled,
        )
        await self._close_interim_dispatcher()
        self._interim_dispatcher = DebouncedInterimDispatcher(
            debounce_ms=self.config.render_debounce_ms,
            logger=self.logger,
            on_flush=self._flush_interim_snapshot,
        )

        # 激活静音
        capture_output_warning = self._activate_capture_output()

        # 发送 START 命令
        try:
            await self.session_manager.send_command("START")
        except Exception:
            if self._tip_primary_active:
                await self._cancel_tip_session("worker_start_failed")
            self.session_manager.clear_session()
            await self._close_interim_dispatcher()
            self.injection_service.end_session()
            restore_warning = self._release_capture_output()
            self.logger.exception("worker_start_command_failed")
            self.set_status(restore_warning or "启动识别失败，请查看 controller.log")
            await self.session_manager.restart_worker()
            return

        await self.overlay_service.show_microphone("正在聆听…")
        self.set_status(self._session_start_status(capture_output_warning))

        # 清除分段文本状态
        self._transcript.reset()

    async def _handle_release(self) -> None:
        """处理热键释放。"""
        self.logger.info("hotkey_up")
        session = self.session_manager.get_session()
        if session is None or session.state != WorkerSessionState.STREAMING:
            return
        if session.stop_sent:
            return
        # 未就绪时延迟发送
        if not session.ready:
            session.pending_stop = True
            self.logger.info("worker_stop_deferred reason=not_ready")
            self.set_status("等待录音就绪…")
            return
        await self._send_stop("worker_stop_sent", "正在转写…")

    async def _send_stop(self, log_tag: str, status: str) -> None:
        """发送 STOP 命令。"""
        session = self.session_manager.get_session()
        if session is None:
            return
        await self.session_manager.send_stop()
        await self.overlay_service.stop_microphone()
        self.logger.info(log_tag)
        self.set_status(status)

    # ===== Worker 事件处理 =====

    async def _handle_worker_event(self, event: VoiceInputEvent) -> None:
        """处理 Worker 发送的事件。"""
        await _handle_worker_event(self, event)

    async def _send_stop_if_needed(self) -> None:
        """延迟发送 STOP（如果需要）。"""
        session = self.session_manager.get_session()
        if session is None or session.stop_sent or not session.pending_stop:
            return
        await self._send_stop("worker_stop_sent_after_ready", "正在转写…")

    # ===== 文本注入 =====

    async def _inject_final(self, text: str) -> None:
        """执行最终文本注入。"""
        await _inject_final(self, text)

    async def _resolve_final_text(self, raw_text: str) -> PolishResult:
        """润色最终文本。"""
        return await _resolve_final_text(self, raw_text)

    # ===== 会话状态管理 =====

    def _record_session_text(
        self,
        event: VoiceInputEvent,
        text: str,
        *,
        is_final: bool,
    ) -> str:
        """记录分段文本。"""
        return record_session_text(self, event, text, is_final=is_final)

    def _resolve_segment_index(self, event: VoiceInputEvent, *, is_final: bool) -> int:
        """解析分段索引。"""
        return resolve_segment_index(self, event, is_final=is_final)

    def _next_segment_index(self) -> int:
        """获取下一个分段索引。"""
        return next_segment_index(self)

    def _aggregate_session_text(self) -> str:
        """聚合分段文本。"""
        return aggregate_session_text(self)

    async def _submit_interim_snapshot(self, text: str) -> int:
        """提交临时结果快照。"""
        return await submit_interim_snapshot(self, text)

    async def _flush_interim_snapshot(self, seq: int, text: str) -> None:
        """刷新临时结果快照。"""
        await flush_interim_snapshot(self, seq, text)

    async def _flush_interim_dispatcher(self, *, reason: str) -> None:
        """刷新临时结果调度器。"""
        await flush_interim_dispatcher(self, reason=reason)

    async def _close_interim_dispatcher(self) -> None:
        """关闭临时结果调度器。"""
        await close_interim_dispatcher(self)

    def _ensure_interim_dispatcher(self) -> DebouncedInterimDispatcher:
        """确保临时结果调度器存在。"""
        return ensure_interim_dispatcher(self)

    def _current_target_profile(self) -> str:
        """获取当前目标的文本配置。"""
        return current_target_profile(self)

    def _text_digest(self, text: str) -> str:
        """计算文本摘要。"""
        return text_digest(text)

    def _reset_session_diagnostics(self) -> None:
        """重置会话诊断信息。"""
        self._session_peak_audio_level = 0.0
        self._session_saw_audio_level = False
        self._session_received_transcript = False

    def _record_audio_level(self, level: float) -> None:
        """记录音频级别。"""
        self._session_saw_audio_level = True
        self._session_peak_audio_level = max(
            self._session_peak_audio_level,
            max(0.0, float(level)),
        )

    def _should_warn_low_input(self) -> bool:
        """检查是否应警告低输入。"""
        return (
            self._session_saw_audio_level
            and not self._session_received_transcript
            and self._session_peak_audio_level < LOW_INPUT_AUDIO_LEVEL_THRESHOLD
        )

    def _resolve_committed_text(self, raw_text: str, result: PolishResult) -> tuple[str, str]:
        """解析提交的文本。"""
        return _resolve_committed_text(self, raw_text, result)

    def _concat_transcript_text(self, current: str, incoming: str) -> str:
        """拼接文本，处理重叠。"""
        return concat_transcript_text_module(current, incoming)

    # ===== Worker 退出处理 =====

    async def _handle_worker_exit(self, session_id: int, exit_code: int) -> None:
        """处理 Worker 进程退出。"""
        session = self.session_manager.get_session()
        if session is None or session.session_id != session_id:
            self.logger.info(
                "worker_exit_ignored session_id=%s current_session_id=%s code=%s",
                session_id,
                session.session_id if session else None,
                exit_code,
            )
            return

        self.logger.info("worker_exit code=%s", exit_code)
        if self._tip_primary_active:
            await self._cancel_tip_session("worker_exit")
        if not self._stopping and exit_code != 0 and not self._status.startswith("识别失败"):
            self.set_status(f"识别进程异常退出: {exit_code}")

        restore_warning = self._release_capture_output()
        if restore_warning is not None:
            self.set_status(restore_warning)

        await self._close_interim_dispatcher()
        await self.session_manager.terminate_worker()

        if not self._stopping:
            self._apply_pending_listener_rebind("listener_rebind_failed_after_worker_exit")

        if not self._stopping and self._pending_worker_restart:
            self._pending_worker_restart = False
            with contextlib.suppress(Exception):
                await self.session_manager.ensure_worker()

    async def _clear_active_session(self) -> None:
        """清除活跃会话。"""
        await self._close_interim_dispatcher()
        self.session_manager.clear_session()
        self.injection_service.end_session()
        self._tip_session_id = None
        self._tip_primary_active = False
        self._pending_service_resolved_final = None
        self._pending_service_fallback_reason = None
        self._reset_session_diagnostics()
        restore_warning = self._release_capture_output()
        if restore_warning is not None:
            self.set_status(restore_warning)
        self._apply_pending_listener_rebind("listener_rebind_failed_after_session")
        if self._pending_worker_restart:
            self._pending_worker_restart = False
            await self.session_manager.restart_worker()
        if self._pending_polisher_warmup:
            self._pending_polisher_warmup = False
            self._schedule_polisher_warmup("after_session")

    async def _cancel_tip_session(self, reason: str) -> TipGatewayResult:
        """取消 TIP 会话。"""
        if not self._tip_primary_active or self._tip_session_id is None:
            return TipGatewayResult(success=False, reason="tip_not_active", cleanup_performed=False)
        result = await self._tip_gateway.cancel_session(session_id=self._tip_session_id, reason=reason)
        self.logger.info(
            "tip_cancel session_id=%s reason=%s cleanup=%s",
            self._tip_session_id,
            reason,
            result.cleanup_performed,
        )
        return result

    async def _activate_tip_fallback(self, *, reason: str) -> bool:
        """激活 TIP fallback。"""
        if not self._tip_primary_active:
            return False
        cleanup_result = await self._cancel_tip_session(reason)
        cleanup_ready = cleanup_result.success and cleanup_result.cleanup_performed is not False
        if not cleanup_ready:
            self.injection_service.handle_focus_changed()
            self.logger.warning(
                "fallback_blocked session_id=%s fallback_reason=%s composition_cleanup=%s",
                self._tip_session_id,
                reason,
                cleanup_result.cleanup_performed,
            )
            self._tip_primary_active = False
            self.set_status(f"TIP cleanup 失败，已阻止 fallback 注入: {reason}")
            return False
        self.logger.info(
            "fallback_activated session_id=%s fallback_reason=%s composition_cleanup=%s writer_owner=legacy",
            self._tip_session_id,
            reason,
            cleanup_result.cleanup_performed,
        )
        self._tip_primary_active = False
        if reason == "tip_timeout":
            self.set_status("TIP 超时，已切换 fallback")
        else:
            self.set_status(f"TIP 失败，已切换 fallback: {reason}")
        return True

    def _preview_settings_overlay(self, config: AgentConfig) -> None:
        """在设置页预览浮层样式。"""
        preview_settings_overlay(self, config)

    async def _run_preview_overlay(self, config: AgentConfig, *, preview_id: int) -> None:
        """应用临时配置并显示短暂预览。"""
        await run_preview_overlay(self, config, preview_id=preview_id)

    # ===== 配置更新 =====

    async def _apply_config(self, new_config: AgentConfig) -> None:
        """应用新配置。"""
        await apply_config(self, new_config, self.logger)

    def _polisher_config_changed(self, old_config: AgentConfig, new_config: AgentConfig) -> bool:
        """检查润色配置是否变化。"""
        return polisher_config_changed_wrapper(self, old_config, new_config)

    # ===== 管理员权限处理 =====

    def _record_elevation_warning(self, target: FocusTarget, *, log_tag: str) -> None:
        """记录管理员权限警告。"""
        _record_elevation_warning(self, target, log_tag=log_tag)

    def _clear_elevation_warning(self) -> None:
        """清除管理员权限警告。"""
        _clear_elevation_warning(self)

    def _elevation_status_message(self, target: FocusTarget) -> str:
        """生成管理员权限状态消息。"""
        return _elevation_status_message(self, target)

    async def _handle_restart_as_admin(self) -> None:
        """处理管理员重启请求。"""
        await _handle_restart_as_admin(self)

    async def _watch_foreground_target(self) -> None:
        """监控前景窗口权限。"""
        await _watch_foreground_target(self)

    def _check_foreground_elevation(self) -> None:
        """检查前景窗口是否需要管理员权限。"""
        _check_foreground_elevation(self)

    # ===== 辅助方法 =====

    def _apply_pending_listener_rebind(self, log_tag: str) -> None:
        """应用延迟的热键更新。"""
        if not self._pending_listener_rebind:
            return
        self._pending_listener_rebind = False
        try:
            self.hotkey_service.update_hotkey(self.config.effective_hotkey_vk())
        except Exception:
            self.logger.exception(log_tag)

    def _activate_capture_output(self) -> str | None:
        """激活录音静音。"""
        try:
            self.capture_output_guard.activate()
        except AudioOutputMuteError:
            self.logger.exception("capture_output_activate_failed")
            return "启动识别中…（自动静音失败，请查看 controller.log）"
        return None

    def _release_capture_output(self) -> str | None:
        """释放录音静音。"""
        try:
            self.capture_output_guard.release()
        except AudioOutputMuteError:
            self.logger.exception("capture_output_release_failed")
            return "恢复系统输出失败，请查看 controller.log"
        return None

    def _session_start_status(self, capture_output_warning: str | None) -> str:
        """生成会话启动状态。"""
        if capture_output_warning is not None:
            return capture_output_warning
        if self.mode == "inject":
            return "启动识别中…"
        return RECOGNIZE_ONLY_STATUS

    def _status_for_final_result(
        self,
        result: PolishResult,
        raw_text: str,
        *,
        committed_text: str | None = None,
        committed_source: str | None = None,
    ) -> str:
        """生成最终结果状态。"""
        return _status_for_final_result(
            self,
            result,
            raw_text,
            committed_text=committed_text,
            committed_source=committed_source,
        )

    def _status_for_error(self, message: str) -> str:
        """生成错误状态消息。"""
        return _status_for_error(self, message)

    def _mode_display_label(self, mode: Mode | None = None) -> str:
        """生成模式显示标签。"""
        resolved = mode or self.mode
        if resolved == "inject":
            return "自动上屏"
        return "仅识别（不自动上屏）"

    def _print_startup_info(self) -> None:
        """打印启动信息。"""
        print("=" * 50)
        print("豆包语音输入 - 全局版")
        print("=" * 50)
        print(f"模式: {self._mode_display_label()}")
        print(f"热键: {self.config.effective_hotkey_display()}")
        print("使用方式：按住热键说话，松开结束。")
        print("按 Ctrl+C 退出。")
        print()

    def _schedule_polisher_warmup(self, reason: str) -> None:
        """调度润色预热。"""
        if self._loop is None:
            return
        if self._polisher_warmup_task is not None and not self._polisher_warmup_task.done():
            self._polisher_warmup_task.cancel()
        if self.config.polish_mode != POLISH_MODE_OLLAMA or not self.config.ollama_warmup_enabled:
            self._polisher_warmup_task = None
            return
        self._polisher_warmup_task = asyncio.create_task(
            self._run_polisher_warmup(reason),
            name="doubao-polisher-warmup",
        )

    async def _run_polisher_warmup(self, reason: str) -> None:
        """执行润色预热。"""
        try:
            warmed = await self.text_polisher.warmup()
            self.logger.info("text_polisher_warmup_finished reason=%s warmed=%s", reason, warmed)
        except asyncio.CancelledError:
            self.logger.info("text_polisher_warmup_cancelled reason=%s", reason)
            raise
        except Exception:
            self.logger.exception("text_polisher_warmup_failed reason=%s", reason)
        finally:
            current_task = asyncio.current_task()
            if self._polisher_warmup_task is current_task:
                self._polisher_warmup_task = None

    async def _cancel_polisher_warmup(self) -> None:
        """取消润色预热。"""
        if self._polisher_warmup_task is None or self._polisher_warmup_task.done():
            self._polisher_warmup_task = None
            return
        self._polisher_warmup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._polisher_warmup_task
        self._polisher_warmup_task = None

    async def _cancel_foreground_watch(self) -> None:
        """取消前景监控。"""
        if self._foreground_watch_task is None or self._foreground_watch_task.done():
            self._foreground_watch_task = None
            return
        self._foreground_watch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._foreground_watch_task
        self._foreground_watch_task = None

    async def _terminate_worker(self) -> None:
        """终止 Worker。"""
        await self.overlay_service.hide("terminate_worker")
        restore_warning = self._release_capture_output()
        if restore_warning is not None and not self._stopping:
            self.set_status(restore_warning)
        await self.session_manager.terminate_worker()

    async def _shutdown(self) -> None:
        """关闭所有资源。"""
        await self._cancel_foreground_watch()
        await self._cancel_polisher_warmup()
        await self._close_interim_dispatcher()
        self._release_capture_output()
        await self.session_manager.terminate_worker()
        self.hotkey_service.stop()
        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.stop()
        if self._tray_thread is not None:
            self._tray_thread.join(timeout=2)
            self._tray_thread = None
        if self._settings_controller is not None:
            self._settings_controller.close()
            self._settings_controller = None
        self.overlay_service.stop()

    def stop(self) -> None:
        """停止协调器。"""
        self._stopping = True
        self.session_manager.stop()
        with contextlib.suppress(Exception):
            self._event_queue.put_nowait(StopEvent())

    # ===== Tray =====

    def _start_tray(self, loop: asyncio.AbstractEventLoop) -> None:
        """启动系统托盘。"""
        start_tray(self, loop)
