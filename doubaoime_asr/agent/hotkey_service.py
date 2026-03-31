"""热键服务封装。

封装 GlobalHotkeyHook，提供热键监听和回调注册接口。
"""
from __future__ import annotations

import logging
from typing import Callable

from .win_keyboard_hook import GlobalHotkeyHook
from .win_hotkey import vk_from_hotkey, vk_to_display


Logger = logging.Logger | logging.LoggerAdapter


class HotkeyService:
    """封装 GlobalHotkeyHook，提供热键监听和回调注册接口。

    使用示例:
        service = HotkeyService(logger)
        service.on_press(lambda: print("按下"))
        service.on_release(lambda: print("释放"))
        service.start(0xA3)  # Right Ctrl
        # ...
        service.stop()
    """

    def __init__(self, logger: Logger) -> None:
        """初始化热键服务。

        Args:
            logger: 日志记录器，用于记录热键事件。
        """
        self._logger = logger
        self._hook: GlobalHotkeyHook | None = None
        self._vk: int = 0
        self._on_press_callback: Callable[[], None] | None = None
        self._on_release_callback: Callable[[], None] | None = None

    def start(self, vk: int) -> None:
        """启动热键监听。

        如果已经在监听，会先停止再重新启动。

        Args:
            vk: 要监听的虚拟键码。
        """
        if self._hook is not None:
            self.stop()

        self._vk = vk
        display_name = vk_to_display(vk)
        self._hook = GlobalHotkeyHook(
            vk,
            on_press=self._handle_press,
            on_release=self._handle_release,
        )
        self._hook.start()
        self._logger.info(f"热键监听已启动: VK={vk}, 显示名={display_name}")

    def stop(self) -> None:
        """停止热键监听。"""
        if self._hook is not None:
            self._hook.stop()
            self._hook = None
            self._logger.info("热键监听已停止")

    def on_press(self, callback: Callable[[], None]) -> None:
        """注册热键按下回调。

        Args:
            callback: 热键按下时调用的回调函数。
        """
        self._on_press_callback = callback

    def on_release(self, callback: Callable[[], None]) -> None:
        """注册热键释放回调。

        Args:
            callback: 热键释放时调用的回调函数。
        """
        self._on_release_callback = callback

    def update_hotkey(self, vk: int) -> None:
        """动态更新热键配置。

        如果服务正在运行且 VK 不同，会重新启动监听。
        如果 VK 相同，则不做任何操作。

        Args:
            vk: 新的虚拟键码。
        """
        if self._hook is not None and self._vk != vk:
            old_display = vk_to_display(self._vk)
            new_display = vk_to_display(vk)
            self._hook.stop()
            self._vk = vk
            self._hook = GlobalHotkeyHook(
                vk,
                on_press=self._handle_press,
                on_release=self._handle_release,
            )
            self._hook.start()
            self._logger.info(f"热键已更新: {old_display} -> {new_display}")

    def start_with_hotkey(self, hotkey: str) -> None:
        """使用热键名称启动监听。

        Args:
            hotkey: 热键名称，如 "right_ctrl", "f8", "space" 等。

        Raises:
            ValueError: 如果热键名称不被支持。
        """
        vk = vk_from_hotkey(hotkey)
        self.start(vk)

    def is_running(self) -> bool:
        """检查热键监听是否正在运行。

        Returns:
            如果正在监听返回 True，否则返回 False。
        """
        return self._hook is not None

    def current_hotkey_vk(self) -> int:
        """获取当前监听的热键虚拟键码。

        Returns:
            当前虚拟键码，如果未启动则返回 0。
        """
        return self._vk

    def current_hotkey_display(self) -> str:
        """获取当前热键的显示名称。

        Returns:
            热键显示名称，如 "RIGHT CTRL"、"F8" 等。
        """
        if self._vk == 0:
            return ""
        return vk_to_display(self._vk)

    def _handle_press(self) -> None:
        """内部处理热键按下事件。"""
        self._logger.debug(f"热键按下: {self.current_hotkey_display()}")
        if self._on_press_callback is not None:
            try:
                self._on_press_callback()
            except Exception:
                self._logger.exception("热键按下回调执行失败")

    def _handle_release(self) -> None:
        """内部处理热键释放事件。"""
        self._logger.debug(f"热键释放: {self.current_hotkey_display()}")
        if self._on_release_callback is not None:
            try:
                self._on_release_callback()
            except Exception:
                self._logger.exception("热键释放回调执行失败")


__all__ = ["HotkeyService"]