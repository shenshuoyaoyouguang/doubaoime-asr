from __future__ import annotations

import asyncio
import sys
from typing import Any

from .stable_simple_app_compat import _ListenerCompat, _convert_legacy_event
from .win_privileges import is_current_process_elevated, restart_as_admin


def bind_session_manager_event_bridge(app: Any) -> None:
    """将 SessionManager 事件桥接回 coordinator。"""
    app._coordinator.session_manager._on_event = app._forward_session_manager_event


def forward_session_manager_event(app: Any, event: object) -> None:
    """按当前 loop 状态转发 SessionManager 事件。"""
    loop = app._coordinator._loop
    if loop is not None:
        app._coordinator._emit_threadsafe(loop, event)
        return
    app._coordinator._emit(event)


def sync_process_elevation_from_wrapper(app: Any, module: Any) -> None:
    """同步 stable_simple_app 模块级 monkeypatch 后的提权状态。"""
    is_elevated = getattr(
        module,
        "is_current_process_elevated",
        is_current_process_elevated,
    )
    elevated = is_elevated() is True
    app._coordinator._process_elevated = elevated
    app._coordinator.injection_service.set_process_elevated(elevated)


async def handle_release(app: Any) -> None:
    """处理按键释放。"""
    await app._coordinator._handle_release()


async def restart_worker(app: Any) -> None:
    """重启 worker。"""
    await app._coordinator.session_manager.restart_worker()


def rebind_listener(app: Any, hotkey_vk: int) -> None:
    """更新热键绑定。"""
    app._coordinator.hotkey_service.update_hotkey(hotkey_vk)


def build_listener(app: Any, hotkey_vk: int) -> Any:
    """构建 listener compat 包装。"""
    return _ListenerCompat(app._coordinator.hotkey_service, hotkey_vk)


def handle_restart_as_admin(app: Any, module: Any) -> None:
    """处理管理员重启请求，保留 stable_simple_app 的 monkeypatch 兼容面。"""
    sync_process_elevation_from_wrapper(app, module)
    restart = getattr(module, "restart_as_admin", restart_as_admin)

    if app._process_elevated:
        app.set_status("进程已在管理员模式运行")
        return

    try:
        restarted = restart(
            app.launch_args,
            executable=sys.executable,
            frozen=bool(getattr(sys, "frozen", False)),
        )
    except Exception:
        app.logger.exception("restart_as_admin_failed")
        app.set_status("管理员提升失败，请查看 controller.log")
        return

    if not restarted:
        app.logger.warning("restart_as_admin_declined")
        app.set_status("管理员重启已取消或被系统拒绝")
        return

    app.logger.info("restart_as_admin_requested args=%s", app.launch_args)
    app.set_status("正在以管理员身份重启…")
    app._stopping = True


def should_enable_inline_streaming(app: Any, target: Any) -> bool:
    """判断目标是否应启用 inline streaming。"""
    return app._coordinator.injection_service.should_enable_inline_streaming(target)


def target_requires_admin(app: Any, target: Any) -> bool:
    """判断目标是否需要管理员权限。"""
    return app._coordinator.injection_service.target_requires_admin(target)


def check_foreground_elevation(app: Any) -> None:
    """检查前台窗口提权状态。"""
    app._coordinator._check_foreground_elevation()


def record_elevation_warning(app: Any, target: Any, *, log_tag: str) -> None:
    """记录提权告警。"""
    app._coordinator._record_elevation_warning(target, log_tag=log_tag)


def clear_elevation_warning(app: Any) -> None:
    """清理提权告警。"""
    app._coordinator._clear_elevation_warning()


def elevation_status_message(app: Any, target: Any) -> str:
    """构建提权状态文案。"""
    return app._coordinator._elevation_status_message(target)


def emit_legacy_event(app: Any, kind: str, payload: object = None) -> None:
    """将 legacy tuple 事件转换后转发到 coordinator。"""
    event = _convert_legacy_event(kind, payload)
    if event is not None:
        app._coordinator._emit(event)


def emit_legacy_event_threadsafe(
    app: Any,
    loop: asyncio.AbstractEventLoop,
    kind: str,
    payload: object = None,
) -> None:
    """线程安全地转发 legacy tuple 事件。"""
    event = _convert_legacy_event(kind, payload)
    if event is not None:
        app._coordinator._emit_threadsafe(loop, event)


__all__ = [
    "bind_session_manager_event_bridge",
    "build_listener",
    "check_foreground_elevation",
    "clear_elevation_warning",
    "elevation_status_message",
    "emit_legacy_event",
    "emit_legacy_event_threadsafe",
    "forward_session_manager_event",
    "handle_release",
    "handle_restart_as_admin",
    "record_elevation_warning",
    "rebind_listener",
    "restart_worker",
    "should_enable_inline_streaming",
    "sync_process_elevation_from_wrapper",
    "target_requires_admin",
]
