from __future__ import annotations

from typing import Any

from .stable_simple_app_compat import resolve_session_owner


def select_worker_ready_timeout_seconds(app: Any) -> float:
    """选择 worker ready 超时。"""
    session_manager = app._coordinator.session_manager
    return app.config.worker_ready_timeout_seconds(
        cold_start=not session_manager._worker_started_once,
    )


async def send_worker_command(app: Any, command: str) -> None:
    """发送 worker 命令。"""
    await app._coordinator.session_manager.send_command(command)


def build_worker_command(app: Any) -> list[str]:
    """构建 worker 命令。"""
    return app._coordinator.session_manager._build_worker_command()


async def spawn_worker(app: Any) -> Any:
    """启动 worker。"""
    return await app._coordinator.session_manager._spawn_worker()


async def read_worker_stdout(app: Any, stream: Any, session: Any) -> None:
    """读取 worker stdout。"""
    await app._coordinator.session_manager._read_worker_stdout(
        stream,
        resolve_session_owner(session),
    )


async def read_worker_stderr(app: Any, stream: Any) -> None:
    """读取 worker stderr。"""
    await app._coordinator.session_manager._read_worker_stderr(stream)


async def wait_worker(app: Any, process: Any, session_id: int) -> None:
    """等待 worker 退出。"""
    await app._coordinator.session_manager._wait_worker(process, session_id)


async def terminate_session_process(app: Any, session: Any) -> None:
    """终止指定 session 对应进程。"""
    await app._coordinator.session_manager._terminate_session_process(
        resolve_session_owner(session)
    )


async def dispose_worker(app: Any) -> None:
    """释放 worker 资源。"""
    await app._coordinator.session_manager._dispose_worker()


def apply_pending_listener_rebind(app: Any, log_tag: str) -> None:
    """应用延迟的 listener rebind。"""
    if not app._pending_listener_rebind:
        return
    app._pending_listener_rebind = False
    try:
        app._rebind_listener(app.config.effective_hotkey_vk())
    except Exception:
        app.logger.exception(log_tag)


def schedule_polisher_warmup(app: Any, reason: str) -> None:
    """调度 polisher 预热。"""
    app._coordinator._schedule_polisher_warmup(reason)


__all__ = [
    "apply_pending_listener_rebind",
    "build_worker_command",
    "dispose_worker",
    "read_worker_stderr",
    "read_worker_stdout",
    "schedule_polisher_warmup",
    "select_worker_ready_timeout_seconds",
    "send_worker_command",
    "spawn_worker",
    "terminate_session_process",
    "wait_worker",
]
