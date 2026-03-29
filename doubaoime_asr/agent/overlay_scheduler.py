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
        show_microphone: bool = False,
        level: float = 0.0,
    ) -> None: ...

    def hide(self, reason: str = "") -> None: ...
    def configure(self, config: AgentConfig) -> None: ...


@dataclass(slots=True)
class OverlayFrame:
    text: str
    kind: str
    stable_prefix_utf16_len: int
    show_microphone: bool = False
    level: float = 0.0


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
        self._microphone_active = False
        self._pending_frame: OverlayFrame | None = None
        self._task: asyncio.Task[None] | None = None
        self._last_flush_at: float | None = None
        self._last_frame: OverlayFrame | None = None
        self._last_level = 0.0
        self._seq = 0

    def configure(self, config: AgentConfig) -> None:
        self._fps = max(1, int(getattr(config, "overlay_render_fps", 60)))

    def _latest_frame(self) -> OverlayFrame | None:
        return self._pending_frame or self._last_frame

    def _latest_microphone_state(self) -> tuple[bool, float]:
        return self._microphone_active, self._last_level if self._microphone_active else 0.0

    async def submit_interim(self, text: str) -> None:
        if not text:
            return
        previous_text = self._last_frame.text if self._last_frame is not None else ""
        show_microphone, level = self._latest_microphone_state()
        frame = OverlayFrame(
            text=text,
            kind="interim",
            stable_prefix_utf16_len=compute_stable_prefix_utf16_len(previous_text, text),
            show_microphone=show_microphone,
            level=level,
        )
        await self._submit(frame)

    async def submit_final(self, text: str, *, kind: str = "final_raw") -> None:
        if not text:
            return
        show_microphone, level = self._latest_microphone_state()
        frame = OverlayFrame(
            text=text,
            kind=kind,
            stable_prefix_utf16_len=_utf16_code_units(text),
            show_microphone=show_microphone,
            level=level,
        )
        await self._submit(frame)

    async def show_microphone(self, placeholder_text: str = "正在聆听…") -> None:
        """显示统一录音 HUD，文字区与麦克风同时出现。"""
        self._microphone_active = True
        frame = OverlayFrame(
            text=placeholder_text,
            kind="listening",
            stable_prefix_utf16_len=_utf16_code_units(placeholder_text),
            show_microphone=True,
            level=self._last_level,
        )
        await self._submit(frame)

    async def stop_microphone(self) -> None:
        self._microphone_active = False
        base_frame = self._latest_frame()
        if base_frame is None or not base_frame.show_microphone:
            return
        if base_frame.kind == "listening":
            await self.hide("stop_microphone_placeholder")
            return
        await self._submit(
            OverlayFrame(
                text=base_frame.text,
                kind=base_frame.kind,
                stable_prefix_utf16_len=base_frame.stable_prefix_utf16_len,
                show_microphone=False,
                level=0.0,
            )
        )

    async def update_microphone_level(self, level: float) -> None:
        self._last_level = max(0.0, min(1.0, float(level)))
        if not self._microphone_active:
            return
        base_frame = self._pending_frame or self._last_frame
        if base_frame is None or not base_frame.show_microphone:
            return
        await self._submit(
            OverlayFrame(
                text=base_frame.text,
                kind=base_frame.kind,
                stable_prefix_utf16_len=base_frame.stable_prefix_utf16_len,
                show_microphone=True,
                level=self._last_level,
            )
        )

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
        self._last_level = 0.0
        self._microphone_active = False
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
                show_microphone=frame.show_microphone,
                level=frame.level,
            )
            self._last_frame = frame
            self._last_flush_at = asyncio.get_running_loop().time()
            self._logger.info(
                "overlay_flush seq=%s kind=%s stable_prefix_utf16_len=%s show_microphone=%s level=%.3f text=%s",
                self._seq,
                frame.kind,
                frame.stable_prefix_utf16_len,
                frame.show_microphone,
                frame.level,
                frame.text,
            )
