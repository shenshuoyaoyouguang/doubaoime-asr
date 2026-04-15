"""
Coordinator Privileges 模块。

提供权限监控与前景窗口检查功能。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING

from .win_privileges import restart_as_admin

if TYPE_CHECKING:
    from .injection_service import InjectionService
    from .input_injector import FocusTarget
    from .session_manager import SessionManager, WorkerSessionState

FOREGROUND_POLL_INTERVAL_S = 0.5

__all__ = [
    "_record_elevation_warning",
    "_clear_elevation_warning",
    "_elevation_status_message",
    "_handle_restart_as_admin",
    "_watch_foreground_target",
    "_check_foreground_elevation",
]


def _record_elevation_warning(
    coordinator,  # type: ignore[misc]
    target: "FocusTarget",
    *,
    log_tag: str,
) -> None:
    """记录管理员权限警告。"""
    message = _elevation_status_message(coordinator, target)
    key = (target.hwnd, target.process_id, target.process_name)
    if key != coordinator._last_elevation_warning_key:
        coordinator.logger.warning(
            "%s hwnd=%s pid=%s process=%s terminal=%s elevated=%s",
            log_tag,
            target.hwnd,
            target.process_id,
            target.process_name,
            target.terminal_kind,
            target.is_elevated,
        )
        coordinator._last_elevation_warning_key = key
    coordinator._elevation_warning_message = message
    coordinator.set_status(message)


def _clear_elevation_warning(coordinator) -> None:  # type: ignore[misc]
    """清除管理员权限警告。"""
    message = coordinator._elevation_warning_message
    coordinator._elevation_warning_message = None
    coordinator._last_elevation_warning_key = None
    session = coordinator.session_manager.get_session()
    if message and coordinator._status == message and (
        session is None or session.state != coordinator.session_manager.SessionState.STREAMING
    ):
        coordinator.set_status("空闲")


def _elevation_status_message(  # type: ignore[misc]
    coordinator,
    target,  # type: ignore[misc]
) -> str:
    """生成管理员权限状态消息。"""
    subject = "管理员终端" if target.is_terminal else "管理员窗口"
    if coordinator.enable_tray:
        return f"{subject}需要以管理员身份运行代理；请从托盘选择\"以管理员重启\""
    return f"{subject}需要以管理员身份运行代理；请重新以管理员身份启动代理"


async def _handle_restart_as_admin(coordinator) -> None:  # type: ignore[misc]
    """处理管理员重启请求。"""
    if coordinator._process_elevated:
        coordinator.set_status("代理已在管理员模式运行")
        return
    try:
        restarted = restart_as_admin(
            coordinator.launch_args,
            executable=sys.executable,
            frozen=bool(getattr(sys, "frozen", False)),
        )
    except Exception:
        coordinator.logger.exception("restart_as_admin_failed")
        coordinator.set_status("管理员重启失败，请查看 controller.log")
        return
    if not restarted:
        coordinator.logger.warning("restart_as_admin_declined")
        coordinator.set_status("管理员重启已取消或被系统拒绝")
        return
    coordinator.logger.info("restart_as_admin_requested args=%s", coordinator.launch_args)
    coordinator.set_status("正在以管理员身份重启…")
    coordinator.stop()


async def _watch_foreground_target(coordinator) -> None:  # type: ignore[misc]
    """监控前景窗口权限。"""
    try:
        while not coordinator._stopping:
            _check_foreground_elevation(coordinator)
            await asyncio.sleep(FOREGROUND_POLL_INTERVAL_S)
    except asyncio.CancelledError:
        raise
    except Exception:
        coordinator.logger.exception("foreground_watch_failed")


def _check_foreground_elevation(coordinator) -> None:  # type: ignore[misc]
    """检查前景窗口是否需要管理员权限。"""
    if coordinator._process_elevated:
        return
    session = coordinator.session_manager.get_session()
    if session is not None and session.state == coordinator.session_manager.SessionState.STREAMING:
        return
    try:
        target = coordinator.injection_service.capture_target()
    except Exception:
        coordinator.logger.exception("foreground_target_capture_failed")
        return
    if coordinator.injection_service.target_requires_admin(target):
        _record_elevation_warning(
            coordinator, target, log_tag="foreground_elevated_target_detected"
        )
        return
    _clear_elevation_warning(coordinator)