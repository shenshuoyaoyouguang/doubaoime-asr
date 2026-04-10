from __future__ import annotations

import logging
from typing import Any, Callable, Protocol

from .config import AgentConfig
from .events import VoiceInputEvent
from .session_manager import SessionManager


class ServiceWorkerSessionAdapter(Protocol):
    def set_event_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None: ...
    async def ensure_worker(self) -> None: ...
    def begin_session(self) -> None: ...
    async def start_session(self) -> None: ...
    async def stop_session(self) -> None: ...
    async def terminate_worker(self) -> None: ...


class SessionManagerWorkerSessionAdapter:
    """Thin adapter that reuses the existing worker subprocess lifecycle."""

    def __init__(self, *, config: AgentConfig, logger: logging.Logger) -> None:
        self._callback: Callable[[dict[str, Any]], None] | None = None
        self._manager = SessionManager(
            config=config,
            logger=logger,
            on_event=self._handle_session_manager_event,
        )

    def set_event_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        self._callback = callback

    async def ensure_worker(self) -> None:
        await self._manager.ensure_worker()

    def begin_session(self) -> None:
        self._manager.begin_session(target=None, mode="recognize")

    async def start_session(self) -> None:
        await self._manager.send_command("START")

    async def stop_session(self) -> None:
        await self._manager.send_stop()

    async def terminate_worker(self) -> None:
        await self._manager.terminate_worker()

    def _handle_session_manager_event(self, event: VoiceInputEvent) -> None:
        if self._callback is not None:
            self._callback(event.to_dict())
