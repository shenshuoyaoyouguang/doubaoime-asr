"""
SessionManager - Worker 进程状态管理器。

从 Controller 中提取 WorkerSession 状态管理,封装 worker 进程生命周期。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass, field
from enum import Enum
import logging
from pathlib import Path
import sys
import time
from typing import Any, Callable

from .config import AgentConfig
from .events import (
    VoiceInputEvent,
    WorkerReadyEvent,
    parse_worker_event,
)
from .input_injector import FocusTarget
from .protocol import ProtocolDecodeError, decode_event


# 类型别名
Mode = str  # "recognize" | "inject"


class WorkerSessionState(Enum):
    """Worker 会话状态机。"""
    IDLE = "idle"          # 无进程或进程已终止
    STARTING = "starting"  # 进程正在启动,等待就绪
    READY = "ready"        # 进程就绪,空闲等待
    STREAMING = "streaming"  # 正在录音/识别
    STOPPING = "stopping"  # 发送了 STOP,等待结束
    TERMINATING = "terminating"  # 正在终止进程


@dataclass(slots=True)
class WorkerSession:
    """Worker 进程会话状态。"""
    session_id: int
    process: asyncio.subprocess.Process
    state: WorkerSessionState = WorkerSessionState.IDLE
    stdout_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None
    wait_task: asyncio.Task[None] | None = None
    process_ready: bool = False
    target: FocusTarget | None = None
    mode: Mode = "inject"
    stop_sent: bool = False
    ready: bool = False
    streaming_started: bool = False
    pending_stop: bool = False
    segment_texts: dict[int, str] = field(default_factory=dict)
    finalized_segment_indexes: set[int] = field(default_factory=set)
    active_segment_index: int | None = None
    stop_sent_at: float | None = None
    finished_at: float | None = None

    def transition_to(self, new_state: WorkerSessionState) -> None:
        """状态转换。"""
        self.state = new_state

    def begin(
        self,
        target: FocusTarget | None,
        mode: Mode,
    ) -> None:
        """开始会话。"""
        self.target = target
        self.mode = mode
        self.stop_sent = False
        self.ready = False
        self.streaming_started = False
        self.pending_stop = False
        self.segment_texts.clear()
        self.finalized_segment_indexes.clear()
        self.active_segment_index = None
        self.stop_sent_at = None
        self.finished_at = None
        self.transition_to(WorkerSessionState.STREAMING)

    def clear_active(self) -> None:
        """清除活跃状态。"""
        self.target = None
        self.mode = "inject"
        self.stop_sent = False
        self.ready = False
        self.streaming_started = False
        self.pending_stop = False
        self.segment_texts.clear()
        self.finalized_segment_indexes.clear()
        self.active_segment_index = None
        if self.state in (WorkerSessionState.STREAMING, WorkerSessionState.STOPPING):
            self.transition_to(WorkerSessionState.READY)

    def mark_stop_sent(self) -> None:
        """标记已发送 STOP 命令。"""
        self.stop_sent = True
        self.pending_stop = False
        self.stop_sent_at = time.perf_counter()
        self.finished_at = None
        self.transition_to(WorkerSessionState.STOPPING)

    def mark_finished(self) -> None:
        """标记会话完成。"""
        self.clear_active()


class SessionManager:
    """Worker 进程状态管理器。"""

    def __init__(
        self,
        config: AgentConfig,
        logger: logging.Logger,
        on_event: Callable[[VoiceInputEvent], None] | None = None,
    ) -> None:
        self.config = config
        self.logger = logger
        self._on_event = on_event
        self._session: WorkerSession | None = None
        self._next_session_id = 0
        self._stopping = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker_started_once = False

    # ===== 生命周期管理 =====

    async def ensure_worker(self) -> WorkerSession:
        """确保 Worker 进程运行并就绪。"""
        if self._session is not None and self._session.process.returncode is None:
            if self._session.process_ready:
                return self._session
            # 进程存活但未就绪，检查是否处于 STARTING 状态（正在等待就绪）
            if self._session.state == WorkerSessionState.STARTING:
                # 复用现有的等待逻辑，等待其就绪
                await self._wait_for_ready()
                return self._session

        if self._session is not None and self._session.process.returncode is not None:
            await self._dispose_worker()

        process = await self._spawn_worker()
        self._next_session_id += 1
        session = WorkerSession(
            session_id=self._next_session_id,
            process=process,
            state=WorkerSessionState.STARTING,
        )
        session.stdout_task = asyncio.create_task(
            self._read_worker_stdout(process.stdout, session)
        )
        session.stderr_task = asyncio.create_task(
            self._read_worker_stderr(process.stderr)
        )
        session.wait_task = asyncio.create_task(
            self._wait_worker(process, session.session_id)
        )
        self._session = session
        self._loop = asyncio.get_running_loop()

        # 等待进程就绪
        await self._await_session_ready(session)
        return session

    async def terminate_worker(self) -> None:
        """终止 Worker 进程。"""
        if self._session is None:
            return
        self._session.transition_to(WorkerSessionState.TERMINATING)
        await self._terminate_session_process(self._session)
        await self._dispose_worker()

    async def _wait_for_ready(self) -> None:
        """等待现有 Worker 进程就绪。"""
        if self._session is None:
            return
        self._loop = asyncio.get_running_loop()
        await self._await_session_ready(self._session)

    async def _await_session_ready(self, session: WorkerSession) -> None:
        """等待会话就绪的通用逻辑。"""
        timeout_s = self._select_ready_timeout_seconds()
        self.logger.info(
            "worker_wait_ready session_id=%s start_kind=%s timeout_ms=%s",
            session.session_id,
            "warm" if self._worker_started_once else "cold",
            int(timeout_s * 1000),
        )
        started_at = self._loop.time()
        deadline = started_at + timeout_s
        while self._loop.time() < deadline:
            if session.process_ready:
                session.transition_to(WorkerSessionState.READY)
                self._worker_started_once = True
                self.logger.info("worker_ready session_id=%s pid=%s", session.session_id, session.process.pid)
                return
            if session.process.returncode is not None:
                break
            await asyncio.sleep(0.02)

        await self._terminate_session_process(session)
        await self._dispose_worker()
        self.logger.warning(
            "worker_wait_ready_timeout session_id=%s waited_ms=%s",
            session.session_id,
            int((self._loop.time() - started_at) * 1000),
        )
        raise RuntimeError("worker process did not become ready")

    async def restart_worker(self) -> None:
        """重启 Worker 进程。"""
        await self.terminate_worker()
        if not self._stopping:
            await self.ensure_worker()

    # ===== 会话管理 =====

    def begin_session(self, target: FocusTarget | None, mode: Mode) -> None:
        """开始录音会话。"""
        if self._session is None:
            raise RuntimeError("worker process not ready")
        self._session.begin(target, mode)

    def clear_session(self) -> None:
        """清除会话状态。"""
        if self._session is None:
            return
        self._session.clear_active()

    # ===== 命令发送 =====

    async def send_command(self, command: str) -> None:
        """向 Worker 发送命令。"""
        if self._session is None or self._session.process.stdin is None:
            raise RuntimeError("worker process is not running")
        self._session.process.stdin.write(f"{command}\n".encode("utf-8"))
        await self._session.process.stdin.drain()
        self.logger.info("worker_command_sent command=%s session_id=%s", command, self._session.session_id)

    async def send_stop(self) -> None:
        """发送 STOP 命令。"""
        if self._session is None:
            return
        await self.send_command("STOP")
        self._session.mark_stop_sent()

    # ===== 状态查询 =====

    def is_active(self) -> bool:
        """检查是否有活跃会话。"""
        return self._session is not None and self._session.state == WorkerSessionState.STREAMING

    def is_ready(self) -> bool:
        """检查 Worker 是否就绪。"""
        return self._session is not None and self._session.state == WorkerSessionState.READY

    def is_streaming(self) -> bool:
        """检查是否正在流式传输。"""
        return self._session is not None and self._session.state == WorkerSessionState.STREAMING

    def get_session(self) -> WorkerSession | None:
        """获取当前会话。"""
        return self._session

    def get_state(self) -> WorkerSessionState:
        """获取当前状态。"""
        if self._session is None:
            return WorkerSessionState.IDLE
        return self._session.state

    # ===== 事件处理 =====

    def handle_worker_event(self, event_data: dict[str, Any]) -> VoiceInputEvent | None:
        """处理从 Worker 接收的事件数据。"""
        event = parse_worker_event(event_data)
        event_type = event.event_type

        if self._session is None:
            self.logger.info("worker_event_ignored reason=no_session type=%s", event_type)
            return None

        # 处理 worker_ready 事件
        if event_type == "worker_ready":
            self._session.process_ready = True
            self._session.transition_to(WorkerSessionState.READY)
            self.logger.info("worker_process_ready session_id=%s", self._session.session_id)
            return event

        # 处理 ready 事件
        if event_type == "ready":
            if self._session.state == WorkerSessionState.STREAMING:
                self._session.ready = True
            return event

        # 处理 streaming_started 事件
        if event_type == "streaming_started":
            if self._session.state == WorkerSessionState.STREAMING:
                self._session.streaming_started = True
            return event

        # 处理 finished 事件
        if event_type == "finished":
            if self._session.stop_sent_at is not None:
                self._session.finished_at = time.perf_counter()
                self.logger.info(
                    "worker_finished_timing session_id=%s stop_to_finished_ms=%d",
                    self._session.session_id,
                    int((self._session.finished_at - self._session.stop_sent_at) * 1000),
                )
            self._session.mark_finished()
            return event

        return event

    def handle_worker_exit(self, session_id: int, exit_code: int) -> None:
        """处理 Worker 进程退出。"""
        if self._session is None or self._session.session_id != session_id:
            self.logger.info(
                "worker_exit_ignored session_id=%s current_session_id=%s code=%s",
                session_id,
                self._session.session_id if self._session is not None else None,
                exit_code,
            )
            return
        self.logger.info("worker_exit session_id=%s code=%s", session_id, exit_code)
        self._session.transition_to(WorkerSessionState.IDLE)

    # ===== 内部方法 =====

    async def _spawn_worker(self) -> asyncio.subprocess.Process:
        """启动 Worker 进程。"""
        command = self._build_worker_command()
        self.logger.info("worker_spawn cmd=%s", command)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.cwd()),
            env=self._build_worker_env(),
        )
        self.logger.info("worker_spawned pid=%s", process.pid)
        return process

    def _build_worker_command(self) -> list[str]:
        """构建 Worker 启动命令。"""
        args = [
            "--worker",
            "--credential-path",
            self.config.credential_path or AgentConfig.default().credential_path or "",
        ]
        if self.config.auto_rotate_device:
            args.append("--auto-rotate-device")
        if self.config.microphone_device is not None:
            args.extend(["--mic-device", str(self.config.microphone_device)])

        if getattr(sys, "frozen", False):
            return [sys.executable, *args]
        return [sys.executable, "-m", "doubaoime_asr.agent.stable_main", *args]

    def _build_worker_env(self) -> dict[str, str]:
        """构建 Worker 进程环境变量。"""
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _select_ready_timeout_seconds(self) -> float:
        return self.config.worker_ready_timeout_seconds(
            cold_start=not self._worker_started_once,
        )

    async def _read_worker_stdout(
        self,
        stream: asyncio.StreamReader | None,
        session: WorkerSession,
    ) -> None:
        """读取 Worker stdout。"""
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                event_data = decode_event(raw)
            except (json.JSONDecodeError, ProtocolDecodeError):
                self.logger.error("worker_stdout_invalid=%s", raw)
                continue
            # 处理事件
            event = self.handle_worker_event(event_data)
            if event and self._on_event:
                self._on_event(event)

    async def _read_worker_stderr(self, stream: asyncio.StreamReader | None) -> None:
        """读取 Worker stderr。"""
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            self.logger.error("worker_stderr=%s", line.decode("utf-8", errors="replace").rstrip())

    async def _wait_worker(
        self,
        process: asyncio.subprocess.Process,
        session_id: int,
    ) -> None:
        """等待 Worker 进程退出。"""
        code = await process.wait()
        self.handle_worker_exit(session_id, code)
        if self._on_event:
            from .events import WorkerExitEvent
            self._on_event(WorkerExitEvent(session_id=session_id, exit_code=code))

    async def _terminate_session_process(self, session: WorkerSession) -> None:
        """终止会话进程。"""
        process = session.process
        grace_timeout_s = self.config.worker_exit_grace_timeout_seconds()
        kill_wait_timeout_s = self.config.worker_kill_wait_timeout_seconds()
        if process.stdin is not None and process.returncode is None:
            with contextlib.suppress(Exception):
                await self.send_command("EXIT")
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_timeout_s)
        except (asyncio.TimeoutError, ProcessLookupError):
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
                await asyncio.wait_for(process.wait(), timeout=kill_wait_timeout_s)

    async def _dispose_worker(self) -> None:
        """清理 Worker 资源。"""
        if self._session is None:
            return
        session = self._session
        for task in (session.stdout_task, session.stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        wait_task = session.wait_task
        if wait_task is not None and not wait_task.done():
            if session.process.returncode is None:
                wait_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await wait_task
            else:
                with contextlib.suppress(Exception):
                    await wait_task
        await self._close_process_streams(session.process)
        self._session = None

    async def _close_process_streams(self, process: asyncio.subprocess.Process) -> None:
        """显式关闭 subprocess pipe,避免 transport 在后续 GC 时泄漏 warning。"""
        stdin = getattr(process, "stdin", None)
        if stdin is not None:
            with contextlib.suppress(Exception):
                stdin.close()
            wait_closed = getattr(stdin, "wait_closed", None)
            if callable(wait_closed):
                with contextlib.suppress(Exception):
                    await wait_closed()
            with contextlib.suppress(Exception):
                process.stdin = None

        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is None:
                continue
            transport = getattr(stream, "_transport", None)
            close = getattr(transport, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
            with contextlib.suppress(Exception):
                setattr(process, stream_name, None)

    def stop(self) -> None:
        """标记停止。"""
        self._stopping = True

    def is_stopping(self) -> bool:
        """检查是否正在停止。"""
        return self._stopping


# 导出
__all__ = [
    "WorkerSessionState",
    "WorkerSession",
    "SessionManager",
    "Mode",
]
