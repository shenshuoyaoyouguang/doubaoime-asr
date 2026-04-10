from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any, Callable

from .config import AgentConfig, FINAL_COMMIT_SOURCE_RAW
from .service_worker_bridge import translate_worker_event_to_service_events
from .service_worker_session import (
    ServiceWorkerSessionAdapter,
    SessionManagerWorkerSessionAdapter,
)
from .text_polisher import PolishResult, TextPolisher
from .transcript_utils import TranscriptAccumulator


@dataclass(slots=True)
class ServiceSessionRunnerState:
    active_session_id: str | None = None
    requested_timeout_ms: int | None = None
    pending_finish_command: str | None = None
    transcript: TranscriptAccumulator = field(default_factory=TranscriptAccumulator)


class ServiceSessionRunner:
    """Transport-neutral service session lifecycle helper."""

    def __init__(
        self,
        *,
        config: AgentConfig | None = None,
        logger: logging.Logger | None = None,
        emit_events: Callable[[list[dict[str, Any]]], None] | None = None,
        worker_session: ServiceWorkerSessionAdapter | None = None,
    ) -> None:
        self.state = ServiceSessionRunnerState()
        self._config = config
        self._logger = logger
        self._emit_events = emit_events
        self._worker_session = worker_session
        self._text_polisher = TextPolisher(logger, config) if config is not None and logger is not None else None
        self._finalize_task: asyncio.Task[None] | None = None
        if self._worker_session is None and config is not None and logger is not None:
            self._worker_session = SessionManagerWorkerSessionAdapter(config=config, logger=logger)
        if self._worker_session is not None:
            self._worker_session.set_event_callback(self._handle_worker_event)

    async def start(self, *, session_id: str | None, requested_timeout_ms: int | None) -> list[dict[str, Any]]:
        if not session_id:
            return [self._error("start command requires session_id", session_id=session_id)]
        if self.state.active_session_id is not None and self.state.active_session_id != session_id:
            return [self._error(f"service busy with session {self.state.active_session_id}", session_id=session_id)]

        self._cancel_finalize_task()
        self.state.active_session_id = session_id
        self.state.requested_timeout_ms = requested_timeout_ms
        self.state.pending_finish_command = None
        self.state.transcript.reset()

        if self._worker_session is not None:
            try:
                await self._worker_session.ensure_worker()
                self._worker_session.begin_session()
                await self._worker_session.start_session()
            except Exception as exc:
                self._clear_session_state()
                return [self._error(f"worker session start failed: {exc}", session_id=session_id)]

        return [
            {
                "type": "status",
                "session_id": session_id,
                "code": "session_start_accepted",
                "message": (
                    "service connected to worker session"
                    if self._worker_session is not None
                    else "service skeleton accepted start; ASR runtime not wired yet"
                ),
                "skeleton": self._worker_session is None,
                "requested_timeout_ms": requested_timeout_ms,
            }
        ]

    async def finish(self, command_name: str, *, session_id: str | None) -> list[dict[str, Any]]:
        if self.state.active_session_id is None:
            return [self._error(f"{command_name} received without active session", session_id=session_id)]
        if session_id is not None and session_id != self.state.active_session_id:
            return [
                self._error(
                    f"{command_name} session_id mismatch; active session is {self.state.active_session_id}",
                    session_id=session_id,
                )
            ]

        final_session_id = self.state.active_session_id
        if self._worker_session is None:
            self._clear_session_state()
            status_code = "session_stopped" if command_name == "stop" else "session_canceled"
            return [
                {
                    "type": "status",
                    "session_id": final_session_id,
                    "code": status_code,
                    "message": f"service skeleton acknowledged {command_name}",
                    "skeleton": True,
                }
            ]

        if self.state.pending_finish_command is not None:
            return [
                {
                    "type": "status",
                    "session_id": final_session_id,
                    "code": f"session_{self.state.pending_finish_command}_already_requested",
                    "message": f"service already requested {self.state.pending_finish_command}",
                    "skeleton": False,
                }
            ]

        await self._worker_session.stop_session()
        self.state.pending_finish_command = command_name
        status_code = "session_stopped" if command_name == "stop" else "session_canceled"
        return [
            {
                "type": "status",
                "session_id": final_session_id,
                "code": f"{status_code}_requested",
                "message": f"service requested worker {command_name}",
                "skeleton": False,
            }
        ]

    async def exit(self, *, requested_by: str | None) -> tuple[bool, list[dict[str, Any]]]:
        events: list[dict[str, Any]] = []
        if self.state.active_session_id is not None:
            events.append(
                {
                    "type": "status",
                    "session_id": self.state.active_session_id,
                    "code": "session_cancelled_on_exit",
                    "message": "service exiting while session was active",
                    "skeleton": self._worker_session is None,
                }
            )
            if self._worker_session is not None:
                await self._worker_session.terminate_worker()
            self._clear_session_state()
        events.append({"type": "service_exiting", "requested_by": requested_by})
        return True, events

    def _handle_worker_event(self, event_data: dict[str, Any]) -> None:
        session_id = self.state.active_session_id
        if session_id is None:
            return

        event_type = event_data.get("type")
        bridged_event_data = dict(event_data)
        if event_type in {"interim", "final"}:
            text = event_data.get("text", "")
            if isinstance(text, str):
                aggregated_text = self.state.transcript.record_text(
                    text,
                    segment_index=self._coerce_segment_index(event_data.get("segment_index")),
                    is_final=event_type == "final",
                )
                bridged_event_data["text"] = aggregated_text
                if event_type == "final":
                    self.state.transcript.last_displayed_raw_final_text = aggregated_text

        events = translate_worker_event_to_service_events(bridged_event_data, session_id=session_id)
        if event_type == "finished":
            raw_text = self.state.transcript.aggregate_text()
            if raw_text and raw_text != self.state.transcript.last_displayed_raw_final_text:
                events.append({"type": "final_raw", "session_id": session_id, "text": raw_text})
            if raw_text:
                self._cancel_finalize_task()
                finalize_coro = self._emit_resolved_final_and_finish(
                    session_id=session_id,
                    raw_text=raw_text,
                    pending_finish_command=self.state.pending_finish_command,
                )
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(finalize_coro)
                else:
                    self._finalize_task = loop.create_task(finalize_coro)
            else:
                events.extend(self._build_finish_status_events(session_id, self.state.pending_finish_command))
                self._clear_session_state()
        elif event_type == "worker_exit":
            events.append(
                {
                    "type": "status",
                    "session_id": session_id,
                    "code": "session_aborted_worker_exit",
                    "message": "worker exited before session completion",
                    "skeleton": False,
                }
            )
            self._clear_session_state()
        elif event_type == "error":
            events.append(
                {
                    "type": "status",
                    "session_id": session_id,
                    "code": "session_aborted_worker_error",
                    "message": "worker reported an error before session completion",
                    "skeleton": False,
                }
            )
            self._clear_session_state()

        if self._emit_events is not None:
            self._emit_events(events)

    async def _emit_resolved_final_and_finish(
        self,
        *,
        session_id: str,
        raw_text: str,
        pending_finish_command: str | None,
    ) -> None:
        try:
            result = await self._resolve_final_text(raw_text)
            committed_text, committed_source = self._resolve_committed_text(raw_text, result)
            events: list[dict[str, Any]] = [
                {
                    "type": "final_resolved",
                    "session_id": session_id,
                    "text": committed_text,
                    "raw_text": raw_text,
                    "applied_mode": result.applied_mode,
                    "fallback_reason": result.fallback_reason,
                    "committed_source": committed_source,
                },
                {
                    "type": "status",
                    "session_id": session_id,
                    "code": "worker_finished",
                    "message": "worker session finished",
                    "source": "worker",
                },
            ]
            if result.fallback_reason:
                events.append(
                    {
                        "type": "fallback_required",
                        "session_id": session_id,
                        "reason": result.fallback_reason,
                        "source": "text_polisher",
                    }
                )
            events.extend(
                self._build_finish_status_events(
                    session_id,
                    pending_finish_command,
                    include_worker_finished=False,
                )
            )
            self._clear_session_state(clear_finalize_task=False)
            if self._emit_events is not None:
                self._emit_events(events)
        finally:
            if self.state.active_session_id == session_id:
                self._clear_session_state(clear_finalize_task=False)

    async def _resolve_final_text(self, raw_text: str) -> PolishResult:
        if self._text_polisher is None:
            return PolishResult(text=raw_text, applied_mode="off", latency_ms=0)
        return await self._text_polisher.polish(raw_text)

    def _resolve_committed_text(self, raw_text: str, result: PolishResult) -> tuple[str, str]:
        if self._config is not None and self._config.final_commit_source == FINAL_COMMIT_SOURCE_RAW:
            return raw_text, "raw"
        committed_text = result.text or raw_text
        return committed_text, ("raw" if committed_text == raw_text else "polished")

    def _build_finish_status_events(
        self,
        session_id: str,
        pending_finish_command: str | None,
        *,
        include_worker_finished: bool = True,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if include_worker_finished:
            events.append(
                {
                    "type": "status",
                    "session_id": session_id,
                    "code": "worker_finished",
                    "message": "worker session finished",
                    "source": "worker",
                }
            )
        if pending_finish_command in {"stop", "cancel"}:
            status_code = "session_stopped" if pending_finish_command == "stop" else "session_canceled"
            events.append(
                {
                    "type": "status",
                    "session_id": session_id,
                    "code": status_code,
                    "message": f"worker session {pending_finish_command} completed",
                    "skeleton": False,
                }
            )
        return events

    def _cancel_finalize_task(self) -> None:
        if self._finalize_task is not None and not self._finalize_task.done():
            self._finalize_task.cancel()
        self._finalize_task = None

    def _clear_session_state(self, *, clear_finalize_task: bool = True) -> None:
        self.state.active_session_id = None
        self.state.requested_timeout_ms = None
        self.state.pending_finish_command = None
        self.state.transcript.reset()
        if clear_finalize_task:
            self._cancel_finalize_task()

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

    @staticmethod
    def _error(message: str, *, session_id: str | None) -> dict[str, Any]:
        return {
            "type": "error",
            "session_id": session_id,
            "message": message,
        }
