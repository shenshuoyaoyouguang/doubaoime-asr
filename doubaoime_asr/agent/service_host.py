from __future__ import annotations

import asyncio
import logging
from typing import Any

from .service_protocol import SERVICE_PROTOCOL_VERSION, ServiceMessage, ServiceProtocolError, decode_service_message
from .service_runtime import ServiceRuntime
from .service_transport import ServiceTransport
from .service_worker_bridge import translate_worker_event_to_service_events


class ServiceHost:
    """Thin host that binds transport, protocol checks, and runtime together."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        transport: ServiceTransport,
        config: Any | None = None,
        runtime: ServiceRuntime | None = None,
    ) -> None:
        self._logger = logger
        self._transport = transport
        self._runtime = runtime or ServiceRuntime(config=config, logger=logger, emit_events=self._emit_runtime_events)
        self._runtime._emit_events = self._emit_runtime_events  # type: ignore[attr-defined]
        self._runtime.session_runner._emit_events = self._runtime._emit_session_runner_events  # type: ignore[attr-defined]

    @property
    def runtime(self) -> ServiceRuntime:
        return self._runtime

    def emit_ready(self) -> None:
        self._transport.emit_event("service_ready", **self._runtime.service_ready_payload())

    async def handle_raw_message(self, raw: str) -> bool:
        try:
            message = decode_service_message(raw)
            self._ensure_supported_message_version(message)
        except ServiceProtocolError as exc:
            self._emit_protocol_error(str(exc))
            return False

        should_exit, events = await self._runtime.handle_command(message)
        self._emit_runtime_events(events)
        return should_exit

    async def run(self, line_queue: asyncio.Queue[str]) -> int:
        self.emit_ready()
        while True:
            raw = await line_queue.get()
            should_exit = await self.handle_raw_message(raw)
            if should_exit:
                return 0

    def emit_worker_event(self, event_data: dict[str, Any], *, session_id: str) -> None:
        self._emit_runtime_events(
            translate_worker_event_to_service_events(event_data, session_id=session_id)
        )

    def _ensure_supported_message_version(self, message: ServiceMessage) -> None:
        if message.version != SERVICE_PROTOCOL_VERSION:
            raise ServiceProtocolError(
                f"unsupported service protocol version {message.version}; expected {SERVICE_PROTOCOL_VERSION}"
            )

    def _emit_protocol_error(self, message: str, *, session_id: str | None = None, **payload: Any) -> None:
        self._logger.warning("service_protocol_error session_id=%s message=%s", session_id, message)
        self._transport.emit_event("error", session_id=session_id, message=message, **payload)

    def _emit_runtime_events(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            event_type = str(event["type"])
            payload = {key: value for key, value in event.items() if key != "type"}
            if event_type == "error":
                message = str(payload.pop("message", "unknown service error"))
                self._emit_protocol_error(
                    message,
                    session_id=payload.pop("session_id", None) if isinstance(payload.get("session_id"), str | type(None)) else None,
                    **payload,
                )
                continue
            self._transport.emit_event(event_type, **payload)
