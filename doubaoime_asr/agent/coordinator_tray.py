"""
Coordinator Tray 模块。

提供系统托盘 UI 功能。
"""
from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

import pystray
from PIL import Image, ImageDraw

if TYPE_CHECKING:
    import asyncio

    from .coordinator import VoiceInputCoordinator

from .events import RestartAsAdminEvent

__all__ = [
    "start_tray",
]


def start_tray(coordinator: "VoiceInputCoordinator", loop: asyncio.AbstractEventLoop) -> None:
    """启动系统托盘。

    Args:
        coordinator: Coordinator 实例,提供配置、控制器和状态访问
        loop: 异步事件循环,用于线程安全调用
    """
    # 本地函数:构建托盘图标
    def build_icon() -> Image.Image:
        """构建托盘图标图像。"""
        image = Image.new("RGBA", (64, 64), (20, 20, 20, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=(38, 110, 255, 255))
        draw.rectangle((26, 18, 38, 42), fill=(255, 255, 255, 255))
        draw.ellipse((22, 12, 42, 28), fill=(255, 255, 255, 255))
        draw.rectangle((22, 44, 42, 48), fill=(255, 255, 255, 255))
        return image

    # 本地函数:打开日志目录
    def open_log_dir(icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
        """打开日志目录。"""
        path = coordinator.config.default_log_dir()
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    # 本地函数:打开设置窗口
    def open_settings(icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
        """打开设置窗口。"""
        if coordinator._settings_controller is not None:
            coordinator._settings_controller.show(coordinator.config)

    # 本地函数:停止应用
    def stop_app(icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
        """停止应用。"""
        loop.call_soon_threadsafe(coordinator.stop)

    # 本地函数:以管理员重启
    def restart_app_as_admin(icon: pystray.Icon | None = None, item: pystray.MenuItem | None = None) -> None:
        """以管理员身份重启应用。"""
        coordinator._emit_threadsafe(loop, RestartAsAdminEvent())

    # 创建托盘图标
    icon = pystray.Icon(
        "doubao-voice-agent",
        build_icon(),
        "Doubao Voice Input",
        menu=pystray.Menu(
            pystray.MenuItem(
                lambda item: f"状态: {coordinator._status}",
                None,
                enabled=False,
            ),
            pystray.MenuItem(
                lambda item: f"模式: {coordinator._mode_display_label()}",
                None,
                enabled=False,
            ),
            pystray.MenuItem(
                lambda item: f"热键: {coordinator.config.effective_hotkey_display()}",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "以管理员重启",
                restart_app_as_admin,
                enabled=lambda item: not coordinator._process_elevated,
            ),
            pystray.MenuItem("设置", open_settings),
            pystray.MenuItem("打开日志目录", open_log_dir),
            pystray.MenuItem("退出", stop_app),
        ),
    )
    coordinator._tray_icon = icon
    coordinator._tray_thread = threading.Thread(target=icon.run, name="doubao-tray", daemon=True)
    coordinator._tray_thread.start()