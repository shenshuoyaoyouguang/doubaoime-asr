from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Protocol

from .config import AgentConfig


class OverlayBackend(Protocol):
    def show(
        self,
        text: str,
        *,
        seq: int = 0,
        kind: str = "interim",
        stable_prefix_utf16_len: int = 0,
    ) -> None: ...

    def hide(self, reason: str = "") -> None: ...
    def configure(self, config: AgentConfig) -> None: ...


@dataclass(slots=True)
class OverlayFrame:
    text: str
    kind: str
    stable_prefix_utf16_len: int


def compute_stable_prefix_utf16_len(previous_text: str, current_text: str) -> int:
    prefix_chars = 0
    for left, right in zip(previous_text, current_text):
        if left != right:
            break
        prefix_chars += 1
    return _utf16_code_units(current_text[:prefix_chars])


def _utf16_code_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class OverlayRenderScheduler:
    def __init__(
        self,
        preview: OverlayBackend,
        *,
        logger: logging.Logger,
        fps: int = 60,
    ) -> None:
        self._preview = preview
        self._logger = logger
        self._fps = max(1, fps)
        self._pending_frame: OverlayFrame | None = None
        self._task: asyncio.Task[None] | None = None
        self._last_flush_at: float | None = None
        self._last_frame: OverlayFrame | None = None
        self._seq = 0

    def configure(self, config: AgentConfig) -> None:
        self._fps = max(1, int(getattr(config, "overlay_render_fps", 60)))

    async def submit_interim(self, text: str) -> None:
        if not text:
            return
        previous_text = self._last_frame.text if self._last_frame is not None else ""
        frame = OverlayFrame(
            text=text,
            kind="interim",
            stable_prefix_utf16_len=compute_stable_prefix_utf16_len(previous_text, text),
        )
        await self._submit(frame)

    async def submit_final(self, text: str, *, kind: str = "final_raw") -> None:
        if not text:
            return
        frame = OverlayFrame(
            text=text,
            kind=kind,
            stable_prefix_utf16_len=_utf16_code_units(text),
        )
        await self._submit(frame)

    async def hide(self, reason: str = "") -> None:
        self._pending_frame = None
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._last_flush_at = None
        self._last_frame = None
        self._preview.hide(reason=reason)

    async def _submit(self, frame: OverlayFrame) -> None:
        if frame == self._last_frame:
            return
        self._pending_frame = frame
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while self._pending_frame is not None:
            frame = self._pending_frame
            now = asyncio.get_running_loop().time()
            if self._last_flush_at is not None:
                interval = 1.0 / self._fps
                delay = max(0.0, interval - (now - self._last_flush_at))
                if delay > 0:
                    await asyncio.sleep(delay)
                    if self._pending_frame is None:
                        return
                    frame = self._pending_frame
            self._pending_frame = None
            if frame == self._last_frame:
                continue
            self._seq += 1
            self._preview.show(
                frame.text,
                seq=self._seq,
                kind=frame.kind,
                stable_prefix_utf16_len=frame.stable_prefix_utf16_len,
            )
            self._last_frame = frame
            self._last_flush_at = asyncio.get_running_loop().time()
            self._logger.info(
                "overlay_flush seq=%s kind=%s stable_prefix_utf16_len=%s text=%s",
                self._seq,
                frame.kind,
                frame.stable_prefix_utf16_len,
                frame.text,
            )
