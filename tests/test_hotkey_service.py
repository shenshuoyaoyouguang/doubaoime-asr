"""HotkeyService 单元测试。"""
from __future__ import annotations

import logging
import threading
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

from doubaoime_asr.agent.hotkey_service import HotkeyService


@pytest.fixture
def logger() -> logging.Logger:
    """创建测试用日志记录器。"""
    return logging.getLogger("test_hotkey_service")


@pytest.fixture
def hotkey_service(logger: logging.Logger) -> HotkeyService:
    """创建 HotkeyService 实例。"""
    return HotkeyService(logger)


@pytest.fixture
def mock_hook_class() -> Generator[MagicMock, None, None]:
    """Mock GlobalHotkeyHook 类。"""
    with patch("doubaoime_asr.agent.hotkey_service.GlobalHotkeyHook") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.start = MagicMock()
        mock_instance.stop = MagicMock()
        mock_cls.return_value = mock_instance
        yield mock_cls


class TestHotkeyServiceInit:
    """测试初始化。"""

    def test_init_sets_logger(self, logger: logging.Logger) -> None:
        """初始化应正确设置日志记录器。"""
        service = HotkeyService(logger)
        assert service._logger is logger

    def test_init_default_state(self, hotkey_service: HotkeyService) -> None:
        """初始化后应为默认状态。"""
        assert hotkey_service._hook is None
        assert hotkey_service._vk == 0
        assert hotkey_service._on_press_callback is None
        assert hotkey_service._on_release_callback is None


class TestHotkeyServiceCallbacks:
    """测试回调注册。"""

    def test_on_press_registers_callback(self, hotkey_service: HotkeyService) -> None:
        """on_press 应正确注册回调。"""
        callback = MagicMock()
        hotkey_service.on_press(callback)
        assert hotkey_service._on_press_callback is callback

    def test_on_release_registers_callback(self, hotkey_service: HotkeyService) -> None:
        """on_release 应正确注册回调。"""
        callback = MagicMock()
        hotkey_service.on_release(callback)
        assert hotkey_service._on_release_callback is callback

    def test_on_press_replaces_callback(self, hotkey_service: HotkeyService) -> None:
        """多次调用 on_press 应替换之前的回调。"""
        callback1 = MagicMock()
        callback2 = MagicMock()
        hotkey_service.on_press(callback1)
        hotkey_service.on_press(callback2)
        assert hotkey_service._on_press_callback is callback2

    def test_on_release_replaces_callback(self, hotkey_service: HotkeyService) -> None:
        """多次调用 on_release 应替换之前的回调。"""
        callback1 = MagicMock()
        callback2 = MagicMock()
        hotkey_service.on_release(callback1)
        hotkey_service.on_release(callback2)
        assert hotkey_service._on_release_callback is callback2


