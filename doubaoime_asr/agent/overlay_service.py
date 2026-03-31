"""OverlayService - 统一浮层服务封装。

封装 OverlayPreview 和 OverlayRenderScheduler，提供简化的浮层控制接口。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from .config import AgentConfig
from .overlay_preview import OverlayPreview
from .overlay_scheduler import OverlayRenderScheduler


class OverlayService:
    """统一浮层服务，封装 OverlayPreview 和 OverlayRenderScheduler。

    提供简化的异步接口用于控制浮层显示、隐藏和内容更新。
    """

    def __init__(self, logger: logging.Logger, config: AgentConfig) -> None:
        """初始化 OverlayService。

        Args:
            logger: 日志记录器
            config: 代理配置
        """
        self._logger = logger
        self._config = config
        self._preview: OverlayPreview | None = None
        self._scheduler: OverlayRenderScheduler | None = None
        self._running = False

    def start(self) -> None:
        """启动浮层服务。"""
        if self._running:
            self._logger.warning("overlay_service_already_running")
            return

        self._preview = OverlayPreview(logger=self._logger, config=self._config)
        self._preview.start()
        self._scheduler = OverlayRenderScheduler(
            self._preview,
            logger=self._logger,
            fps=self._config.overlay_render_fps,
        )
        self._running = True
        self._logger.info("overlay_service_started fps=%d", self._config.overlay_render_fps)

    def stop(self) -> None:
        """停止浮层服务。"""
        if not self._running:
            return

        self._running = False
        if self._scheduler is not None:
            self._scheduler = None
        if self._preview is not None:
            try:
                self._preview.stop()
            except Exception:
                self._logger.exception("overlay_preview_stop_failed")
            self._preview = None
        self._logger.info("overlay_service_stopped")

    def configure(self, config: AgentConfig) -> None:
        """更新配置。

        Args:
            config: 新的代理配置
        """
        self._config = config
        if self._preview is not None:
            self._preview.configure(config)
        if self._scheduler is not None:
            self._scheduler.configure(config)

    async def show_microphone(self, text: str = "正在聆听…") -> None:
        """显示麦克风录音状态。

        Args:
            text: 占位文本，默认为"正在聆听…"
        """
        if self._scheduler is None:
            self._logger.warning("overlay_service_not_started show_microphone")
            return
        await self._scheduler.show_microphone(text)

    async def stop_microphone(self) -> None:
        """停止麦克风显示状态。"""
        if self._scheduler is None:
            self._logger.warning("overlay_service_not_started stop_microphone")
            return
        await self._scheduler.stop_microphone()

    async def hide(self, reason: str = "") -> None:
        """隐藏浮层。

        Args:
            reason: 隐藏原因，用于日志记录
        """
        if self._scheduler is None:
            self._logger.warning("overlay_service_not_started hide")
            return
        await self._scheduler.hide(reason)

    async def submit_interim(self, text: str) -> None:
        """提交中间识别结果。

        Args:
            text: 中间识别文本
        """
        if self._scheduler is None:
            self._logger.warning("overlay_service_not_started submit_interim")
            return
        await self._scheduler.submit_interim(text)

    async def submit_final(self, text: str, kind: str = "final_raw") -> None:
        """提交最终识别结果。

        Args:
            text: 最终识别文本
            kind: 结果类型，默认为"final_raw"
        """
        if self._scheduler is None:
            self._logger.warning("overlay_service_not_started submit_final")
            return
        await self._scheduler.submit_final(text, kind=kind)

    async def update_microphone_level(self, level: float) -> None:
        """更新麦克风音量级别。

        Args:
            level: 音量级别，范围 0.0-1.0
        """
        if self._scheduler is None:
            self._logger.warning("overlay_service_not_started update_microphone_level")
            return
        await self._scheduler.update_microphone_level(level)

    def is_running(self) -> bool:
        """检查服务是否正在运行。

        Returns:
            如果服务正在运行返回 True，否则返回 False
        """
        return self._running