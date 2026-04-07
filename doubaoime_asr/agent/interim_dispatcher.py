from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable


class DebouncedInterimDispatcher:
    """对 interim 文本做统一防抖，再把同一份 snapshot 派发给下游。"""

    def __init__(
        self,
        *,
        debounce_ms: int,
        logger: logging.Logger,
        on_flush: Callable[[int, str], Awaitable[None]],
    ) -> None:
        self._debounce_s = max(0, debounce_ms) / 1000.0
        self._logger = logger
        self._on_flush = on_flush
        self._pending: tuple[int, str] | None = None
        self._task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None
        self._seq = 0

    def _raise_if_failed(self) -> None:
        if self._error is not None:
            raise RuntimeError("interim dispatcher failed") from self._error

    async def submit(self, text: str) -> int:
        self._raise_if_failed()
        self._seq += 1
        seq = self._seq
        self._pending = (seq, text)
        if self._debounce_s == 0:
            await self.flush(reason="no_debounce")
            return seq
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(
            self._sleep_then_flush(expected_seq=seq),
            name="doubao-interim-dispatch",
        )
        return seq

    async def flush(self, *, reason: str) -> tuple[int, str] | None:
        self._raise_if_failed()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        snapshot = self._pending
        self._pending = None
        if snapshot is None:
            return None
        seq, text = snapshot
        await self._flush_snapshot(seq, text)
        self._logger.info("interim_dispatcher_flush reason=%s seq=%s", reason, seq)
        return snapshot

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._pending = None

    async def _sleep_then_flush(self, *, expected_seq: int) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._debounce_s)
            snapshot = self._pending
            if snapshot is None:
                return
            seq, text = snapshot
            if seq != expected_seq:
                return
            self._pending = None
            await self._flush_snapshot(seq, text)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._error = exc
            self._logger.exception("interim_dispatcher_failed")
        finally:
            if self._task is current_task:
                self._task = None

    async def _flush_snapshot(self, seq: int, text: str) -> None:
        await self._on_flush(seq, text)
