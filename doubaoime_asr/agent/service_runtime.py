from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import AgentConfig
from .service_protocol import ServiceMessage
from .service_session_runner import ServiceSessionRunner


@dataclass(slots=True)
class ServiceRuntimeState:
    active_session_id: str | None = None
    requested_timeout_ms: int | None = None
    last_command: str | None = None


class ServiceRuntime:
    """Service skeleton runtime state machine.

    Keeps command/session semantics separate from transport so Phase 1 can
    evolve from stdio skeleton to real pipe transport without rewriting the
    command lifecycle rules again.
    """

    def __init__(
        self,
        *,
        config: AgentConfig | None = None,
        logger: Any = None,
        emit_events: Any = None,
        session_runner: ServiceSessionRunner | None = None,
    ) -> None:
        self.state = ServiceRuntimeState()
        self._emit_events = emit_events
        self.session_runner = session_runner or ServiceSessionRunner(
            config=config,
            logger=logger,
            emit_events=self._emit_session_runner_events,
        )
        self.session_runner._emit_events = self._emit_session_runner_events  # type: ignore[attr-defined]

    def service_ready_payload(self) -> dict[str, Any]:
        return {
            "protocol_version": 1,
            "skeleton": getattr(self.session_runner, "_worker_session", None) is None,
            "message": "service ready",
        }

    async def handle_command(self, message: ServiceMessage) -> tuple[bool, list[dict[str, Any]]]:
        payload = message.payload or {}
        session_id = message.session_id
        self.state.last_command = message.name

        if message.name == "ping":
            should_exit = False
            events = [
                {
                    "type": "pong",
                    "session_id": session_id,
                    "active_session_id": self.session_runner.state.active_session_id,
                    "skeleton": getattr(self.session_runner, "_worker_session", None) is None,
                }
            ]
        elif message.name == "start":
            should_exit = False
            events = await self.session_runner.start(
                session_id=session_id,
                requested_timeout_ms=payload.get("timeout_ms"),
            )
        elif message.name in {"stop", "cancel"}:
            should_exit = False
            events = await self.session_runner.finish(message.name, session_id=session_id)
        elif message.name == "exit":
            should_exit, events = await self.session_runner.exit(requested_by=session_id)
        else:
            should_exit = False
            events = [self._error(f"unsupported command: {message.name}", session_id=session_id)]

        self._sync_state()
        return should_exit, events

    def _sync_state(self) -> None:
        self.state.active_session_id = self.session_runner.state.active_session_id
        self.state.requested_timeout_ms = self.session_runner.state.requested_timeout_ms

    def _emit_session_runner_events(self, events: list[dict[str, Any]]) -> None:
        self._sync_state()
        if self._emit_events is not None:
            self._emit_events(events)

    @staticmethod
    def _error(message: str, *, session_id: str | None) -> dict[str, Any]:
        return {
            "type": "error",
            "session_id": session_id,
            "message": message,
        }