class TestHotkeyServiceLifecycle:
    """测试生命周期管理。"""

    def test_start_creates_hook(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """start 应创建并启动 GlobalHotkeyHook。"""
        hotkey_service.start(0xA3)  # Right Ctrl

        mock_hook_class.assert_called_once()
        call_kwargs = mock_hook_class.call_args[1]
        assert call_kwargs["on_press"] == hotkey_service._handle_press
        assert call_kwargs["on_release"] == hotkey_service._handle_release

        mock_instance = mock_hook_class.return_value
        mock_instance.start.assert_called_once()

    def test_start_sets_vk(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """start 应设置 VK 码。"""
        hotkey_service.start(0x77)  # F8

        assert hotkey_service._vk == 0x77

    def test_start_stops_existing_hook(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """如果已在运行，start 应先停止现有钩子。"""
        mock_instance = mock_hook_class.return_value

        hotkey_service.start(0xA3)
        assert mock_instance.start.call_count == 1

        hotkey_service.start(0x77)

        # 应该停止旧的钩子
        mock_instance.stop.assert_called_once()
        # 应该创建新的钩子（总共 2 次）
        assert mock_hook_class.call_count == 2

    def test_stop_clears_hook(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """stop 应清除钩子引用。"""
        hotkey_service.start(0xA3)
        assert hotkey_service._hook is not None

        hotkey_service.stop()

        assert hotkey_service._hook is None

    def test_stop_calls_hook_stop(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """stop 应调用钩子的 stop 方法。"""
        mock_instance = mock_hook_class.return_value
        hotkey_service.start(0xA3)
        hotkey_service.stop()

        mock_instance.stop.assert_called_once()

    def test_stop_without_start_is_safe(
        self,
        hotkey_service: HotkeyService,
    ) -> None:
        """未启动时调用 stop 应安全。"""
        # 不应抛出异常
        hotkey_service.stop()


class TestHotkeyServiceUpdateHotkey:
    """测试热键更新。"""

    def test_update_hotkey_while_running(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """运行中更新热键应重启钩子。"""
        mock_instance = mock_hook_class.return_value

        hotkey_service.start(0xA3)
        assert hotkey_service._vk == 0xA3

        hotkey_service.update_hotkey(0x77)  # F8

        # 应停止旧钩子
        mock_instance.stop.assert_called_once()
        # 应创建新钩子（总共 2 次：start + update_hotkey）
        assert mock_hook_class.call_count == 2
        # VK 应更新
        assert hotkey_service._vk == 0x77

    def test_update_hotkey_same_vk_noop(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """更新为相同 VK 应无操作。"""
        mock_instance = mock_hook_class.return_value

        hotkey_service.start(0xA3)
        initial_call_count = mock_hook_class.call_count

        hotkey_service.update_hotkey(0xA3)  # 相同 VK

        # 不应再调用
        assert mock_hook_class.call_count == initial_call_count
        mock_instance.stop.assert_not_called()

    def test_update_hotkey_while_stopped(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """未运行时更新热键应无操作。"""
        hotkey_service.update_hotkey(0x77)

        # 不应创建钩子
        mock_hook_class.assert_not_called()
        assert hotkey_service._vk == 0


class TestHotkeyServiceStateQuery:
    """测试状态查询。"""

    def test_is_running_false_initially(
        self,
        hotkey_service: HotkeyService,
    ) -> None:
        """初始状态 is_running 应为 False。"""
        assert hotkey_service.is_running() is False

    def test_is_running_true_after_start(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """启动后 is_running 应为 True。"""
        hotkey_service.start(0xA3)
        assert hotkey_service.is_running() is True

    def test_is_running_false_after_stop(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """停止后 is_running 应为 False。"""
        hotkey_service.start(0xA3)
        hotkey_service.stop()
        assert hotkey_service.is_running() is False

    def test_current_hotkey_vk_initially_zero(
        self,
        hotkey_service: HotkeyService,
    ) -> None:
        """初始状态 current_hotkey_vk 应为 0。"""
        assert hotkey_service.current_hotkey_vk() == 0

    def test_current_hotkey_vk_after_start(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """启动后 current_hotkey_vk 应返回设置的 VK。"""
        hotkey_service.start(0xA3)
        assert hotkey_service.current_hotkey_vk() == 0xA3

    def test_current_hotkey_vk_after_update(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """更新后 current_hotkey_vk 应返回新的 VK。"""
        hotkey_service.start(0xA3)
        hotkey_service.update_hotkey(0x77)
        assert hotkey_service.current_hotkey_vk() == 0x77

    def test_current_hotkey_display_initially_empty(
        self,
        hotkey_service: HotkeyService,
    ) -> None:
        """初始状态 current_hotkey_display 应为空字符串。"""
        assert hotkey_service.current_hotkey_display() == ""

    def test_current_hotkey_display_right_ctrl(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """Right Ctrl 应显示为 'RIGHT CTRL'。"""
        hotkey_service.start(0xA3)
        assert hotkey_service.current_hotkey_display() == "RIGHT CTRL"

    def test_current_hotkey_display_f8(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """F8 应显示为 'F8'。"""
        hotkey_service.start(0x77)
        assert hotkey_service.current_hotkey_display() == "F8"


class TestHotkeyServiceStartWithHotkey:
    """测试 start_with_hotkey 方法。"""

    def test_start_with_hotkey_right_ctrl(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """使用 'right_ctrl' 启动应设置正确的 VK。"""
        hotkey_service.start_with_hotkey("right_ctrl")
        assert hotkey_service._vk == 0xA3

    def test_start_with_hotkey_f8(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """使用 'f8' 启动应设置正确的 VK。"""
        hotkey_service.start_with_hotkey("f8")
        assert hotkey_service._vk == 0x77

    def test_start_with_hotkey_space(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """使用 'space' 启动应设置正确的 VK。"""
        hotkey_service.start_with_hotkey("space")
        assert hotkey_service._vk == 0x20

    def test_start_with_hotkey_invalid_raises(
        self,
        hotkey_service: HotkeyService,
    ) -> None:
        """使用无效热键名称应抛出 ValueError。"""
        with pytest.raises(ValueError):
            hotkey_service.start_with_hotkey("invalid_key")


class TestHotkeyServiceHandlePress:
    """测试内部热键按下处理。"""

    def test_handle_press_calls_callback(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """_handle_press 应调用已注册的回调。"""
        callback = MagicMock()
        hotkey_service.on_press(callback)
        hotkey_service.start(0xA3)

        hotkey_service._handle_press()

        callback.assert_called_once()

    def test_handle_press_no_callback_safe(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """_handle_press 无回调时应安全。"""
        hotkey_service.start(0xA3)
        # 不应抛出异常
        hotkey_service._handle_press()

    def test_handle_press_catches_callback_exception(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """_handle_press 应捕获回调异常。"""
        callback = MagicMock(side_effect=RuntimeError("test error"))
        hotkey_service.on_press(callback)
        hotkey_service.start(0xA3)

        # 不应抛出异常
        hotkey_service._handle_press()
        callback.assert_called_once()


class TestHotkeyServiceHandleRelease:
    """测试内部热键释放处理。"""

    def test_handle_release_calls_callback(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """_handle_release 应调用已注册的回调。"""
        callback = MagicMock()
        hotkey_service.on_release(callback)
        hotkey_service.start(0xA3)

        hotkey_service._handle_release()

        callback.assert_called_once()

    def test_handle_release_no_callback_safe(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """_handle_release 无回调时应安全。"""
        hotkey_service.start(0xA3)
        # 不应抛出异常
        hotkey_service._handle_release()

    def test_handle_release_catches_callback_exception(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """_handle_release 应捕获回调异常。"""
        callback = MagicMock(side_effect=RuntimeError("test error"))
        hotkey_service.on_release(callback)
        hotkey_service.start(0xA3)

        # 不应抛出异常
        hotkey_service._handle_release()
        callback.assert_called_once()


class TestHotkeyServiceIntegration:
    """集成测试。"""

    def test_full_lifecycle(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """测试完整生命周期。"""
        press_callback = MagicMock()
        release_callback = MagicMock()

        hotkey_service.on_press(press_callback)
        hotkey_service.on_release(release_callback)

        # 启动
        hotkey_service.start_with_hotkey("right_ctrl")
        assert hotkey_service.is_running() is True
        assert hotkey_service.current_hotkey_vk() == 0xA3
        assert hotkey_service.current_hotkey_display() == "RIGHT CTRL"

        # 更新热键
        hotkey_service.update_hotkey(0x77)
        assert hotkey_service.current_hotkey_vk() == 0x77
        assert hotkey_service.current_hotkey_display() == "F8"

        # 停止
        hotkey_service.stop()
        assert hotkey_service.is_running() is False

    def test_callback_invocation_from_hook(
        self,
        hotkey_service: HotkeyService,
        mock_hook_class: MagicMock,
    ) -> None:
        """测试从 GlobalHotkeyHook 触发回调。"""
        press_callback = MagicMock()
        release_callback = MagicMock()
        hotkey_service.on_press(press_callback)
        hotkey_service.on_release(release_callback)
        hotkey_service.start(0xA3)

        # 获取传给 GlobalHotkeyHook 的回调
        call_kwargs = mock_hook_class.call_args[1]
        hook_on_press = call_kwargs["on_press"]
        hook_on_release = call_kwargs["on_release"]

        # 模拟钩子触发
        hook_on_press()
        press_callback.assert_called_once()

        hook_on_release()
        release_callback.assert_called_once()