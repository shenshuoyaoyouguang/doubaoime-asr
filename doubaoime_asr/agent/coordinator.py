"""
VoiceInputCoordinator - 精简协调器。

集成 SessionManager、OverlayService、InjectionService、HotkeyService，
通过委托模式将 Controller 逻辑分散到各 Service。
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
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
    POLISH_MODE_OFF,
    POLISH_MODE_OLLAMA,
    STREAMING_TEXT_MODE_SAFE_INLINE,
    SUPPORTED_CAPTURE_OUTPUT_POLICIES,
    SUPPORTED_INJECTION_POLICIES,
    SUPPORTED_POLISH_MODES,
    SUPPORTED_STREAMING_TEXT_MODES,
)
from .events import (
    AudioLevelEvent,
    ConfigChangeEvent,
    ErrorEvent,
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
    parse_worker_event,
)
from .settings_window import SettingsWindowController
from .hotkey_service import HotkeyService
from .injection_service import InjectionService
from .input_injector import FocusChangedError, FocusTarget
from .overlay_service import OverlayService
from .runtime_logging import setup_named_logger
from .session_manager import Mode, SessionManager, WorkerSession, WorkerSessionState
from .text_polisher import PolishResult, TextPolisher
from .win_audio_output import AudioOutputMuteError, SystemOutputMuteGuard
from .win_hotkey import normalize_hotkey, vk_from_hotkey, vk_to_display, vk_to_hotkey
from .win_privileges import is_current_process_elevated, restart_as_admin


Mode = Literal["recognize", "inject"]
FOREGROUND_POLL_INTERVAL_S = 0.5
RECOGNIZE_ONLY_STATUS = "启动识别中（仅识别，不自动上屏）…"


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
    ) -> None:
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

        # 初始化 Services
        self.session_manager = SessionManager(
            config,
            self.logger,
            on_event=self._forward_session_manager_event,
        )
        self.overlay_service = OverlayService(self.logger, config)
        self.injection_service = InjectionService(self.logger, config)
        self.hotkey_service = HotkeyService(self.logger)
        self.text_polisher = TextPolisher(self.logger, config)
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

        # 会话状态（保留部分未迁移的状态）
        self._segment_texts: dict[int, str] = {}
        self._finalized_segment_indexes: set[int] = set()
        self._active_segment_index: int | None = None
        self._last_displayed_raw_final_text: str = ""
        self._finished_event_started_at: float | None = None

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
                "captured_target hwnd=%s focus_hwnd=%s process=%s terminal=%s elevated=%s",
                target.hwnd,
                target.focus_hwnd,
                target.process_name,
                target.terminal_kind,
                target.is_elevated,
            )
            if self.injection_service.should_enable_inline_streaming(target):
                inline_streaming_enabled = True
        else:
            self.logger.info("inject_skipped reason=recognize_mode phase=session_start")

        # 开始会话
        self.session_manager.begin_session(target, self.mode)
        composition = self.injection_service.begin_session(
            target,
            self.mode,
            inline_streaming_enabled=inline_streaming_enabled,
        )

        # 激活静音
        capture_output_warning = self._activate_capture_output()

        # 发送 START 命令
        try:
            await self.session_manager.send_command("START")
        except Exception:
            self.session_manager.clear_session()
            self.injection_service.end_session()
            restore_warning = self._release_capture_output()
            self.logger.exception("worker_start_command_failed")
            self.set_status(restore_warning or "启动识别失败，请查看 controller.log")
            await self.session_manager.restart_worker()
            return

        await self.overlay_service.show_microphone("正在聆听…")
        self.set_status(self._session_start_status(capture_output_warning))

        # 清除分段文本状态
        self._segment_texts.clear()
        self._finalized_segment_indexes.clear()
        self._active_segment_index = None
        self._last_displayed_raw_final_text = ""

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
        session = self.session_manager.get_session()
        if session is None:
            return

        event_type = event.event_type
        self.logger.info("worker_event=%s", event_type)

        if isinstance(event, WorkerReadyEvent):
            session.process_ready = True
            return

        if isinstance(event, ReadyEvent):
            if session.state == WorkerSessionState.STREAMING:
                session.ready = True
                self.set_status("录音中，等待说话")
                await self._send_stop_if_needed()

        elif isinstance(event, StreamingStartedEvent):
            if session.state == WorkerSessionState.STREAMING:
                session.streaming_started = True
            self.logger.info(
                "worker_streaming_started chunks=%s bytes=%s",
                event.chunks,
                event.bytes,
            )
            await self._send_stop_if_needed()

        elif isinstance(event, WorkerStatusEvent):
            if event.message:
                self.set_status(event.message)

        elif isinstance(event, AudioLevelEvent):
            if session.state == WorkerSessionState.STREAMING:
                await self.overlay_service.update_microphone_level(event.level)

        elif isinstance(event, InterimResultEvent):
            if session.state != WorkerSessionState.STREAMING:
                return
            text = self._record_session_text(event, event.text, is_final=False)
            if self.console:
                print(f"\r[识别中] {text}", end="", flush=True)
            await self.overlay_service.submit_interim(text)
            await self.injection_service.apply_inline_interim(text)
            self.set_status(f"识别中: {text[-24:]}")

        elif isinstance(event, FinalResultEvent):
            if session.state != WorkerSessionState.STREAMING:
                return
            raw_text = self._record_session_text(event, event.text, is_final=True)
            self._last_displayed_raw_final_text = raw_text
            await self.overlay_service.submit_final(raw_text, kind="final_raw")
            await self.injection_service.prepare_final_text(raw_text)
            if not session.stop_sent:
                self.set_status(f"识别中: {raw_text[-24:]}")

        elif isinstance(event, ErrorEvent):
            await self.overlay_service.hide("error")
            self.set_status(f"识别失败: {event.message}")
            await self._clear_active_session()

        elif isinstance(event, FinishedEvent):
            self._finished_event_started_at = time.perf_counter()
            raw_text = self._aggregate_session_text()
            if raw_text:
                if raw_text != self._last_displayed_raw_final_text:
                    await self.overlay_service.submit_final(raw_text, kind="final_raw")
                    self._last_displayed_raw_final_text = raw_text
                if session.mode == "inject":
                    self.set_status("正在准备上屏…")
                if self.config.polish_mode == POLISH_MODE_OFF:
                    result = PolishResult(text=raw_text, applied_mode=POLISH_MODE_OFF, latency_ms=0)
                    self.logger.info("final_text_resolved mode=%s latency_ms=%d", result.applied_mode, 0)
                else:
                    result = await self._resolve_final_text(raw_text)
                if result.text and result.text != raw_text:
                    await self.overlay_service.submit_final(result.text, kind="final_committed")
                self.set_status(self._status_for_final_result(result, raw_text))
                if self.console:
                    print(f"\r[最终] {result.text}          ", flush=True)
                await self._inject_final(result.text)
            await self.overlay_service.hide("finished")
            if not raw_text and not self._status.startswith("识别失败"):
                self.set_status("空闲")
            await self._clear_active_session()
            self._finished_event_started_at = None

    async def _send_stop_if_needed(self) -> None:
        """延迟发送 STOP（如果需要）。"""
        session = self.session_manager.get_session()
        if session is None or session.stop_sent or not session.pending_stop:
            return
        await self._send_stop("worker_stop_sent_after_ready", "正在转写…")

    # ===== 文本注入 =====

    async def _inject_final(self, text: str) -> None:
        """执行最终文本注入。"""
        if not text:
            return
        session = self.session_manager.get_session()
        if session is None:
            return
        if session.mode != "inject":
            self.logger.info("inject_skipped reason=recognize_mode text_length=%s", len(text))
            return
        if self.injection_service.is_injection_blocked():
            return

        inject_started_at = time.perf_counter()
        try:
            result = await self.injection_service.inject_final(text)
            if result:
                finished_to_inject_ms = None
                stop_to_inject_ms = None
                if self._finished_event_started_at is not None:
                    finished_to_inject_ms = int((inject_started_at - self._finished_event_started_at) * 1000)
                if session.stop_sent_at is not None:
                    stop_to_inject_ms = int((inject_started_at - session.stop_sent_at) * 1000)
                self.logger.info(
                    "inject_success method=%s stop_to_inject_ms=%s finished_to_inject_ms=%s",
                    result.method,
                    stop_to_inject_ms,
                    finished_to_inject_ms,
                )
        except FocusChangedError:
            self.injection_service.handle_focus_changed()
            self.logger.warning("inject_focus_changed")
            self.set_status("焦点已变化，仅保留识别")
        except Exception:
            self.logger.exception("inject_final_failed")
            self.set_status("注入失败，仅保留识别")

    async def _resolve_final_text(self, raw_text: str) -> PolishResult:
        """润色最终文本。"""
        resolve_started_at = time.perf_counter()
        if self.config.polish_mode == POLISH_MODE_OLLAMA and raw_text.strip():
            self.set_status("润色中…")
        result = await self.text_polisher.polish(raw_text)
        self.logger.info(
            "final_text_resolved mode=%s latency_ms=%d",
            result.applied_mode,
            int((time.perf_counter() - resolve_started_at) * 1000),
        )
        return result

    # ===== 会话状态管理 =====

    def _record_session_text(
        self,
        event: VoiceInputEvent,
        text: str,
        *,
        is_final: bool,
    ) -> str:
        """记录分段文本。"""
        index = self._resolve_segment_index(event, is_final=is_final)
        self._segment_texts[index] = text
        return self._aggregate_session_text()

    def _resolve_segment_index(self, event: VoiceInputEvent, *, is_final: bool) -> int:
        """解析分段索引。"""
        segment_index = getattr(event, "segment_index", None)
        if segment_index is None:
            segment_index = self._active_segment_index if self._active_segment_index is not None else self._next_segment_index()
        if is_final:
            self._finalized_segment_indexes.add(segment_index)
            if self._active_segment_index == segment_index:
                self._active_segment_index = None
        else:
            self._active_segment_index = segment_index
        return segment_index

    def _next_segment_index(self) -> int:
        """获取下一个分段索引。"""
        if not self._segment_texts:
            return 0
        return max(self._segment_texts) + 1

    def _aggregate_session_text(self) -> str:
        """聚合分段文本。"""
        text = ""
        for _, segment in sorted(self._segment_texts.items()):
            if not segment:
                continue
            text = self._concat_transcript_text(text, segment)
        return text

    def _concat_transcript_text(self, current: str, incoming: str) -> str:
        """拼接文本，处理重叠。"""
        if not current:
            return incoming
        if not incoming:
            return current
        if incoming.startswith(current):
            return incoming
        if current.endswith(incoming):
            return current
        overlap = self._suffix_prefix_overlap(current, incoming)
        if overlap > 0:
            return current + incoming[overlap:]
        if current[-1].isascii() and current[-1].isalnum() and incoming[0].isascii() and incoming[0].isalnum():
            return f"{current} {incoming}"
        return current + incoming

    def _suffix_prefix_overlap(self, left: str, right: str) -> int:
        """计算后缀前缀重叠。"""
        max_overlap = min(len(left), len(right))
        for size in range(max_overlap, 0, -1):
            if left[-size:] == right[:size]:
                return size
        return 0

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
        if not self._stopping and exit_code != 0 and not self._status.startswith("识别失败"):
            self.set_status(f"识别进程异常退出: {exit_code}")

        restore_warning = self._release_capture_output()
        if restore_warning is not None:
            self.set_status(restore_warning)

        await self.session_manager.terminate_worker()

        if not self._stopping:
            self._apply_pending_listener_rebind("listener_rebind_failed_after_worker_exit")

        if not self._stopping and self._pending_worker_restart:
            self._pending_worker_restart = False
            with contextlib.suppress(Exception):
                await self.session_manager.ensure_worker()

    async def _clear_active_session(self) -> None:
        """清除活跃会话。"""
        self.session_manager.clear_session()
        self.injection_service.end_session()
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

    def _preview_settings_overlay(self, config: AgentConfig) -> None:
        """在设置页预览浮层样式。"""
        if self._loop is None:
            return
        if self.session_manager.is_streaming():
            self.set_status("录音中，暂不预览浮层")
            return
        with self._preview_lock:
            self._preview_counter += 1
            preview_id = self._preview_counter
        self._emit_threadsafe(self._loop, ConfigChangeEvent(config=config, preview_id=preview_id, preview_only=True))

    async def _run_preview_overlay(self, config: AgentConfig, *, preview_id: int) -> None:
        """应用临时配置并显示短暂预览。"""
        self.overlay_service.configure(config)
        try:
            await self.overlay_service.show_microphone("浮层预览：请确认字号、宽度与透明度")
            await asyncio.sleep(1.2)
        finally:
            with self._preview_lock:
                still_latest = preview_id == self._preview_counter
            if still_latest:
                await self.overlay_service.hide("settings_preview")
                self.overlay_service.configure(self.config)

    # ===== 配置更新 =====

    async def _apply_config(self, new_config: AgentConfig) -> None:
        """应用新配置。"""
        old_config = self.config
        old_mode = self.mode
        old_pending_listener_rebind = self._pending_listener_rebind
        old_pending_worker_restart = self._pending_worker_restart
        old_pending_polisher_warmup = self._pending_polisher_warmup

        hotkey_changed = old_config.effective_hotkey_vk() != new_config.effective_hotkey_vk()
        worker_changed = (
            old_config.credential_path != new_config.credential_path
            or old_config.microphone_device != new_config.microphone_device
        )
        polisher_changed = self._polisher_config_changed(old_config, new_config)
        session_active = self.session_manager.is_streaming()
        listener_rebound = False
        worker_restarted = False

        try:
            self.config = new_config
            self.mode = new_config.mode

            # 更新各 Service
            self.session_manager.config = new_config
            self.overlay_service.configure(new_config)
            self.injection_service.configure(new_config)
            self.text_polisher.configure(new_config)
            self.capture_output_guard.configure(new_config.capture_output_policy)

            if hotkey_changed:
                if session_active:
                    self._pending_listener_rebind = True
                else:
                    self.hotkey_service.update_hotkey(new_config.effective_hotkey_vk())
                    listener_rebound = True

            if worker_changed:
                if session_active:
                    self._pending_worker_restart = True
                else:
                    await self.session_manager.restart_worker()
                    worker_restarted = True

            if polisher_changed:
                if session_active:
                    self._pending_polisher_warmup = True
                else:
                    self._schedule_polisher_warmup("config_update")

            self.config.save()
        except Exception:
            self.logger.exception("apply_config_failed")
            # 回滚
            self.config = old_config
            self.mode = old_mode
            self._pending_listener_rebind = old_pending_listener_rebind
            self._pending_worker_restart = old_pending_worker_restart
            self._pending_polisher_warmup = old_pending_polisher_warmup
            self.session_manager.config = old_config
            self.overlay_service.configure(old_config)
            self.injection_service.configure(old_config)
            self.text_polisher.configure(old_config)
            self.capture_output_guard.configure(old_config.capture_output_policy)
            if listener_rebound:
                self.hotkey_service.update_hotkey(old_config.effective_hotkey_vk())
            if worker_restarted:
                await self.session_manager.restart_worker()
            with contextlib.suppress(Exception):
                self.config.save()
            self.set_status("设置保存失败，已恢复旧配置")
            return

        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.update_menu()

        if not session_active:
            if hotkey_changed:
                self.set_status(f"热键已更新为 {new_config.effective_hotkey_display()}")
            elif worker_changed:
                self.set_status("设置已保存并重启识别服务")
            elif polisher_changed:
                self.set_status("设置已保存并更新润色配置")
            else:
                self.set_status("设置已保存")
        else:
            self.logger.info("settings_saved_during_active_session")

    def _polisher_config_changed(self, old_config: AgentConfig, new_config: AgentConfig) -> bool:
        """检查润色配置是否变化。"""
        return any(
            (
                old_config.polish_mode != new_config.polish_mode,
                old_config.ollama_base_url != new_config.ollama_base_url,
                old_config.ollama_model != new_config.ollama_model,
                old_config.polish_timeout_ms != new_config.polish_timeout_ms,
                old_config.ollama_warmup_enabled != new_config.ollama_warmup_enabled,
                old_config.ollama_keep_alive != new_config.ollama_keep_alive,
                old_config.ollama_prompt_template != new_config.ollama_prompt_template,
            )
        )

    # ===== 管理员权限处理 =====

    def _record_elevation_warning(self, target: FocusTarget, *, log_tag: str) -> None:
        """记录管理员权限警告。"""
        message = self._elevation_status_message(target)
        key = (target.hwnd, target.process_id, target.process_name)
        if key != self._last_elevation_warning_key:
            self.logger.warning(
                "%s hwnd=%s pid=%s process=%s terminal=%s elevated=%s",
                log_tag,
                target.hwnd,
                target.process_id,
                target.process_name,
                target.terminal_kind,
                target.is_elevated,
            )
            self._last_elevation_warning_key = key
        self._elevation_warning_message = message
        self.set_status(message)

    def _clear_elevation_warning(self) -> None:
        """清除管理员权限警告。"""
        message = self._elevation_warning_message
        self._elevation_warning_message = None
        self._last_elevation_warning_key = None
        session = self.session_manager.get_session()
        if message and self._status == message and (session is None or session.state != WorkerSessionState.STREAMING):
            self.set_status("空闲")

    def _elevation_status_message(self, target: FocusTarget) -> str:
        """生成管理员权限状态消息。"""
        subject = "管理员终端" if target.is_terminal else "管理员窗口"
        if self.enable_tray:
            return f"{subject}需要以管理员身份运行代理；请从托盘选择\"以管理员重启\""
        return f"{subject}需要以管理员身份运行代理；请重新以管理员身份启动代理"

    async def _handle_restart_as_admin(self) -> None:
        """处理管理员重启请求。"""
        if self._process_elevated:
            self.set_status("代理已在管理员模式运行")
            return
        try:
            restarted = restart_as_admin(
                self.launch_args,
                executable=sys.executable,
                frozen=bool(getattr(sys, "frozen", False)),
            )
        except Exception:
            self.logger.exception("restart_as_admin_failed")
            self.set_status("管理员重启失败，请查看 controller.log")
            return
        if not restarted:
            self.logger.warning("restart_as_admin_declined")
            self.set_status("管理员重启已取消或被系统拒绝")
            return
        self.logger.info("restart_as_admin_requested args=%s", self.launch_args)
        self.set_status("正在以管理员身份重启…")
        self.stop()

    async def _watch_foreground_target(self) -> None:
        """监控前景窗口权限。"""
        try:
            while not self._stopping:
                self._check_foreground_elevation()
                await asyncio.sleep(FOREGROUND_POLL_INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("foreground_watch_failed")

    def _check_foreground_elevation(self) -> None:
        """检查前景窗口是否需要管理员权限。"""
        if self._process_elevated:
            return
        session = self.session_manager.get_session()
        if session is not None and session.state == WorkerSessionState.STREAMING:
            return
        try:
            target = self.injection_service.capture_target()
        except Exception:
            self.logger.exception("foreground_target_capture_failed")
            return
        if self.injection_service.target_requires_admin(target):
            self._record_elevation_warning(target, log_tag="foreground_elevated_target_detected")
            return
        self._clear_elevation_warning()

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

    def _status_for_final_result(self, result: PolishResult, raw_text: str) -> str:
        """生成最终结果状态。"""
        if result.applied_mode != "raw_fallback":
            return f"最终结果: {result.text[-24:]}"
        excerpt = raw_text[-18:]
        fallback_messages = {
            "timeout": f"润色超时，已使用原文: {excerpt}",
            "unavailable": f"润色不可用，已使用原文: {excerpt}",
            "no_model": f"未配置润色模型，已使用原文: {excerpt}",
            "invalid_response": f"润色结果无效，已使用原文: {excerpt}",
            "bad_prompt": f"润色提示词无效，已使用原文: {excerpt}",
        }
        return fallback_messages.get(result.fallback_reason or "", f"最终结果: {result.text[-24:]}")

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
        import pystray
        from PIL import Image, ImageDraw

        def build_icon():
            image = Image.new("RGBA", (64, 64), (20, 20, 20, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=(38, 110, 255, 255))
            draw.rectangle((26, 18, 38, 42), fill=(255, 255, 255, 255))
            draw.ellipse((22, 12, 42, 28), fill=(255, 255, 255, 255))
            draw.rectangle((22, 44, 42, 48), fill=(255, 255, 255, 255))
            return image

        def open_log_dir(icon=None, item=None):
            path = self.config.default_log_dir()
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)

        def open_settings(icon=None, item=None):
            if self._settings_controller is not None:
                self._settings_controller.show(self.config)

        def stop_app(icon=None, item=None):
            loop.call_soon_threadsafe(self.stop)

        def restart_app_as_admin(icon=None, item=None):
            self._emit_threadsafe(loop, RestartAsAdminEvent())

        icon = pystray.Icon(
            "doubao-voice-agent",
            build_icon(),
            "Doubao Voice Input",
            menu=pystray.Menu(
                pystray.MenuItem(lambda item: f"状态: {self._status}", None, enabled=False),
                pystray.MenuItem(lambda item: f"模式: {self._mode_display_label()}", None, enabled=False),
                pystray.MenuItem(lambda item: f"热键: {self.config.effective_hotkey_display()}", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("以管理员重启", restart_app_as_admin, enabled=lambda item: not self._process_elevated),
                pystray.MenuItem("设置", open_settings),
                pystray.MenuItem("打开日志目录", open_log_dir),
                pystray.MenuItem("退出", stop_app),
            ),
        )
        self._tray_icon = icon
        self._tray_thread = threading.Thread(target=icon.run, name="doubao-tray", daemon=True)
        self._tray_thread.start()


# ===== CLI 入口（兼容原有接口） =====


def build_arg_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(description="Doubao 语音输入全局版")
    parser.add_argument(
        "--mode",
        choices=("recognize", "inject"),
        default=argparse.SUPPRESS,
        help="recognize 仅识别；inject 识别后尝试写入当前焦点输入框",
    )
    parser.add_argument("--hotkey", help="覆盖默认热键，例如 right_ctrl / f9 / space")
    parser.add_argument("--mic-device", help="覆盖麦克风设备名称或索引")
    parser.add_argument("--credential-path", help="覆盖凭据文件路径")
    parser.add_argument(
        "--injection-policy",
        choices=SUPPORTED_INJECTION_POLICIES,
        default=argparse.SUPPRESS,
        help="direct_only 仅直接输入；direct_then_clipboard 失败时允许剪贴板回退",
    )
    parser.add_argument(
        "--streaming-text-mode",
        choices=SUPPORTED_STREAMING_TEXT_MODES,
        default=argparse.SUPPRESS,
        help="safe_inline 安全编辑框实时上屏；overlay_only 仅显示浮层",
    )
    parser.add_argument(
        "--capture-output-policy",
        choices=SUPPORTED_CAPTURE_OUTPUT_POLICIES,
        default=argparse.SUPPRESS,
        help="off 保持现状；mute_system_output 在录音期间静音系统输出",
    )
    parser.add_argument(
        "--polish-mode",
        choices=SUPPORTED_POLISH_MODES,
        default=argparse.SUPPRESS,
        help="light 轻量整理（推荐）；off 关闭；ollama 使用本地 Ollama 模型润色最终结果（较慢）",
    )
    parser.add_argument("--ollama-base-url", help="本地 Ollama 服务地址，默认 http://localhost:11434")
    parser.add_argument("--ollama-model", help="本地 Ollama 模型名，为空时仅在唯一模型场景下自动探测")
    parser.add_argument("--polish-timeout-ms", type=int, help="最终结果润色超时毫秒数")
    parser.add_argument("--ollama-keep-alive", help="Ollama 模型保温时长，例如 15m")
    parser.add_argument("--disable-ollama-warmup", action="store_true", help="关闭程序启动后的 Ollama 模型预热")
    parser.add_argument("--render-debounce-ms", type=int, help="流式渲染防抖毫秒数")
    parser.add_argument("--console", action="store_true", help="显示控制台输出，便于调试")
    parser.add_argument("--no-tray", action="store_true", help="禁用系统托盘，仅作为前台常驻工具运行")
    return parser


def build_config_from_args(args: argparse.Namespace | None = None) -> AgentConfig:
    """从命令行参数构建配置。"""
    if args is None:
        parser = build_arg_parser()
        args = parser.parse_args()

    config = AgentConfig.load()
    if getattr(args, "mode", None):
        config.mode = args.mode
    if getattr(args, "hotkey", None):
        hotkey = str(args.hotkey)
        hotkey_vk = vk_from_hotkey(hotkey)
        config.hotkey = normalize_cli_hotkey(hotkey_vk)
        config.hotkey_vk = hotkey_vk
        config.hotkey_display = vk_to_display(hotkey_vk)
    if getattr(args, "mic_device", None):
        config.microphone_device = (
            int(args.mic_device)
            if str(args.mic_device).isdigit()
            else args.mic_device
        )
    if getattr(args, "credential_path", None):
        config.credential_path = args.credential_path
    if getattr(args, "injection_policy", None):
        config.injection_policy = args.injection_policy
    if getattr(args, "streaming_text_mode", None):
        config.streaming_text_mode = args.streaming_text_mode
    if getattr(args, "capture_output_policy", None):
        config.capture_output_policy = args.capture_output_policy
    if getattr(args, "polish_mode", None):
        config.polish_mode = args.polish_mode
    if getattr(args, "ollama_base_url", None):
        config.ollama_base_url = str(args.ollama_base_url).strip().rstrip("/") or config.ollama_base_url
    if getattr(args, "ollama_model", None) is not None:
        config.ollama_model = str(args.ollama_model).strip()
    if getattr(args, "polish_timeout_ms", None) is not None:
        config.polish_timeout_ms = args.polish_timeout_ms
    if getattr(args, "ollama_keep_alive", None):
        config.ollama_keep_alive = args.ollama_keep_alive
    if getattr(args, "disable_ollama_warmup", False):
        config.ollama_warmup_enabled = False
    if getattr(args, "render_debounce_ms", None) is not None:
        config.render_debounce_ms = args.render_debounce_ms
    return config


def normalize_cli_hotkey(hotkey_vk: int) -> str:
    """规范化 CLI 热键。"""
    return vk_to_hotkey(hotkey_vk) or normalize_hotkey(vk_to_display(hotkey_vk))


__all__ = [
    "VoiceInputCoordinator",
    "build_arg_parser",
    "build_config_from_args",
    "normalize_cli_hotkey",
]
