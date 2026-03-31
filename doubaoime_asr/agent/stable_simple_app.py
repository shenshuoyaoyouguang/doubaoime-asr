"""
StableVoiceInputApp - 向后兼容入口。

委托给 VoiceInputCoordinator，保持原有公开接口不变。
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
from pathlib import Path
import sys
import threading
from typing import Literal, Any

from .config import (
    AgentConfig,
    SUPPORTED_CAPTURE_OUTPUT_POLICIES,
    SUPPORTED_INJECTION_POLICIES,
    SUPPORTED_POLISH_MODES,
    SUPPORTED_STREAMING_TEXT_MODES,
    POLISH_MODE_OLLAMA,
    STREAMING_TEXT_MODE_SAFE_INLINE,
)
from .coordinator import (
    VoiceInputCoordinator,
    build_arg_parser,
    build_config_from_args,
    normalize_cli_hotkey,
)
from .input_injector import FocusTarget, FocusChangedError

# 重新导出测试所需的模块（向后兼容）
from .runtime_logging import setup_named_logger
from .injection_manager import TextInjectionManager
from .overlay_preview import OverlayPreview
from .overlay_scheduler import OverlayRenderScheduler
from .text_polisher import TextPolisher
from .win_audio_output import SystemOutputMuteGuard
from .win_privileges import is_current_process_elevated, restart_as_admin
from .composition import CompositionSession  # [P1-Fix4] real re-export, not None


# 类型别名（向后兼容）
Mode = Literal["recognize", "inject"]

# 哨兵对象：标识 _session 从未被外部显式设置过
_UNSET: object = object()

# 重新导出 FocusTarget（向后兼容）


class StableVoiceInputApp:
    """向后兼容的入口类，委托给 Coordinator。

    保持原有公开接口不变，内部使用 VoiceInputCoordinator 实现。
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
        """初始化应用，创建 Coordinator。

        [P1-Fix1] 在构造 Coordinator 之后，用当前模块命名空间中的工厂替换其
        内部服务实例。这样调用者（或测试）monkeypatch stable_simple_app.TextInjectionManager
        等属性之后构造的 App 就会使用被替换后的 double，而不是真实的 Windows 依赖。
        """
        self._coordinator = VoiceInputCoordinator(
            config,
            mode=mode,
            enable_tray=enable_tray,
            console=console,
            launch_args=launch_args,
        )

        # [P1-Fix1] 用模块级工厂（已被 monkeypatch 替换）重建各 service 实例。
        # 直接引用 stable_simple_app 模块命名空间中的名字——这正是 monkeypatch
        # 所替换的那些名字。
        import doubaoime_asr.agent.stable_simple_app as _self_module  # noqa: PLC0415
        logger = self._coordinator.logger

        # ---- TextInjectionManager ----
        # 重建 injection_service 的底层 _manager
        _InjMgr = getattr(_self_module, "TextInjectionManager", TextInjectionManager)
        self._coordinator.injection_service._manager = _InjMgr(
            logger, policy=config.injection_policy
        )

        # ---- OverlayPreview / OverlayRenderScheduler ----
        # 策略：用模块级工厂创建 preview 和 raw scheduler。然后把
        # _SchedulerCompat（它包装 raw scheduler）注入为 overlay_service._scheduler。
        # 这样 coordinator 内部对 overlay_service 的所有调用都会流经
        # _SchedulerCompat，从而 _SchedulerCompat.calls 能捕获全部操作。
        _OvPreview = getattr(_self_module, "OverlayPreview", OverlayPreview)
        _OvSched = getattr(_self_module, "OverlayRenderScheduler", OverlayRenderScheduler)
        overlay_svc = self._coordinator.overlay_service
        _preview_instance = _OvPreview(logger=logger, config=config)
        _raw_scheduler = _OvSched(
            _preview_instance,
            logger=logger,
            fps=config.overlay_render_fps,
        )
        # 创建 _SchedulerCompat 单例，让它包装 raw scheduler（不是 overlay_service）
        _sched_compat = _SchedulerCompat(_raw_scheduler, raw_scheduler=True)
        # 注入为 overlay_service 的内部 scheduler，并标记服务已启动
        overlay_svc._preview = _preview_instance
        overlay_svc._scheduler = _sched_compat
        overlay_svc._running = True

        # ---- TextPolisher ----
        _Polisher = getattr(_self_module, "TextPolisher", TextPolisher)
        self._coordinator.text_polisher = _Polisher(logger, config)

        # ---- SystemOutputMuteGuard ----
        _MuteGuard = getattr(_self_module, "SystemOutputMuteGuard", SystemOutputMuteGuard)
        self._coordinator.capture_output_guard = _MuteGuard(
            logger, policy=config.capture_output_policy
        )

        # 测试兼容：存储外部设置的 session（_UNSET = 未被外部设置过）
        self.__test_session: Any = _UNSET

        # 保存运行态原始实现；compat 包装器只在显式测试会话覆盖时接管。
        self._runtime_inject_final_impl = self._coordinator._inject_final
        self._runtime_apply_inline_interim_impl = (
            self._coordinator.injection_service.apply_inline_interim
        )
        self._runtime_prepare_final_text_impl = (
            self._coordinator.injection_service.prepare_final_text
        )
        self._runtime_clear_active_session_impl = (
            self._coordinator._clear_active_session
        )
        self._runtime_send_stop_if_needed_impl = (
            self._coordinator._send_stop_if_needed
        )

        # [P1-Fix2] 将 coordinator 的关键钩子方法重定向到 self 的对应方法，
        # 使测试 monkeypatch app.xxx 能拦截 coordinator 内部调用。
        # 用 lambda 关闭 _app_ref 引用——monkeypatch 替换 app.xxx 后仍能生效。
        _app_ref = self

        async def _coord_inject_final(text: str) -> None:
            await _app_ref._inject_final(text)

        async def _coord_apply_inline_interim(text: str) -> None:
            await _app_ref._apply_inline_interim(text)

        async def _coord_prepare_final_text(text: str) -> None:
            await _app_ref._prepare_final_text_compat(text)

        async def _coord_clear_active_session() -> None:
            await _app_ref._clear_active_session()

        async def _coord_send_stop_if_needed() -> None:
            await _app_ref._send_stop_if_needed()

        async def _coord_handle_restart_as_admin() -> None:
            await _app_ref._handle_restart_as_admin()

        self._coordinator._inject_final = _coord_inject_final
        self._coordinator.injection_service.apply_inline_interim = _coord_apply_inline_interim
        self._coordinator.injection_service.prepare_final_text = _coord_prepare_final_text
        self._coordinator._clear_active_session = _coord_clear_active_session
        self._coordinator._send_stop_if_needed = _coord_send_stop_if_needed
        self._coordinator._handle_restart_as_admin = _coord_handle_restart_as_admin

        # [compat-singleton] 缓存 compat 包装器，保证多次访问时状态持续累积。
        # _overlay_scheduler_compat 直接使用上面创建的单例（已注入为内部 scheduler）。
        self._injection_manager_compat: _InjectionManagerCompat | None = None
        self._preview_compat: _PreviewCompat | None = None
        self._overlay_scheduler_compat: _SchedulerCompat = _sched_compat

        self._bind_session_manager_event_bridge()
        self._sync_process_elevation_from_wrapper()

    def _has_test_session_override(self) -> bool:
        """是否存在显式 compat 会话覆盖。"""
        return self.__test_session is not _UNSET

    def _uses_runtime_session_flow(self, session: Any) -> bool:
        """当前会话是否应走运行态 SessionManager/InjectionService 流程。"""
        return (
            not self._has_test_session_override()
            and isinstance(session, _SessionCompatWrapper)
        )

    def _bind_session_manager_event_bridge(self) -> None:
        """把 SessionManager 事件转发回 coordinator 事件队列。"""
        self._coordinator.session_manager._on_event = self._forward_session_manager_event

    def _forward_session_manager_event(self, event: object) -> None:
        """桥接 SessionManager 事件到 coordinator。"""
        loop = self._coordinator._loop
        if loop is not None:
            self._coordinator._emit_threadsafe(loop, event)
            return
        self._coordinator._emit(event)

    def _sync_process_elevation_from_wrapper(self) -> None:
        """用 stable_simple_app 模块级钩子同步提升状态。"""
        import doubaoime_asr.agent.stable_simple_app as _self_module  # noqa: PLC0415

        _is_process_elevated = getattr(
            _self_module,
            "is_current_process_elevated",
            is_current_process_elevated,
        )
        elevated = _is_process_elevated() is True
        self._coordinator._process_elevated = elevated
        self._coordinator.injection_service.set_process_elevated(elevated)

    # ===== 向后兼容属性 =====

    @property
    def config(self) -> AgentConfig:
        """获取配置。"""
        return self._coordinator.config

    @config.setter
    def config(self, value: AgentConfig) -> None:
        """设置配置。"""
        self._coordinator.config = value

    @property
    def mode(self) -> Mode:
        """获取模式。"""
        return self._coordinator.mode

    @mode.setter
    def mode(self, value: Mode) -> None:
        """设置模式。"""
        self._coordinator.mode = value

    @property
    def logger(self) -> Any:
        """获取日志器。"""
        return self._coordinator.logger

    @property
    def injection_manager(self) -> Any:
        """获取注入管理器（向后兼容）。

        [compat-singleton] 返回固定的包装器实例，以便 injector.calls 可以跨多次
        属性访问持续累积。
        """
        if self._injection_manager_compat is None:
            self._injection_manager_compat = _InjectionManagerCompat(
                self._coordinator.injection_service
            )
        return self._injection_manager_compat

    @property
    def preview(self) -> Any:
        """获取预览（向后兼容）。"""
        if self._preview_compat is None:
            self._preview_compat = _PreviewCompat(self._coordinator.overlay_service)
        return self._preview_compat

    @property
    def overlay_scheduler(self) -> Any:
        """获取调度器（向后兼容）。

        [compat-singleton] 返回固定的包装器实例（已在 __init__ 中创建并注入为
        overlay_service._scheduler），以便 calls 可以捕获所有调用（包括
        coordinator 内部直接对 overlay_service 的调用）。
        """
        return self._overlay_scheduler_compat

    @property
    def text_polisher(self) -> Any:
        """获取润色器。"""
        return self._coordinator.text_polisher

    @property
    def capture_output_guard(self) -> Any:
        """获取静音守护。"""
        return self._coordinator.capture_output_guard

    # ===== 状态属性 =====

    @property
    def _status(self) -> str:
        """获取状态。"""
        return self._coordinator.get_status()

    @_status.setter
    def _status(self, value: str) -> None:
        """设置状态。"""
        self._coordinator.set_status(value)

    @property
    def _session(self) -> Any:
        """获取会话（向后兼容）。

        - 如果外部通过 _session = value 设置过（包括 None），返回该值。
        - 否则从 SessionManager 读取（若无 session 返回 None）。
        """
        # _UNSET sentinel 表示从未被外部设置过
        if self.__test_session is _UNSET:
            sm_session = self._coordinator.session_manager._session
            if sm_session is None:
                return None
            return _SessionCompat(self._coordinator.session_manager)
        return self.__test_session

    @_session.setter
    def _session(self, value: Any) -> None:
        """设置会话（向后兼容）。"""
        # 外部显式设置（包括 None），记录到 __test_session
        self.__test_session = value
        # 同步到 SessionManager
        if value is None:
            self._coordinator.session_manager._session = None
        elif isinstance(value, WorkerSession):
            self._coordinator.session_manager._session = value._real
        elif hasattr(value, "_session"):
            self._coordinator.session_manager._session = value._session

    @property
    def _stopping(self) -> bool:
        """获取停止状态。"""
        return self._coordinator._stopping

    @_stopping.setter
    def _stopping(self, value: bool) -> None:
        """设置停止状态。"""
        self._coordinator._stopping = value

    @property
    def launch_args(self) -> list[str]:
        """获取启动参数。"""
        return self._coordinator.launch_args

    @launch_args.setter
    def launch_args(self, value: list[str]) -> None:
        """设置启动参数。"""
        self._coordinator.launch_args = value

    @property
    def enable_tray(self) -> bool:
        """获取托盘启用状态。"""
        return self._coordinator.enable_tray

    @property
    def console(self) -> bool:
        """获取控制台输出状态。"""
        return self._coordinator.console

    @property
    def _process_elevated(self) -> bool:
        """获取进程是否提升权限。"""
        return self._coordinator._process_elevated

    @property
    def _pending_listener_rebind(self) -> bool:
        """获取待重绑状态。"""
        return self._coordinator._pending_listener_rebind

    @_pending_listener_rebind.setter
    def _pending_listener_rebind(self, value: bool) -> None:
        """设置待重绑状态。"""
        self._coordinator._pending_listener_rebind = value

    @property
    def _pending_worker_restart(self) -> bool:
        """获取待重启状态。"""
        return self._coordinator._pending_worker_restart

    @_pending_worker_restart.setter
    def _pending_worker_restart(self, value: bool) -> None:
        """设置待重启状态。"""
        self._coordinator._pending_worker_restart = value

    @property
    def _pending_polisher_warmup(self) -> bool:
        """获取待预热状态。"""
        return self._coordinator._pending_polisher_warmup

    @_pending_polisher_warmup.setter
    def _pending_polisher_warmup(self, value: bool) -> None:
        """设置待预热状态。"""
        self._coordinator._pending_polisher_warmup = value

    # ===== 公开方法委托 =====

    def set_status(self, value: str) -> None:
        """设置状态。"""
        self._coordinator.set_status(value)

    async def run(self) -> int:
        """运行应用。"""
        self._bind_session_manager_event_bridge()
        self._sync_process_elevation_from_wrapper()
        return await self._coordinator.run()

    def stop(self) -> None:
        """停止应用。"""
        self._coordinator.stop()

    # ===== 向后兼容方法 =====

    def _emit(self, kind: str, payload: object = None) -> None:
        """发射事件（向后兼容）。"""
        from .events import VoiceInputEvent
        event = _convert_legacy_event(kind, payload)
        if event is not None:
            self._coordinator._emit(event)

    def _emit_threadsafe(self, loop: asyncio.AbstractEventLoop, kind: str, payload: object = None) -> None:
        """线程安全发射事件（向后兼容）。"""
        from .events import VoiceInputEvent
        event = _convert_legacy_event(kind, payload)
        if event is not None:
            self._coordinator._emit_threadsafe(loop, event)

    # ===== [P1-Fix2] 兼容入口：通过 self 的钩子方法路由，支持 monkeypatch =====

    async def _handle_press(self) -> None:
        """处理按下（向后兼容）。

        [P1-Fix2] 此方法直接实现逻辑并经由 self._ensure_worker、
        self._send_worker_command 等钩子，从而使测试 monkeypatch 正常生效。
        """
        self.logger.info("hotkey_down")
        session = await self._ensure_worker()
        use_runtime_session_flow = self._uses_runtime_session_flow(session)
        # 检查是否已在流式中
        from .session_manager import WorkerSessionState
        real_state = getattr(getattr(session, "_real", session), "state", None)
        if real_state == WorkerSessionState.STREAMING:
            return

        # 目标捕获
        target: FocusTarget | None = None
        inline_streaming_enabled = False
        composition = None
        if self.mode == "inject":
            target = self.injection_manager.capture_target()
            if target is None:
                self.set_status("未检测到可写入焦点")
                return
            if self._target_requires_admin(target):
                self._record_elevation_warning(target, log_tag="press_blocked_elevated_target")
                return
            self._clear_elevation_warning()
            self.logger.info(
                "captured_target hwnd=%s focus_hwnd=%s process=%s terminal=%s elevated=%s",
                target.hwnd,
                getattr(target, "focus_hwnd", None),
                target.process_name,
                getattr(target, "terminal_kind", None),
                target.is_elevated,
            )
            if self._should_enable_inline_streaming(target):
                inline_streaming_enabled = True
                if not use_runtime_session_flow:
                    composition = CompositionSession(
                        self.injection_manager.injector, target
                    )
        else:
            self.logger.info("inject_skipped reason=recognize_mode phase=session_start")

        # 开始会话
        if not use_runtime_session_flow:
            if hasattr(session, "begin"):
                session.begin(
                    target,
                    self.mode,
                    composition=composition,
                    inline_streaming_enabled=inline_streaming_enabled,
                )
        else:
            self._coordinator.session_manager.begin_session(target, self.mode)
            self._coordinator.injection_service.begin_session(
                target,
                self.mode,
                inline_streaming_enabled=inline_streaming_enabled,
            )

        # 激活静音
        capture_output_warning = self._activate_capture_output()

        # 发送 START 命令
        try:
            await self._send_worker_command("START")
        except Exception:
            # 清除会话状态
            if hasattr(session, "clear_active"):
                session.clear_active()
            self._clear_session_state()
            restore_warning = self._release_capture_output()
            self.logger.exception("worker_start_command_failed")
            self.set_status(restore_warning or "启动识别失败，请查看 controller.log")
            await self._restart_worker()
            return

        await self.overlay_scheduler.show_microphone("正在聆听…")
        self.set_status(self._session_start_status(capture_output_warning))

        # 清除分段文本状态
        self._coordinator._segment_texts.clear()
        self._coordinator._finalized_segment_indexes.clear()
        self._coordinator._active_segment_index = None
        self._coordinator._last_displayed_raw_final_text = ""

    def _clear_session_state(self) -> None:
        """内部：清除会话关联状态（不含 capture_output）。"""
        self._coordinator.session_manager.clear_session()
        self._coordinator.injection_service.end_session()

    async def _handle_release(self) -> None:
        """处理释放（向后兼容）。"""
        await self._coordinator._handle_release()

    async def _send_stop(self, log_tag: str, status: str) -> None:
        """发送停止（向后兼容）。

        [P1-Fix2] 经由 self._send_worker_command 发送，支持 monkeypatch。
        """
        session = self._session
        if session is None:
            return
        # 标记 stop 已发送
        await self._send_worker_command("STOP")
        # 更新 session 状态
        if hasattr(session, "stop_sent"):
            session.stop_sent = True
        if hasattr(session, "pending_stop"):
            session.pending_stop = False
        # 停止麦克风 HUD
        await self.overlay_scheduler.stop_microphone()
        self.logger.info(log_tag)
        self.set_status(status)

    async def _inject_final(self, text: str) -> None:
        """注入最终文本（向后兼容）。

        [P1-Fix2/Fix3] 从 self._session 读取 composition/inline_streaming_enabled/
        final_injection_blocked，这些字段存储在 compat WorkerSession 上。
        """
        if not self._has_test_session_override():
            await self._runtime_inject_final_impl(text)
            return
        if not text:
            return
        session = self._session
        if session is None:
            return
        if getattr(session, "mode", "inject") != "inject":
            self.logger.info("inject_skipped reason=recognize_mode text_length=%s", len(text))
            return
        if getattr(session, "final_injection_blocked", False):
            return

        target = getattr(session, "target", None)
        composition = getattr(session, "composition", None)
        inline_streaming_enabled = getattr(session, "inline_streaming_enabled", False)

        if composition is not None and inline_streaming_enabled:
            # 流式上屏最终注入
            try:
                if getattr(composition, "rendered_text", None) != text or getattr(composition, "final_text", None) != text:
                    composition.finalize(text)
                self.logger.info("inject_success method=inline_composition")
            except FocusChangedError:
                self._handle_inline_focus_changed("inject_final")
                self.logger.warning("inject_focus_changed")
            except Exception:
                self._handle_inline_failure(composition, log_tag="inject_inline_final_failed")
            return

        if target is None:
            return
        if self._target_requires_admin(target):
            return
        try:
            result = await self.injection_manager.inject_text(target, text)
            if result:
                self.logger.info("inject_success method=%s", getattr(result, "method", "unknown"))
        except FocusChangedError:
            self._handle_inline_focus_changed("inject_final_focus_changed")
            self.logger.warning("inject_focus_changed")
            self.set_status("焦点已变化，仅保留识别")
        except Exception:
            self.logger.exception("inject_final_failed")
            self.set_status("注入失败，仅保留识别")

    async def _clear_active_session(self) -> None:
        """清除活跃会话（向后兼容）。

        [P1-Fix2] 经由 self._session 修改状态，支持 monkeypatch。
        """
        if not self._has_test_session_override():
            await self._runtime_clear_active_session_impl()
            return
        session = self._session
        if session is not None and hasattr(session, "clear_active"):
            session.clear_active()
        self._coordinator.injection_service.end_session()
        restore_warning = self._release_capture_output()
        if restore_warning is not None:
            self.set_status(restore_warning)
        self._apply_pending_listener_rebind("listener_rebind_failed_after_session")
        if self._pending_worker_restart:
            self._pending_worker_restart = False
            await self._restart_worker()
        if self._pending_polisher_warmup:
            self._pending_polisher_warmup = False
            self._schedule_polisher_warmup("after_session")

    async def _handle_worker_exit(self, session_id: int, code: int) -> None:
        """处理 Worker 退出（向后兼容）。

        [P1-Fix2] 经由 self._rebind_listener 钩子，支持 monkeypatch。
        """
        session = self._session
        if session is None or getattr(session, "session_id", None) != session_id:
            self.logger.info(
                "worker_exit_ignored session_id=%s current_session_id=%s code=%s",
                session_id,
                getattr(session, "session_id", None) if session else None,
                code,
            )
            return

        self.logger.info("worker_exit code=%s", code)
        if not self._stopping and code != 0 and not self._status.startswith("识别失败"):
            self.set_status(f"识别进程异常退出: {code}")

        restore_warning = self._release_capture_output()
        if restore_warning is not None:
            self.set_status(restore_warning)

        # Worker 已经退出，只需清理资源（不调用 terminate_worker 避免重复 wait）
        await self._dispose_worker()
        self.__test_session = _UNSET  # 清除 compat session 引用

        if not self._stopping:
            self._apply_pending_listener_rebind("listener_rebind_failed_after_worker_exit")

        if not self._stopping and self._pending_worker_restart:
            self._pending_worker_restart = False
            with contextlib.suppress(Exception):
                await self._coordinator.session_manager.ensure_worker()

    async def _handle_worker_event(self, session_id: int, event: object) -> None:
        """处理 Worker 事件（向后兼容）。"""
        session = self._session
        current_session_id = getattr(session, "session_id", None) if session is not None else None
        if session is None or current_session_id != session_id:
            event_type = event.get("type") if isinstance(event, dict) else type(event).__name__
            self.logger.info(
                "worker_event_ignored session_id=%s current_session_id=%s type=%s",
                session_id,
                current_session_id,
                event_type,
            )
            return
        if isinstance(event, dict):
            from .events import parse_worker_event
            parsed = parse_worker_event(event)
            await self._coordinator._handle_worker_event(parsed)

    async def _apply_config(self, new_config: AgentConfig) -> None:
        """应用配置（向后兼容）。

        [P1-Fix2] 经由 self._rebind_listener / self._restart_worker 钩子，
        使测试 monkeypatch 正常生效。
        """
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
        session_active = self._coordinator.session_manager.is_streaming()
        listener_rebound = False
        worker_restarted = False

        def _configure_services(cfg: AgentConfig) -> None:
            """更新全部 service，并通过 compat 包装器记录 configure 调用。

            self.preview.configure(cfg) 会：
              1. 在 _PreviewCompat.configured 中追加记录
              2. 调用 overlay_service.configure(cfg)，后者再调用
                 _sched_compat.configure(cfg)，从而在 overlay_scheduler.configured 中追加记录
            无需再单独调用 self.overlay_scheduler.configure(cfg)，否则会重复追加。
            """
            self._coordinator.session_manager.config = cfg
            # preview.configure → overlay_service.configure → _sched_compat.configure（双覆盖）
            self.preview.configure(cfg)
            self._coordinator.injection_service.configure(cfg)
            self._coordinator.text_polisher.configure(cfg)
            self._coordinator.capture_output_guard.configure(cfg.capture_output_policy)

        try:
            self.config = new_config
            self.mode = new_config.mode

            _configure_services(new_config)

            if hotkey_changed:
                if session_active:
                    self._pending_listener_rebind = True
                else:
                    self._rebind_listener(new_config.effective_hotkey_vk())
                    listener_rebound = True

            if worker_changed:
                if session_active:
                    self._pending_worker_restart = True
                else:
                    await self._restart_worker()
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
            _configure_services(old_config)
            if listener_rebound:
                self._rebind_listener(old_config.effective_hotkey_vk())
            if worker_restarted:
                await self._restart_worker()
            with contextlib.suppress(Exception):
                self.config.save()
            self.set_status("设置保存失败，已恢复旧配置")
            return

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

    async def _restart_worker(self) -> None:
        """重启 Worker（向后兼容）。"""
        await self._coordinator.session_manager.restart_worker()

    async def _terminate_worker(self) -> None:
        """终止 Worker（向后兼容）。

        [P1-Fix2] 经由 self._send_worker_command 发送 EXIT，支持 monkeypatch。
        实现与 session_manager._terminate_session_process 等效的逻辑。
        """
        session = self._session
        if session is None:
            return

        process = getattr(session, "process", None)
        if process is None:
            return

        # 尝试优雅退出
        if getattr(process, "stdin", None) is not None and getattr(process, "returncode", 1) is None:
            try:
                await self._send_worker_command("EXIT")
            except Exception:
                pass

        # 等待进程退出，超时后强制 kill
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass

        # 清除会话
        self._coordinator.session_manager._session = None
        self.__test_session = _UNSET

    async def _ensure_worker(self) -> Any:
        """确保 Worker（向后兼容）。

        [P1-Fix2] 通过 self._spawn_worker、self._terminate_session_process 等
        钩子实现，支持测试 monkeypatch 这些方法。
        如果外部显式设置了 session（测试通过 app._session = ...），直接返回。
        """
        # 检查是否有测试显式设置的外部 session
        if self.__test_session is not _UNSET:
            return self.__test_session

        sm = self._coordinator.session_manager

        # 若已有可用 session，直接返回
        if sm._session is not None and sm._session.process.returncode is None:
            if sm._session.process_ready:
                return _SessionCompat.wrap(sm._session)

        # 若旧 session 已退出，先清理
        if sm._session is not None and sm._session.process.returncode is not None:
            await self._dispose_worker()

        # 生成新 worker
        process = await self._spawn_worker()
        sm._next_session_id += 1
        from .session_manager import WorkerSession as _RealSession, WorkerSessionState
        session = _RealSession(
            session_id=sm._next_session_id,
            process=process,
            state=WorkerSessionState.STARTING,
        )
        session.stdout_task = asyncio.create_task(
            self._read_worker_stdout(process.stdout, _SessionCompat.wrap(session))
        )
        session.stderr_task = asyncio.create_task(
            self._read_worker_stderr(process.stderr)
        )
        session.wait_task = asyncio.create_task(
            self._wait_worker(process, session.session_id)
        )
        sm._session = session

        # 等待进程就绪（最多 2.5 秒）
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2.5
        while loop.time() < deadline:
            if session.process_ready:
                session.transition_to(WorkerSessionState.READY)
                return _SessionCompat.wrap(session)
            if session.process.returncode is not None:
                break
            await asyncio.sleep(0.02)

        await self._terminate_session_process(_SessionCompat.wrap(session))
        await self._dispose_worker()
        raise RuntimeError("worker process did not become ready")

    def _rebind_listener(self, hotkey_vk: int) -> None:
        """重绑定监听器（向后兼容）。"""
        self._coordinator.hotkey_service.update_hotkey(hotkey_vk)

    def _build_listener(self, loop: asyncio.AbstractEventLoop, hotkey_vk: int) -> Any:
        """构建监听器（向后兼容）。"""
        return _ListenerCompat(self._coordinator.hotkey_service, hotkey_vk)

    def _should_enable_inline_streaming(self, target: FocusTarget) -> bool:
        """判断是否启用流式上屏（向后兼容）。"""
        return self._coordinator.injection_service.should_enable_inline_streaming(target)

    def _target_requires_admin(self, target: FocusTarget | None) -> bool:
        """判断目标是否需要管理员（向后兼容）。"""
        return self._coordinator.injection_service.target_requires_admin(target)

    def _check_foreground_elevation(self) -> None:
        """检查前景权限（向后兼容）。"""
        self._coordinator._check_foreground_elevation()

    def _record_elevation_warning(self, target: FocusTarget, *, log_tag: str) -> None:
        """记录权限警告（向后兼容）。"""
        self._coordinator._record_elevation_warning(target, log_tag=log_tag)

    def _clear_elevation_warning(self) -> None:
        """清除权限警告（向后兼容）。"""
        self._coordinator._clear_elevation_warning()

    async def _handle_restart_as_admin(self) -> None:
        """处理管理员重启（向后兼容）。

        [P1-Fix2] 调用 stable_simple_app.restart_as_admin（模块级名字），
        从而使测试 monkeypatch 正常生效。
        """
        self._sync_process_elevation_from_wrapper()
        import doubaoime_asr.agent.stable_simple_app as _self_module  # noqa: PLC0415
        _restart_as_admin = getattr(_self_module, "restart_as_admin", restart_as_admin)

        if self._process_elevated:
            self.set_status("代理已在管理员模式运行")
            return
        try:
            restarted = _restart_as_admin(
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
        self._stopping = True

    async def _send_stop_if_needed(self) -> None:
        """发送停止如果需要（向后兼容）。"""
        if not self._has_test_session_override():
            await self._runtime_send_stop_if_needed_impl()
            return
        session = self._session
        if session is None or getattr(session, "stop_sent", False) or not getattr(session, "pending_stop", False):
            return
        await self._send_stop("worker_stop_sent_after_ready", "等待最终结果…")

    async def _apply_inline_interim(self, text: str) -> None:
        """应用流式中间结果（向后兼容）。

        [P1-Fix3] 从 self._session（compat WorkerSession）读写 composition、
        inline_streaming_enabled、final_injection_blocked，而不是委托给
        injection_service（后者有自己独立的 InjectionSessionState）。
        """
        if not self._has_test_session_override():
            await self._runtime_apply_inline_interim_impl(text)
            return
        session = self._session
        if session is None:
            return
        if not getattr(session, "inline_streaming_enabled", False):
            return
        composition = getattr(session, "composition", None)
        if composition is None:
            return
        if getattr(composition, "rendered_text", None) == text:
            return
        try:
            composition.render_interim(text)
        except FocusChangedError:
            self._handle_inline_focus_changed("apply_inline_interim")
            self.logger.warning("inline_streaming_focus_changed")
        except Exception:
            self._handle_inline_failure(composition, log_tag="inline_streaming_failed")

    async def _prepare_final_text(self, text: str) -> None:
        """准备流式最终文本（向后兼容）。"""
        await self._prepare_final_text_compat(text)

    async def _prepare_final_text_compat(self, text: str) -> None:
        """准备流式最终文本（从 compat WorkerSession 读取 composition）。

        [P1-Fix3] 从 self._session（compat WorkerSession）读写 composition，
        不依赖 injection_service._session。
        """
        if not self._has_test_session_override():
            await self._runtime_prepare_final_text_impl(text)
            return
        session = self._session
        if session is None:
            return
        if not getattr(session, "inline_streaming_enabled", False):
            return
        composition = getattr(session, "composition", None)
        if composition is None:
            return
        if (getattr(composition, "rendered_text", None) == text
                and getattr(composition, "final_text", None) == text):
            return
        try:
            composition.finalize(text)
        except FocusChangedError:
            self._handle_inline_focus_changed("prepare_final_text")
            self.logger.warning("inline_final_focus_changed")
        except Exception:
            self._handle_inline_failure(composition, log_tag="inline_final_prepare_failed")

    def _activate_capture_output(self) -> str | None:
        """激活静音（向后兼容）。"""
        return self._coordinator._activate_capture_output()

    def _release_capture_output(self) -> str | None:
        """释放静音（向后兼容）。"""
        return self._coordinator._release_capture_output()

    def _mode_display_label(self, mode: Mode | None = None) -> str:
        """模式显示标签（向后兼容）。"""
        return self._coordinator._mode_display_label(mode)

    def _session_start_status(self, capture_output_warning: str | None) -> str:
        """会话启动状态（向后兼容）。"""
        return self._coordinator._session_start_status(capture_output_warning)

    def _status_for_final_result(self, result: Any, raw_text: str) -> str:
        """最终结果状态（向后兼容）。"""
        return self._coordinator._status_for_final_result(result, raw_text)

    async def _resolve_final_text(self, raw_text: str) -> Any:
        """解析最终文本（向后兼容）。"""
        return await self._coordinator._resolve_final_text(raw_text)

    async def _send_worker_command(self, command: str) -> None:
        """发送 Worker 命令（向后兼容）。"""
        await self._coordinator.session_manager.send_command(command)

    def _build_worker_command(self) -> list[str]:
        """构建 Worker 命令（向后兼容）。"""
        return self._coordinator.session_manager._build_worker_command()

    async def _spawn_worker(self) -> Any:
        """生成 Worker（向后兼容）。"""
        return await self._coordinator.session_manager._spawn_worker()

    async def _read_worker_stdout(self, stream: Any, session: Any) -> None:
        """读取 Worker stdout（向后兼容）。"""
        # 解包为真实 session 对象
        real = getattr(session, "_real", None) or getattr(session, "_session", None) or session
        await self._coordinator.session_manager._read_worker_stdout(stream, real)

    async def _read_worker_stderr(self, stream: Any) -> None:
        """读取 Worker stderr（向后兼容）。"""
        await self._coordinator.session_manager._read_worker_stderr(stream)

    async def _wait_worker(self, process: Any, session_id: int) -> None:
        """等待 Worker（向后兼容）。"""
        await self._coordinator.session_manager._wait_worker(process, session_id)

    async def _terminate_session_process(self, session: Any) -> None:
        """终止会话进程（向后兼容）。"""
        real_session = session._real if hasattr(session, "_real") else session
        await self._coordinator.session_manager._terminate_session_process(real_session)

    async def _dispose_worker(self) -> None:
        """清理 Worker（向后兼容）。"""
        await self._coordinator.session_manager._dispose_worker()

    def _handle_inline_focus_changed(self, log_tag: str) -> None:
        """处理流式焦点变化（向后兼容）。

        [P1-Fix3] 直接修改 self._session 上的字段。
        """
        session = self._session
        if session is None:
            return
        if hasattr(session, "inline_streaming_enabled"):
            session.inline_streaming_enabled = False
        if hasattr(session, "final_injection_blocked"):
            session.final_injection_blocked = True
        if hasattr(session, "target"):
            session.target = None

    def _handle_inline_failure(self, composition: Any, *, log_tag: str, fallback_status: str | None = None, blocked_status: str = "实时上屏失败，仅保留识别") -> bool:
        """处理流式失败（向后兼容）。

        [P1-Fix3] 直接修改 self._session 上的字段。
        """
        composed_text_exists = bool(
            composition is not None
            and (getattr(composition, "rendered_text", "") or getattr(composition, "final_text", ""))
        )
        if composed_text_exists:
            self._handle_inline_focus_changed(log_tag)
            self.set_status(blocked_status)
        elif fallback_status:
            self.set_status(fallback_status)
        self.logger.exception(log_tag)
        return composed_text_exists

    def _elevation_status_message(self, target: FocusTarget) -> str:
        """权限状态消息（向后兼容）。"""
        return self._coordinator._elevation_status_message(target)

    def _polisher_config_changed(self, old_config: AgentConfig, new_config: AgentConfig) -> bool:
        """润色配置变化（向后兼容）。"""
        return self._coordinator._polisher_config_changed(old_config, new_config)

    def _apply_pending_listener_rebind(self, log_tag: str) -> None:
        """应用待重绑（向后兼容）。

        [P1-Fix2] 经由 self._rebind_listener 钩子，支持 monkeypatch。
        """
        if not self._pending_listener_rebind:
            return
        self._pending_listener_rebind = False
        try:
            self._rebind_listener(self.config.effective_hotkey_vk())
        except Exception:
            self.logger.exception(log_tag)

    def _schedule_polisher_warmup(self, reason: str) -> None:
        """调度润色预热（向后兼容）。"""
        self._coordinator._schedule_polisher_warmup(reason)


# ===== 向后兼容类 =====


class WorkerSession:
    """向后兼容的 WorkerSession（委托给 SessionManager.WorkerSession）。

    [P1-Fix3] composition、inline_streaming_enabled、final_injection_blocked
    作为实例变量存储在此包装器上，因为底层 session_manager.WorkerSession
    使用 slots=True 且不定义这些字段。
    """

    def __init__(
        self,
        session_id: int,
        process: Any = None,
        stdout_task: Any = None,
        stderr_task: Any = None,
        wait_task: Any = None,
    ) -> None:
        from .session_manager import WorkerSession as _RealSession
        self._real = _RealSession(session_id=session_id, process=process)
        self._real.stdout_task = stdout_task
        self._real.stderr_task = stderr_task
        self._real.wait_task = wait_task
        # [P1-Fix3] 本地存储的流式字段（底层 dataclass 无此 slot）
        self._composition: Any = None
        self._inline_streaming_enabled: bool = False
        self._final_injection_blocked: bool = False

    @property
    def session_id(self) -> int:
        return self._real.session_id

    @property
    def process(self) -> Any:
        return self._real.process

    @process.setter
    def process(self, value: Any) -> None:
        self._real.process = value

    @property
    def active(self) -> bool:
        from .session_manager import WorkerSessionState
        return self._real.state == WorkerSessionState.STREAMING

    @property
    def state(self) -> Any:
        return self._real.state

    @property
    def process_ready(self) -> bool:
        return self._real.process_ready

    @property
    def stop_sent(self) -> bool:
        return self._real.stop_sent

    @property
    def pending_stop(self) -> bool:
        return self._real.pending_stop

    @property
    def ready(self) -> bool:
        return self._real.ready

    @property
    def streaming_started(self) -> bool:
        return self._real.streaming_started

    @property
    def target(self) -> FocusTarget | None:
        return self._real.target

    @property
    def mode(self) -> Mode:
        return self._real.mode

    @property
    def composition(self) -> Any:
        """[P1-Fix3] 本地存储，不委托给 _real（_real 无此 slot）。"""
        return self._composition

    @property
    def inline_streaming_enabled(self) -> bool:
        """[P1-Fix3] 本地存储，不委托给 _real（_real 无此 slot）。"""
        return self._inline_streaming_enabled

    @property
    def final_injection_blocked(self) -> bool:
        """[P1-Fix3] 本地存储，不委托给 _real（_real 无此 slot）。"""
        return self._final_injection_blocked

    @property
    def segment_texts(self) -> dict[int, str]:
        return self._real.segment_texts

    @property
    def finalized_segment_indexes(self) -> set[int]:
        return self._real.finalized_segment_indexes

    @property
    def active_segment_index(self) -> int | None:
        return self._real.active_segment_index

    @property
    def last_displayed_raw_final_text(self) -> str:
        return getattr(self._real, "last_displayed_raw_final_text", "")

    @property
    def stdout_task(self) -> Any:
        return self._real.stdout_task

    @property
    def stderr_task(self) -> Any:
        return self._real.stderr_task

    @property
    def wait_task(self) -> Any:
        return self._real.wait_task

    # Setter properties
    @active.setter
    def active(self, value: bool) -> None:
        from .session_manager import WorkerSessionState
        if value:
            self._real.transition_to(WorkerSessionState.STREAMING)
        else:
            self._real.transition_to(WorkerSessionState.READY)

    @process_ready.setter
    def process_ready(self, value: bool) -> None:
        self._real.process_ready = value

    @stop_sent.setter
    def stop_sent(self, value: bool) -> None:
        self._real.stop_sent = value

    @pending_stop.setter
    def pending_stop(self, value: bool) -> None:
        self._real.pending_stop = value

    @ready.setter
    def ready(self, value: bool) -> None:
        self._real.ready = value

    @streaming_started.setter
    def streaming_started(self, value: bool) -> None:
        self._real.streaming_started = value

    @target.setter
    def target(self, value: FocusTarget | None) -> None:
        self._real.target = value

    @mode.setter
    def mode(self, value: Mode) -> None:
        self._real.mode = value

    @composition.setter
    def composition(self, value: Any) -> None:
        """[P1-Fix3] 本地存储，不委托给 _real。"""
        self._composition = value

    @inline_streaming_enabled.setter
    def inline_streaming_enabled(self, value: bool) -> None:
        """[P1-Fix3] 本地存储，不委托给 _real。"""
        self._inline_streaming_enabled = value

    @final_injection_blocked.setter
    def final_injection_blocked(self, value: bool) -> None:
        """[P1-Fix3] 本地存储，不委托给 _real。"""
        self._final_injection_blocked = value

    @active_segment_index.setter
    def active_segment_index(self, value: int | None) -> None:
        self._real.active_segment_index = value

    @last_displayed_raw_final_text.setter
    def last_displayed_raw_final_text(self, value: str) -> None:
        setattr(self._real, "last_displayed_raw_final_text", value)

    @stdout_task.setter
    def stdout_task(self, value: Any) -> None:
        self._real.stdout_task = value

    @stderr_task.setter
    def stderr_task(self, value: Any) -> None:
        self._real.stderr_task = value

    @wait_task.setter
    def wait_task(self, value: Any) -> None:
        self._real.wait_task = value

    def begin(self, target: FocusTarget | None, mode: Mode, *, composition: Any = None, inline_streaming_enabled: bool = False) -> None:
        """[P1-Fix3] begin 接受 composition/inline_streaming_enabled，本地存储。"""
        self._real.begin(target, mode)
        self._composition = composition
        self._inline_streaming_enabled = inline_streaming_enabled
        self._final_injection_blocked = False

    def clear_active(self) -> None:
        self._real.clear_active()
        self._composition = None
        self._inline_streaming_enabled = False
        self._final_injection_blocked = False


# [P1-Fix4] CompositionSession 是真实的类，不能是 None。
# 已在文件顶部通过 `from .composition import CompositionSession` 导入。
# 不再有 `CompositionSession = None` 的覆盖赋值。


class _InjectionManagerCompat:
    """注入管理器兼容包装。"""

    def __init__(self, injection_service: Any) -> None:
        self._service = injection_service

    @property
    def policy(self) -> str:
        """动态读取注入策略，保证 configure 后立即反映新值。"""
        return self._service.get_injection_policy()

    @policy.setter
    def policy(self, value: str) -> None:
        """设置策略（通过 service 更新）。"""
        if hasattr(self._service, "_manager") and hasattr(self._service._manager, "policy"):
            self._service._manager.policy = value

    @property
    def injector(self) -> Any:
        """动态读取 injector，保证替换 _manager 后仍然正确。"""
        return self._service._manager.injector

    @property
    def captured_target(self) -> FocusTarget | None:
        """从底层 _manager 读取 captured_target（测试通过此属性注入 target）。"""
        return getattr(self._service._manager, "captured_target", None)

    @captured_target.setter
    def captured_target(self, value: FocusTarget | None) -> None:
        """同步设置到底层 _manager，使 injection_service.capture_target() 返回此值。"""
        if hasattr(self._service._manager, "captured_target"):
            self._service._manager.captured_target = value

    def set_policy(self, policy: str) -> None:
        self._service.configure(self._service._config)

    def capture_target(self) -> FocusTarget | None:
        target = self._service.capture_target()
        return target

    async def inject_text(self, target: FocusTarget, text: str) -> Any:
        from types import SimpleNamespace
        self._service.begin_session(target, "inject")
        result = await self._service.inject_final(text)
        if result is None:
            return SimpleNamespace(
                method="direct",
                target_profile="editor",
                clipboard_touched=False,
                restored_clipboard=False,
            )
        return result


class _PreviewCompat:
    """预览兼容包装。"""

    def __init__(self, overlay_service: Any) -> None:
        self._service = overlay_service
        self.configured: list[AgentConfig] = []

    def configure(self, config: AgentConfig) -> None:
        self.configured.append(config)
        self._service.configure(config)

    def start(self) -> None:
        self._service.start()

    def stop(self) -> None:
        self._service.stop()


class _SchedulerCompat:
    """调度器兼容包装。

    支持两种模式：
    - raw_scheduler=False（默认，遗留行为）：包装 overlay_service，通过它间接调度。
    - raw_scheduler=True：直接包装底层 scheduler 对象（如 _DummyScheduler），
      可被注入为 overlay_service._scheduler，从而捕获所有内部调用。
    """

    def __init__(self, service: Any, *, raw_scheduler: bool = False) -> None:
        self._service = service
        self._raw = raw_scheduler
        self.configured: list[AgentConfig] = []
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def configure(self, config: AgentConfig) -> None:
        self.configured.append(config)
        if hasattr(self._service, "configure"):
            self._service.configure(config)

    async def submit_interim(self, text: str) -> None:
        self.calls.append(("interim", (text,)))
        await self._service.submit_interim(text)

    async def submit_final(self, text: str, *, kind: str) -> None:
        self.calls.append(("final", (text, kind)))
        await self._service.submit_final(text, kind=kind)

    async def show_microphone(self, placeholder_text: str = "正在聆听…") -> None:
        self.calls.append(("microphone", (placeholder_text,)))
        await self._service.show_microphone(placeholder_text)

    async def update_microphone_level(self, level: float) -> None:
        self.calls.append(("audio_level", (level,)))
        await self._service.update_microphone_level(level)

    async def stop_microphone(self) -> None:
        self.calls.append(("stop_microphone", ()))
        await self._service.stop_microphone()

    async def hide(self, reason: str) -> None:
        self.calls.append(("hide", (reason,)))
        await self._service.hide(reason)


class _SessionCompat:
    """会话兼容包装。"""

    def __init__(self, session_manager: Any) -> None:
        self._manager = session_manager

    @property
    def session_id(self) -> int:
        session = self._manager.get_session()
        return session.session_id if session else 0

    @property
    def active(self) -> bool:
        return self._manager.is_streaming()

    @property
    def process(self) -> Any:
        session = self._manager.get_session()
        return session.process if session else None

    @property
    def process_ready(self) -> bool:
        session = self._manager.get_session()
        return session.process_ready if session else False

    @property
    def stop_sent(self) -> bool:
        session = self._manager.get_session()
        return session.stop_sent if session else False

    @property
    def pending_stop(self) -> bool:
        session = self._manager.get_session()
        return session.pending_stop if session else False

    @property
    def ready(self) -> bool:
        session = self._manager.get_session()
        return session.ready if session else False

    @property
    def streaming_started(self) -> bool:
        session = self._manager.get_session()
        return session.streaming_started if session else False

    @property
    def target(self) -> FocusTarget | None:
        session = self._manager.get_session()
        return session.target if session else None

    @property
    def mode(self) -> Mode:
        session = self._manager.get_session()
        return session.mode if session else "inject"

    @property
    def composition(self) -> Any:
        session = self._manager.get_session()
        return getattr(session, "composition", None) if session else None

    @property
    def inline_streaming_enabled(self) -> bool:
        session = self._manager.get_session()
        return bool(getattr(session, "inline_streaming_enabled", False)) if session else False

    @property
    def final_injection_blocked(self) -> bool:
        session = self._manager.get_session()
        return bool(getattr(session, "final_injection_blocked", False)) if session else False

    @staticmethod
    def wrap(session: Any) -> "_SessionCompatWrapper":
        return _SessionCompatWrapper(session)


class _SessionCompatWrapper(_SessionCompat):
    """会话兼容包装（用于 _ensure_worker 返回）。"""

    def __init__(self, session: Any) -> None:
        self._session = session
        self._composition = getattr(session, "composition", None)
        self._inline_streaming_enabled = bool(
            getattr(session, "inline_streaming_enabled", False)
        )
        self._final_injection_blocked = bool(
            getattr(session, "final_injection_blocked", False)
        )

    @property
    def session_id(self) -> int:
        return self._session.session_id

    @property
    def active(self) -> bool:
        from .session_manager import WorkerSessionState
        return self._session.state == WorkerSessionState.STREAMING

    @property
    def process(self) -> Any:
        return self._session.process

    @property
    def process_ready(self) -> bool:
        return self._session.process_ready

    @property
    def stop_sent(self) -> bool:
        return self._session.stop_sent

    @property
    def pending_stop(self) -> bool:
        return self._session.pending_stop

    @property
    def ready(self) -> bool:
        return self._session.ready

    @property
    def streaming_started(self) -> bool:
        return self._session.streaming_started

    @property
    def target(self) -> FocusTarget | None:
        return self._session.target

    @property
    def mode(self) -> Mode:
        return self._session.mode

    @property
    def composition(self) -> Any:
        return getattr(self._session, "composition", self._composition)

    @property
    def inline_streaming_enabled(self) -> bool:
        return bool(
            getattr(self._session, "inline_streaming_enabled", self._inline_streaming_enabled)
        )

    @property
    def final_injection_blocked(self) -> bool:
        return bool(
            getattr(
                self._session,
                "final_injection_blocked",
                self._final_injection_blocked,
            )
        )

    @property
    def stdout_task(self) -> Any:
        return self._session.stdout_task

    @property
    def stderr_task(self) -> Any:
        return self._session.stderr_task

    @property
    def wait_task(self) -> Any:
        return self._session.wait_task

    def begin(self, target: FocusTarget | None, mode: Mode, *, composition: Any = None, inline_streaming_enabled: bool = False) -> None:
        self._session.begin(target, mode)
        self._composition = composition
        self._inline_streaming_enabled = inline_streaming_enabled
        self._final_injection_blocked = False


class _ListenerCompat:
    """监听器兼容包装。"""

    def __init__(self, hotkey_service: Any, vk: int) -> None:
        self._service = hotkey_service
        self._vk = vk

    def start(self) -> None:
        self._service.start(self._vk)

    def stop(self) -> None:
        self._service.stop()


def _convert_legacy_event(kind: str, payload: object) -> Any:
    """转换旧式事件到类型化事件。"""
    from .events import (
        HotkeyPressEvent,
        HotkeyReleaseEvent,
        ConfigChangeEvent,
        RestartAsAdminEvent,
        StopEvent,
        WorkerExitEvent,
        VoiceInputEvent,
        parse_worker_event,
    )

    if kind == "press":
        return HotkeyPressEvent()
    if kind == "release":
        return HotkeyReleaseEvent()
    if kind == "apply_config":
        return ConfigChangeEvent(config=payload)
    if kind == "restart_as_admin":
        return RestartAsAdminEvent()
    if kind == "stop":
        return StopEvent()
    if kind == "worker_exit":
        if isinstance(payload, tuple) and len(payload) == 2:
            return WorkerExitEvent(session_id=int(payload[0]), exit_code=int(payload[1]))
    if kind == "worker_event":
        if isinstance(payload, tuple) and len(payload) == 2:
            event_data = payload[1]
            if isinstance(event_data, dict):
                return parse_worker_event(event_data)
    return None


# 重新导出原有接口
__all__ = [
    "StableVoiceInputApp",
    "WorkerSession",
    "FocusTarget",
    "Mode",
    "build_arg_parser",
    "build_config_from_args",
    "normalize_cli_hotkey",
    "CompositionSession",
]
