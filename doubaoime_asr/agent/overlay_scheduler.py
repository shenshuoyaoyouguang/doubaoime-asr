from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from .config import AgentConfig


class OverlayBackend(Protocol):
    def show(self, text: str, *, seq: int = 0, kind: str = "interim") -> None: ...
    def hide(self, reason: str = "") -> None: ...
    def configure(self, config: AgentConfig) -> None: ...


class OverlayRenderScheduler:
    def __init__(
        self,
        preview: OverlayBackend,
        *,
        logger: logging.Logger,
        fps: int = 30,
    ) -> None:
        self._preview = preview
        self._logger = logger
        self._fps = max(1, fps)
        self._pending_text: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._last_flush_at: float | None = None
        self._last_text: str | None = None
        self._seq = 0

    def configure(self, config: AgentConfig) -> None:
        self._fps = max(1, int(getattr(config, "overlay_render_fps", 30)))

    async def submit_interim(self, text: str) -> None:
        if not text:
            return
        self._pending_text = text
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain())

    async def hide(self, reason: str = "") -> None:
        self._pending_text = None
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._last_flush_at = None
        self._last_text = None
        self._preview.hide(reason=reason)

    async def _drain(self) -> None:
        while self._pending_text is not None:
            text = self._pending_text
            now = asyncio.get_running_loop().time()
            if self._last_flush_at is not None:
                interval = 1.0 / self._fps
                delay = max(0.0, interval - (now - self._last_flush_at))
                if delay > 0:
                    await asyncio.sleep(delay)
                    if self._pending_text is None:
                        return
                    text = self._pending_text
            self._pending_text = None
            if text == self._last_text:
                continue
            self._seq += 1
            self._preview.show(text, seq=self._seq, kind="interim")
            self._last_text = text
            self._last_flush_at = asyncio.get_running_loop().time()
            self._logger.info("overlay_flush seq=%s text=%s", self._seq, text)
