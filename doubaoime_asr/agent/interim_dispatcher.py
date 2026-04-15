"""
中间结果防抖调度器。

提供防抖功能的中间结果处理,用于在渲染前延迟显示中间文本。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .config import INTERIM_DEBOUNCE_MS_DEFAULT

if TYPE_CHECKING:
    from collections.abc import Awaitable

DebouncedCallback = Callable[[str], Awaitable[None]]


class DebouncedInterimDispatcher:
    """中间结果防抖调度器。

    用于在渲染中间文本前进行防抖处理,避免频繁更新 UI。
    """

    __slots__ = (
        "_debounce_ms",
        "_logger",
        "_on_flush",
        "_pending_text",
        "_pending_task",
    )

    def __init__(
        self,
        debounce_ms: int = INTERIM_DEBOUNCE_MS_DEFAULT,
        logger: logging.Logger | None = None,
        on_flush: DebouncedCallback | None = None,
    ):
        """初始化防抖调度器。

        Args:
            debounce_ms: 防抖延迟时间(毫秒)
            logger: 日志记录器
            on_flush: 刷新回调函数
        """
        self._debounce_ms = debounce_ms
        self._logger = logger or logging.getLogger(__name__)
        self._on_flush = on_flush
        self._pending_text: str | None = None
        self._pending_task: asyncio.Task | None = None

    async def submit(self, text: str) -> None:
        """提交中间文本进行防抖处理。

        Args:
            text: 中间文本内容
        """
        self._pending_text = text
        if self._pending_task is not None and not self._pending_task.done():
            self._pending_task.cancel()
            with asyncio.CancelledError:
                pass
        self._pending_task = asyncio.create_task(self._run_debounce())

    async def _run_debounce(self) -> None:
        """执行防抖等待。"""
        try:
            await asyncio.sleep(self._debounce_ms / 1000.0)
            if self._on_flush is not None and self._pending_text is not None:
                await self._on_flush(self._pending_text)
        except asyncio.CancelledError:
            pass

    async def flush(self) -> None:
        """立即刷新待处理文本。"""
        if self._pending_task is not None and not self._pending_task.done():
            self._pending_task.cancel()
            with asyncio.CancelledError:
                pass
            self._pending_task = None
        if self._on_flush is not None and self._pending_text is not None:
            await self._on_flush(self._pending_text)
            self._pending_text = None

    async def close(self) -> None:
        """关闭调度器并取消待处理任务。"""
        if self._pending_task is not None and not self._pending_task.done():
            self._pending_task.cancel()
            with asyncio.CancelledError:
                await self._pending_task
        self._pending_task = None
        self._pending_text = None


__all__ = [
    "DebouncedInterimDispatcher",
    "INTERIM_DEBOUNCE_MS_DEFAULT",
]