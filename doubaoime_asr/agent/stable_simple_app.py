"""
StableVoiceInputApp - 向后兼容入口。

委托给 VoiceInputCoordinator，保持原有公开接口不变。
"""
from __future__ import annotations

import argparse
import asyncio
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
from .input_injector import FocusTarget

# 重新导出测试所需的模块（向后兼容）
from .runtime_logging import setup_named_logger
from .injection_manager import TextInjectionManager
from .overlay_preview import OverlayPreview
from .overlay_scheduler import OverlayRenderScheduler
from .text_polisher import TextPolisher
from .tip_gateway import build_tip_gateway_from_env
from .win_audio_output import SystemOutputMuteGuard
from .win_privileges import is_current_process_elevated, restart_as_admin
from .composition import CompositionSession  # [P1-Fix4] real re-export, not None
from .stable_simple_app_compat import (
    WorkerSession,
    get_managed_session,
    sync_managed_session,
    _InjectionManagerCompat,
    _PreviewCompat,
    _SchedulerCompat,
    _SessionCompat,
    _SessionCompatWrapper,
    _ListenerCompat,
)
from .stable_simple_app_runtime import (
    apply_config_update as compat_apply_config_update,
    apply_inline_interim as compat_apply_inline_interim,
    clear_session_state as compat_clear_session_state,
    clear_active_session as compat_clear_active_session,
    ensure_worker as compat_ensure_worker,
    handle_worker_event as compat_handle_worker_event,
    handle_worker_exit as compat_handle_worker_exit,
    handle_press as compat_handle_press,
    handle_inline_failure as compat_handle_inline_failure,
    handle_inline_focus_changed as compat_handle_inline_focus_changed,
    inject_final as compat_inject_final,
    prepare_final_text_compat as compat_prepare_final_text_compat,
    send_stop as compat_send_stop,
    send_stop_if_needed as compat_send_stop_if_needed,
    terminate_worker as compat_terminate_worker,
)
from .stable_simple_app_bootstrap import (
    bind_runtime_compat_hooks,
    bootstrap_runtime_services,
    capture_runtime_impls,
)
from .stable_simple_app_worker_bridge import (
    apply_pending_listener_rebind as worker_apply_pending_listener_rebind,
    build_worker_command as worker_build_worker_command,
    dispose_worker as worker_dispose_worker,
    read_worker_stderr as worker_read_worker_stderr,
    read_worker_stdout as worker_read_worker_stdout,
    schedule_polisher_warmup as worker_schedule_polisher_warmup,
    select_worker_ready_timeout_seconds as worker_select_worker_ready_timeout_seconds,
    send_worker_command as worker_send_worker_command,
    spawn_worker as worker_spawn_worker,
    terminate_session_process as worker_terminate_session_process,
    wait_worker as worker_wait_worker,
)
from .stable_simple_app_coordinator_bridge import (
    activate_capture_output as coordinator_activate_capture_output,
    mode_display_label as coordinator_mode_display_label,
    polisher_config_changed as coordinator_polisher_config_changed,
    release_capture_output as coordinator_release_capture_output,
    resolve_final_text as coordinator_resolve_final_text,
    session_start_status as coordinator_session_start_status,
    status_for_final_result as coordinator_status_for_final_result,
)
from .stable_simple_app_state_bridge import (
    get_capture_output_guard as state_get_capture_output_guard,
    get_coordinator_attr as state_get_coordinator_attr,
    get_injection_manager as state_get_injection_manager,
    get_overlay_scheduler as state_get_overlay_scheduler,
    get_preview as state_get_preview,
    get_status as state_get_status,
    get_text_polisher as state_get_text_polisher,
    set_coordinator_attr as state_set_coordinator_attr,
    set_status as state_set_status,
)
from .stable_simple_app_session_bridge import (
    has_test_session_override as session_has_test_session_override,
    reset_test_session_override as session_reset_test_session_override,
    uses_runtime_session_flow as session_uses_runtime_session_flow,
    wrap_session as session_wrap_session,
)
from .stable_simple_app_bridge import (
    bind_session_manager_event_bridge,
    build_listener as bridge_build_listener,
    check_foreground_elevation,
    clear_elevation_warning,
    elevation_status_message,
    emit_legacy_event,
    emit_legacy_event_threadsafe,
    forward_session_manager_event,
    handle_release as bridge_handle_release,
    handle_restart_as_admin as bridge_handle_restart_as_admin,
    record_elevation_warning,
    rebind_listener as bridge_rebind_listener,
    restart_worker as bridge_restart_worker,
    should_enable_inline_streaming,
    sync_process_elevation_from_wrapper,
    target_requires_admin,
)


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
            tip_gateway=build_tip_gateway_from_env(),
        )

        # [P1-Fix1] 用模块级工厂（已被 monkeypatch 替换）重建各 service 实例。
        # 直接引用 stable_simple_app 模块命名空间中的名字——这正是 monkeypatch
        # 所替换的那些名字。
        import doubaoime_asr.agent.stable_simple_app as _self_module  # noqa: PLC0415
        _sched_compat = bootstrap_runtime_services(self, _self_module, config)

        # 显式测试会话覆盖；_UNSET 表示未被外部设置
        self.__test_session: Any = _UNSET

        # 保存运行态原始实现，仅在 compat 显式接管时回落使用
        capture_runtime_impls(self)

        # 将 coordinator 关键回调绑定回 facade，保留 monkeypatch 兼容面
        bind_runtime_compat_hooks(self)

        # [compat-singleton] 缓存 compat 包装器，保证多次访问时状态持续累积。
        # _overlay_scheduler_compat 直接使用上面创建的单例（已注入为内部 scheduler）。
        self._injection_manager_compat: _InjectionManagerCompat | None = None
        self._preview_compat: _PreviewCompat | None = None
        self._overlay_scheduler_compat: _SchedulerCompat = _sched_compat

        self._bind_session_manager_event_bridge()
        self._sync_process_elevation_from_wrapper()

    def _has_test_session_override(self) -> bool:
        """是否存在显式 compat 会话覆盖。"""
        return session_has_test_session_override(self.__test_session, _UNSET)

    def _reset_test_session_override(self) -> None:
        """清除显式 compat 会话覆盖。"""
        self.__test_session = session_reset_test_session_override(_UNSET)

    def _wrap_session(self, session: Any) -> _SessionCompatWrapper:
        """统一构造 runtime session 的 compat 包装。"""
        return session_wrap_session(session)

    def _uses_runtime_session_flow(self, session: Any) -> bool:
        """当前会话是否应走运行态 SessionManager/InjectionService 流程。"""
        return session_uses_runtime_session_flow(
            test_session=self.__test_session,
            unset=_UNSET,
            session=session,
        )

    def _bind_session_manager_event_bridge(self) -> None:
        """将 SessionManager 事件桥接回 coordinator。"""
        bind_session_manager_event_bridge(self)

    def _forward_session_manager_event(self, event: object) -> None:
        """转发 SessionManager 事件。"""
        forward_session_manager_event(self, event)

    def _sync_process_elevation_from_wrapper(self) -> None:
        """同步 wrapper 侧的进程提权状态。"""
        import doubaoime_asr.agent.stable_simple_app as _self_module  # noqa: PLC0415

        sync_process_elevation_from_wrapper(self, _self_module)


    # ===== 向后兼容属性 =====

    @property
    def config(self) -> AgentConfig:
        """获取配置。"""
        return state_get_coordinator_attr(self, "config")

    @config.setter
    def config(self, value: AgentConfig) -> None:
        """设置配置。"""
        state_set_coordinator_attr(self, "config", value)

    @property
    def mode(self) -> Mode:
        """获取模式。"""
        return state_get_coordinator_attr(self, "mode")

    @mode.setter
    def mode(self, value: Mode) -> None:
        """设置模式。"""
        state_set_coordinator_attr(self, "mode", value)

    @property
    def logger(self) -> Any:
        """获取日志器。"""
        return state_get_coordinator_attr(self, "logger")

    @property
    def injection_manager(self) -> Any:
        """获取注入管理器（向后兼容）。

        [compat-singleton] 返回固定的包装器实例，以便 injector.calls 可以跨多次
        属性访问持续累积。
        """
        return state_get_injection_manager(self)

    @property
    def preview(self) -> Any:
        """获取预览（向后兼容）。"""
        return state_get_preview(self)

    @property
    def overlay_scheduler(self) -> Any:
        """获取调度器（向后兼容）。

        [compat-singleton] 返回固定的包装器实例（已在 __init__ 中创建并注入为
        overlay_service._scheduler），以便 calls 可以捕获所有调用（包括
        coordinator 内部直接对 overlay_service 的调用）。
        """
        return state_get_overlay_scheduler(self)

    @property
    def text_polisher(self) -> Any:
        """获取润色器。"""
        return state_get_text_polisher(self)

    @property
    def capture_output_guard(self) -> Any:
        """获取静音守护。"""
        return state_get_capture_output_guard(self)

    # ===== 状态属性 =====

    @property
    def _status(self) -> str:
        """获取状态。"""
        return state_get_status(self)

    @_status.setter
    def _status(self, value: str) -> None:
        """设置状态。"""
        state_set_status(self, value)

    @property
    def _session(self) -> Any:
        """获取会话对象。"""
        return get_managed_session(
            self._coordinator.session_manager,
            test_session=self.__test_session,
            unset=_UNSET,
        )

    @_session.setter
    def _session(self, value: Any) -> None:
        """设置会话对象。"""
        self.__test_session = value
        sync_managed_session(self._coordinator.session_manager, value)

    @property
    def _stopping(self) -> bool:
        """获取停止状态。"""
        return state_get_coordinator_attr(self, "_stopping")

    @_stopping.setter
    def _stopping(self, value: bool) -> None:
        """设置停止状态。"""
        state_set_coordinator_attr(self, "_stopping", value)

    @property
    def launch_args(self) -> list[str]:
        """获取启动参数。"""
        return state_get_coordinator_attr(self, "launch_args")

    @launch_args.setter
    def launch_args(self, value: list[str]) -> None:
        """设置启动参数。"""
        state_set_coordinator_attr(self, "launch_args", value)

    @property
    def enable_tray(self) -> bool:
        """获取托盘启用状态。"""
        return state_get_coordinator_attr(self, "enable_tray")

    @property
    def console(self) -> bool:
        """获取控制台输出状态。"""
        return state_get_coordinator_attr(self, "console")

    @property
    def _process_elevated(self) -> bool:
        """获取进程是否提升权限。"""
        return state_get_coordinator_attr(self, "_process_elevated")

    @property
    def _pending_listener_rebind(self) -> bool:
        """获取待重绑状态。"""
        return state_get_coordinator_attr(self, "_pending_listener_rebind")

    @_pending_listener_rebind.setter
    def _pending_listener_rebind(self, value: bool) -> None:
        """设置待重绑状态。"""
        state_set_coordinator_attr(self, "_pending_listener_rebind", value)

    @property
    def _pending_worker_restart(self) -> bool:
        """获取待重启状态。"""
        return state_get_coordinator_attr(self, "_pending_worker_restart")

    @_pending_worker_restart.setter
    def _pending_worker_restart(self, value: bool) -> None:
        """设置待重启状态。"""
        state_set_coordinator_attr(self, "_pending_worker_restart", value)

    @property
    def _pending_polisher_warmup(self) -> bool:
        """获取待预热状态。"""
        return state_get_coordinator_attr(self, "_pending_polisher_warmup")

    @_pending_polisher_warmup.setter
    def _pending_polisher_warmup(self, value: bool) -> None:
        """设置待预热状态。"""
        state_set_coordinator_attr(self, "_pending_polisher_warmup", value)

    # ===== 公开方法委托 =====

    def set_status(self, value: str) -> None:
        """设置状态。"""
        state_set_status(self, value)

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
        """转发 legacy 事件。"""
        emit_legacy_event(self, kind, payload)

    def _emit_threadsafe(self, loop: asyncio.AbstractEventLoop, kind: str, payload: object = None) -> None:
        """线程安全地转发 legacy 事件。"""
        emit_legacy_event_threadsafe(self, loop, kind, payload)


    async def _handle_press(self) -> None:
        """处理按下，保持 legacy facade 兼容。"""
        await compat_handle_press(self)


    def _clear_session_state(self) -> None:
        """内部：清除会话关联状态（不含 capture_output）。"""
        compat_clear_session_state(self)

    async def _handle_release(self) -> None:
        """处理释放（向后兼容）。"""
        await bridge_handle_release(self)

    async def _send_stop(self, log_tag: str, status: str) -> None:
        """发送停止（向后兼容）。

        [P1-Fix2] 经由 self._send_worker_command 发送，支持 monkeypatch。
        """
        await compat_send_stop(self, log_tag, status)

    async def _inject_final(self, text: str) -> None:
        """注入最终文本（向后兼容）。

        [P1-Fix2/Fix3] 从 self._session 读取 composition/inline_streaming_enabled/
        final_injection_blocked，这些字段存储在 compat WorkerSession 上。
        """
        await compat_inject_final(self, text)

    async def _clear_active_session(self) -> None:
        """清除活跃会话（向后兼容）。

        [P1-Fix2] 经由 self._session 修改状态，支持 monkeypatch。
        """
        await compat_clear_active_session(self)

    async def _handle_worker_exit(self, session_id: int, code: int) -> None:
        """处理 Worker 退出。"""
        await compat_handle_worker_exit(self, session_id, code)

    async def _handle_worker_event(self, session_id: int, event: object) -> None:
        """处理 Worker 事件（向后兼容）。"""
        await compat_handle_worker_event(self, session_id, event)

    async def _apply_config(self, new_config: AgentConfig) -> None:
        """应用配置（向后兼容）。

        [P1-Fix2] 经由 self._rebind_listener / self._restart_worker 钩子，
        使测试 monkeypatch 正常生效。
        """
        await compat_apply_config_update(self, new_config)

    async def _restart_worker(self) -> None:
        """重启 Worker（向后兼容）。"""
        await bridge_restart_worker(self)

    async def _terminate_worker(self) -> None:
        """终止 Worker（向后兼容）。

        [P1-Fix2] 经由 self._send_worker_command 发送 EXIT，支持 monkeypatch。
        实现与 session_manager._terminate_session_process 等效的逻辑。
        """
        await compat_terminate_worker(self)

    def _select_worker_ready_timeout_seconds(self) -> float:
        return worker_select_worker_ready_timeout_seconds(self)


    async def _ensure_worker(self) -> Any:
        """确保 Worker（向后兼容）。

        [P1-Fix2] 通过 self._spawn_worker、self._terminate_session_process 等
        钩子实现，支持测试 monkeypatch 这些方法。
        如果外部显式设置了 session（测试通过 app._session = ...），直接返回。
        """
        return await compat_ensure_worker(self)

    def _rebind_listener(self, hotkey_vk: int) -> None:
        """重绑定监听器（向后兼容）。"""
        bridge_rebind_listener(self, hotkey_vk)

    def _build_listener(self, loop: asyncio.AbstractEventLoop, hotkey_vk: int) -> Any:
        """构建监听器（向后兼容）。"""
        return bridge_build_listener(self, hotkey_vk)

    def _should_enable_inline_streaming(self, target: FocusTarget) -> bool:
        """判断是否启用流式上屏（向后兼容）。"""
        return should_enable_inline_streaming(self, target)

    def _target_requires_admin(self, target: FocusTarget | None) -> bool:
        """判断目标是否需要管理员（向后兼容）。"""
        return target_requires_admin(self, target)

    def _check_foreground_elevation(self) -> None:
        """检查前景权限（向后兼容）。"""
        check_foreground_elevation(self)

    def _record_elevation_warning(self, target: FocusTarget, *, log_tag: str) -> None:
        """记录权限警告（向后兼容）。"""
        record_elevation_warning(self, target, log_tag=log_tag)

    def _clear_elevation_warning(self) -> None:
        """清除权限警告（向后兼容）。"""
        clear_elevation_warning(self)

    async def _handle_restart_as_admin(self) -> None:
        """处理管理员重启请求。"""
        import doubaoime_asr.agent.stable_simple_app as _self_module  # noqa: PLC0415
        bridge_handle_restart_as_admin(self, _self_module)


    async def _send_stop_if_needed(self) -> None:
        """发送停止如果需要（向后兼容）。"""
        await compat_send_stop_if_needed(self)

    async def _apply_inline_interim(self, text: str) -> None:
        """应用流式中间结果（向后兼容）。

        [P1-Fix3] 从 self._session（compat WorkerSession）读写 composition、
        inline_streaming_enabled、final_injection_blocked，而不是委托给
        injection_service（后者有自己独立的 InjectionSessionState）。
        """
        await compat_apply_inline_interim(self, text)

    async def _prepare_final_text(self, text: str) -> None:
        """准备流式最终文本（向后兼容）。"""
        await self._prepare_final_text_compat(text)

    async def _prepare_final_text_compat(self, text: str) -> None:
        """准备流式最终文本（从 compat WorkerSession 读取 composition）。

        [P1-Fix3] 从 self._session（compat WorkerSession）读写 composition，
        不依赖 injection_service._session。
        """
        await compat_prepare_final_text_compat(self, text)

    def _activate_capture_output(self) -> str | None:
        """激活静音（向后兼容）。"""
        return coordinator_activate_capture_output(self)

    def _release_capture_output(self) -> str | None:
        """释放静音（向后兼容）。"""
        return coordinator_release_capture_output(self)

    def _mode_display_label(self, mode: Mode | None = None) -> str:
        """模式显示标签（向后兼容）。"""
        return coordinator_mode_display_label(self, mode)

    def _session_start_status(self, capture_output_warning: str | None) -> str:
        """会话启动状态（向后兼容）。"""
        return coordinator_session_start_status(self, capture_output_warning)

    def _status_for_final_result(self, result: Any, raw_text: str) -> str:
        """最终结果状态（向后兼容）。"""
        return coordinator_status_for_final_result(self, result, raw_text)

    async def _resolve_final_text(self, raw_text: str) -> Any:
        """解析最终文本（向后兼容）。"""
        return await coordinator_resolve_final_text(self, raw_text)

    async def _send_worker_command(self, command: str) -> None:
        """发送 Worker 命令（向后兼容）。"""
        await worker_send_worker_command(self, command)

    def _build_worker_command(self) -> list[str]:
        """构建 Worker 命令（向后兼容）。"""
        return worker_build_worker_command(self)

    async def _spawn_worker(self) -> Any:
        """生成 Worker（向后兼容）。"""
        return await worker_spawn_worker(self)

    async def _read_worker_stdout(self, stream: Any, session: Any) -> None:
        """读取 Worker stdout。"""
        await worker_read_worker_stdout(self, stream, session)


    async def _read_worker_stderr(self, stream: Any) -> None:
        """读取 Worker stderr（向后兼容）。"""
        await worker_read_worker_stderr(self, stream)

    async def _wait_worker(self, process: Any, session_id: int) -> None:
        """等待 Worker（向后兼容）。"""
        await worker_wait_worker(self, process, session_id)

    async def _terminate_session_process(self, session: Any) -> None:
        """终止会话进程（向后兼容）。"""
        await worker_terminate_session_process(self, session)


    async def _dispose_worker(self) -> None:
        """清理 Worker（向后兼容）。"""
        await worker_dispose_worker(self)

    def _handle_inline_focus_changed(self, log_tag: str) -> None:
        """处理流式焦点变化（向后兼容）。

        [P1-Fix3] 直接修改 self._session 上的字段。
        """
        compat_handle_inline_focus_changed(self, log_tag)

    def _handle_inline_failure(self, composition: Any, *, log_tag: str, fallback_status: str | None = None, blocked_status: str = "实时上屏失败，仅保留识别") -> bool:
        """处理流式失败（向后兼容）。

        [P1-Fix3] 直接修改 self._session 上的字段。
        """
        return compat_handle_inline_failure(
            self,
            composition,
            log_tag=log_tag,
            fallback_status=fallback_status,
            blocked_status=blocked_status,
        )

    def _elevation_status_message(self, target: FocusTarget) -> str:
        """权限状态消息（向后兼容）。"""
        return elevation_status_message(self, target)

    def _polisher_config_changed(self, old_config: AgentConfig, new_config: AgentConfig) -> bool:
        """润色配置变化（向后兼容）。"""
        return coordinator_polisher_config_changed(self, old_config, new_config)

    def _apply_pending_listener_rebind(self, log_tag: str) -> None:
        """应用待重绑（向后兼容）。

        [P1-Fix2] 经由 self._rebind_listener 钩子，支持 monkeypatch。
        """
        worker_apply_pending_listener_rebind(self, log_tag)


    def _schedule_polisher_warmup(self, reason: str) -> None:
        """调度润色预热（向后兼容）。"""
        worker_schedule_polisher_warmup(self, reason)


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
