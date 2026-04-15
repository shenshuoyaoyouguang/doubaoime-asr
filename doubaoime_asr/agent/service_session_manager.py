from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable

from .config import AgentConfig
from .events import (
    ErrorEvent,
    FallbackRequiredEvent,
    FinalResultEvent,
    FinishedEvent,
    InterimResultEvent,
    ServiceResolvedFinalEvent,
    VoiceInputEvent,
    WorkerExitEvent,
    WorkerStatusEvent,
)
from .input_injector import FocusTarget
from .service_protocol import decode_service_message, encode_service_command
from .session_manager import Mode, WorkerSession, WorkerSessionState


class ServiceSessionManager:
    """Controller-side manager for the external Python service process."""

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
        self._worker_started_once = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def ensure_worker(self) -> WorkerSession:
        if self._session is not None and self._session.process.returncode is None:
            if self._session.process_ready:
                return self._session
            # 进程存活但未就绪，检查是否处于 STARTING 状态（正在等待就绪）
            if self._session.state == WorkerSessionState.STARTING:
                # 复用现有的等待逻辑，等待其就绪
                await self._wait_for_ready()
                return self._session
        if self._session is not None and self._session.process.returncode is not None:
            await self._dispose_service()

        process = await self._spawn_service()
        self._next_session_id += 1
        session = WorkerSession(
            session_id=self._next_session_id,
            process=process,
            state=WorkerSessionState.STARTING,
        )
        session.stdout_task = asyncio.create_task(self._read_service_stdout(process.stdout, session))
        session.stderr_task = asyncio.create_task(self._read_service_stderr(process.stderr))
        session.wait_task = asyncio.create_task(self._wait_service(process, session.session_id))
        self._session = session

        self._loop = asyncio.get_running_loop()
        timeout_s = self.config.worker_ready_timeout_seconds(cold_start=not self._worker_started_once)
        started_at = self._loop.time()
        deadline = started_at + timeout_s
        while self._loop.time() < deadline:
            if session.process_ready:
                session.transition_to(WorkerSessionState.READY)
                self._worker_started_once = True
                self.logger.info("service_ready session_id=%s pid=%s", session.session_id, process.pid)
                return session
            if session.process.returncode is not None:
                break
            await asyncio.sleep(0.02)

        await self._terminate_session_process(session)
        await self._dispose_service()
        self.logger.warning(
            "service_wait_ready_timeout session_id=%s waited_ms=%s",
            session.session_id,
            int((self._loop.time() - started_at) * 1000),
        )
        raise RuntimeError("service process did not become ready")


    async def _wait_for_ready(self) -> None:
        """等待 Service 进程就绪。复用现有的等待逻辑。"""
        session = self._session
        if session is None:
            return

        timeout_s = self.config.worker_ready_timeout_seconds(cold_start=not self._worker_started_once)
        self.logger.info(
            "service_wait_ready session_id=%s start_kind=%s timeout_ms=%s",
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
                self.logger.info("service_ready session_id=%s pid=%s", session.session_id, session.process.pid)
                return
            if session.process.returncode is not None:
                break
            await asyncio.sleep(0.02)

        await self._terminate_session_process(session)
        await self._dispose_service()
        self.logger.warning(
            "service_wait_ready_timeout session_id=%s waited_ms=%s",
            session.session_id,
            int((self._loop.time() - started_at) * 1000),
        )
        raise RuntimeError("service process did not become ready")

    async def terminate_worker(self) -> None:
        if self._session is None:
            return
        self._session.transition_to(WorkerSessionState.TERMINATING)
        await self._terminate_session_process(self._session)
        await self._dispose_service()

    async def restart_worker(self) -> None:
        await self.terminate_worker()
        if not self._stopping:
            await self.ensure_worker()

    def begin_session(self, target: FocusTarget | None, mode: Mode) -> None:
        if self._session is None:
            raise RuntimeError("service process not ready")
        self._session.begin(target, mode)

    def clear_session(self) -> None:
        if self._session is None:
            return
        self._session.clear_active()

    async def send_command(self, command: str) -> None:
        if self._session is None or self._session.process.stdin is None:
            raise RuntimeError("service process is not running")
        session_id = str(self._session.session_id)
        raw = encode_service_command(command.lower(), session_id=session_id)
        self._session.process.stdin.write(f"{raw}\n".encode("utf-8"))
        await self._session.process.stdin.drain()
        self.logger.info("service_command_sent command=%s session_id=%s", command, session_id)

    async def send_stop(self) -> None:
        if self._session is None:
            return
        await self.send_command("STOP")
        self._session.mark_stop_sent()

    def is_active(self) -> bool:
        return self._session is not None and self._session.state == WorkerSessionState.STREAMING

    def is_ready(self) -> bool:
        return self._session is not None and self._session.state == WorkerSessionState.READY

    def is_streaming(self) -> bool:
        return self._session is not None and self._session.state == WorkerSessionState.STREAMING

    def get_session(self) -> WorkerSession | None:
        return self._session

    def get_state(self) -> WorkerSessionState:
        if self._session is None:
            return WorkerSessionState.IDLE
        return self._session.state

    def stop(self) -> None:
        self._stopping = True

    def is_stopping(self) -> bool:
        return self._stopping

    async def _spawn_service(self) -> asyncio.subprocess.Process:
        command = self._build_service_command()
        self.logger.info("service_spawn cmd=%s", command)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.cwd()),
            env=self._build_service_env(),
        )
        self.logger.info("service_spawned pid=%s", process.pid)
        return process

    def _build_service_command(self) -> list[str]:
        args = [
            "--service",
            "--service-transport",
            "stdio",
        ]
        if getattr(sys, "frozen", False):
            return [sys.executable, *args]
        return [sys.executable, "-m", "doubaoime_asr.agent.stable_main", *args]

    def _build_service_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    async def _read_service_stdout(
        self,
        stream: asyncio.StreamReader | None,
        session: WorkerSession,
    ) -> None:
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
                message = decode_service_message(raw)
            except Exception:
                self.logger.error("service_stdout_invalid=%s", raw)
                continue
            event = self._map_service_message_to_event(message, session)
            if event is not None and self._on_event is not None:
                self._on_event(event)

    async def _read_service_stderr(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            self.logger.error("service_stderr=%s", line.decode("utf-8", errors="replace").rstrip())

    def _map_service_message_to_event(self, message: Any, session: WorkerSession) -> VoiceInputEvent | None:
        if message.kind != "event":
            return None
        payload = message.payload or {}
        if message.name == "service_ready":
            session.process_ready = True
            session.transition_to(WorkerSessionState.READY)
            return WorkerStatusEvent(message="service ready")
        if message.name == "interim":
            return InterimResultEvent(
                text=str(payload.get("text", "")),
                segment_index=self._coerce_segment_index(payload.get("segment_index")),
            )
        if message.name == "final_raw":
            return FinalResultEvent(
                text=str(payload.get("text", "")),
                segment_index=self._coerce_segment_index(payload.get("segment_index")),
            )
        if message.name == "final_resolved":
            return ServiceResolvedFinalEvent(
                text=str(payload.get("text", "")),
                raw_text=str(payload.get("raw_text", "")),
                applied_mode=str(payload.get("applied_mode", "")),
                fallback_reason=(
                    str(payload.get("fallback_reason"))
                    if payload.get("fallback_reason") is not None
                    else None
                ),
                committed_source=str(payload.get("committed_source", "")),
            )
        if message.name == "fallback_required":
            return FallbackRequiredEvent(
                reason=str(payload.get("reason", "")),
                source=str(payload.get("source", "")),
            )
        if message.name == "status":
            code = payload.get("code")
            if code == "worker_finished":
                if session.stop_sent_at is not None:
                    session.finished_at = time.perf_counter()
                session.mark_finished()
                return FinishedEvent()
            return WorkerStatusEvent(message=str(payload.get("message", "")))
        if message.name == "error":
            return ErrorEvent(message=str(payload.get("message", "service error")))
        return None

    async def _wait_service(self, process: asyncio.subprocess.Process, session_id: int) -> None:
        code = await process.wait()
        self.handle_worker_exit(session_id, code)
        if self._on_event:
            self._on_event(WorkerExitEvent(session_id=session_id, exit_code=code))

    def handle_worker_exit(self, session_id: int, exit_code: int) -> None:
        if self._session is None or self._session.session_id != session_id:
            self.logger.info(
                "service_exit_ignored session_id=%s current_session_id=%s code=%s",
                session_id,
                self._session.session_id if self._session is not None else None,
                exit_code,
            )
            return
        self.logger.info("service_exit session_id=%s code=%s", session_id, exit_code)
        self._session.transition_to(WorkerSessionState.IDLE)

    async def _terminate_session_process(self, session: WorkerSession) -> None:
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

    async def _dispose_service(self) -> None:
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

    @staticmethod
    def _coerce_segment_index(raw_value: Any) -> int | None:
        if raw_value is None:
            return None
        if isinstance(raw_value, int):
            return raw_value
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return None
