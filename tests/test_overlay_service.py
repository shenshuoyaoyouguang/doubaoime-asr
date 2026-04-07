"""OverlayService 测试用例。"""

import asyncio
import logging

import pytest

from doubaoime_asr.agent.config import AgentConfig
from doubaoime_asr.agent.overlay_service import OverlayService


class _MockPreview:
    """模拟 OverlayPreview 用于测试。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self._started = False

    def start(self) -> None:
        self._started = True
        self.calls.append(("start", ()))

    def show(
        self,
        text: str,
        *,
        seq: int = 0,
        kind: str = "interim",
        stable_prefix_utf16_len: int = 0,
        show_microphone: bool = False,
        level: float = 0.0,
    ) -> None:
        self.calls.append(("show", (text, seq, kind, stable_prefix_utf16_len, show_microphone, level)))

    def hide(self, reason: str = "") -> None:
        self.calls.append(("hide", (reason,)))

    def configure(self, config: AgentConfig) -> None:
        self.calls.append(("configure", (config,)))

    def stop(self) -> None:
        self._started = False
        self.calls.append(("stop", ()))


@pytest.fixture
def mock_preview(monkeypatch: pytest.MonkeyPatch) -> _MockPreview:
    """创建模拟的 Preview 后端。"""
    preview = _MockPreview()
    monkeypatch.setattr(
        "doubaoime_asr.agent.overlay_service.OverlayPreview",
        lambda logger, config: preview,
    )
    return preview


@pytest.fixture
def logger() -> logging.Logger:
    """创建测试用的 Logger。"""
    return logging.getLogger("test-overlay-service")


@pytest.fixture
def config() -> AgentConfig:
    """创建测试用的配置。"""
    return AgentConfig()


class TestOverlayServiceLifecycle:
    """测试 OverlayService 生命周期。"""

    def test_start_creates_preview_and_scheduler(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试 start 方法创建 Preview 和 Scheduler。"""
        service = OverlayService(logger, config)

        assert not service.is_running()
        service.start()
        assert service.is_running()

        assert ("start", ()) in mock_preview.calls

        service.stop()
        assert not service.is_running()
        assert ("stop", ()) in mock_preview.calls

    def test_stop_is_idempotent(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试 stop 方法可以安全地多次调用。"""
        service = OverlayService(logger, config)

        service.stop()
        service.stop()

        assert ("stop", ()) not in mock_preview.calls

    def test_start_is_idempotent(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试 start 方法不会重复启动。"""
        service = OverlayService(logger, config)

        service.start()
        initial_start_count = sum(1 for call in mock_preview.calls if call[0] == "start")

        service.start()

        final_start_count = sum(1 for call in mock_preview.calls if call[0] == "start")
        assert final_start_count == initial_start_count

        service.stop()

    def test_install_runtime_components_marks_service_running(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """安装运行时组件后应进入运行态。"""
        service = OverlayService(logger, config)
        scheduler = object()

        service.install_runtime_components(mock_preview, scheduler)

        assert service.is_running()
        assert service._preview is mock_preview
        assert service._scheduler is scheduler

    def test_configure_updates_preview(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试 configure 方法更新 Preview 配置。"""
        service = OverlayService(logger, config)
        service.start()

        new_config = AgentConfig(overlay_font_size=20)
        service.configure(new_config)

        configure_calls = [call for call in mock_preview.calls if call[0] == "configure"]
        assert len(configure_calls) >= 1

        service.stop()


class TestOverlayServiceDisplayControl:
    """测试 OverlayService 显示控制方法。"""

    @pytest.mark.asyncio
    async def test_show_microphone_displays_listening(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试 show_microphone 显示监听状态。"""
        service = OverlayService(logger, config)
        service.start()

        await service.show_microphone()
        await asyncio.sleep(0.05)

        show_calls = [call for call in mock_preview.calls if call[0] == "show"]
        assert len(show_calls) >= 1
        assert show_calls[0][1][0] == "正在聆听…"
        assert show_calls[0][1][2] == "listening"
        assert show_calls[0][1][4] is True

        service.stop()

    @pytest.mark.asyncio
    async def test_show_microphone_with_custom_text(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试 show_microphone 使用自定义文本。"""
        service = OverlayService(logger, config)
        service.start()

        await service.show_microphone("自定义文本")
        await asyncio.sleep(0.05)

        show_calls = [call for call in mock_preview.calls if call[0] == "show"]
        assert len(show_calls) >= 1
        assert show_calls[0][1][0] == "自定义文本"

        service.stop()

    @pytest.mark.asyncio
    async def test_stop_microphone_hides_placeholder(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试 stop_microphone 隐藏占位符。"""
        service = OverlayService(logger, config)
        service.start()

        await service.show_microphone()
        await asyncio.sleep(0.05)
        await service.stop_microphone()

        hide_calls = [call for call in mock_preview.calls if call[0] == "hide"]
        assert len(hide_calls) >= 1

        service.stop()

    @pytest.mark.asyncio
    async def test_hide_calls_scheduler_hide(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试 hide 方法调用 Scheduler 的 hide。"""
        service = OverlayService(logger, config)
        service.start()

        await service.submit_interim("测试文本")
        await asyncio.sleep(0.05)
        await service.hide("test_reason")

        hide_calls = [call for call in mock_preview.calls if call[0] == "hide"]
        assert len(hide_calls) >= 1
        assert hide_calls[-1][1][0] == "test_reason"

        service.stop()


class TestOverlayServiceContentUpdate:
    """测试 OverlayService 内容更新方法。"""

    @pytest.mark.asyncio
    async def test_submit_interim_updates_text(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试 submit_interim 更新中间文本。"""
        service = OverlayService(logger, config)
        service.start()

        await service.submit_interim("你好")
        await asyncio.sleep(0.05)

        show_calls = [call for call in mock_preview.calls if call[0] == "show"]
        assert len(show_calls) >= 1
        assert show_calls[-1][1][0] == "你好"
        assert show_calls[-1][1][2] == "interim"

        service.stop()

    @pytest.mark.asyncio
    async def test_submit_final_updates_text(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试 submit_final 更新最终文本。"""
        service = OverlayService(logger, config)
        service.start()

        await service.submit_final("最终结果", kind="final_polished")
        await asyncio.sleep(0.05)

        show_calls = [call for call in mock_preview.calls if call[0] == "show"]
        assert len(show_calls) >= 1
        assert show_calls[-1][1][0] == "最终结果"
        assert show_calls[-1][1][2] == "final_polished"

        service.stop()

    @pytest.mark.asyncio
    async def test_update_microphone_level_updates_display(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试 update_microphone_level 更新音量显示。"""
        service = OverlayService(logger, config)
        service.start()

        await service.show_microphone()
        await asyncio.sleep(0.05)
        await service.update_microphone_level(0.75)
        await asyncio.sleep(0.05)

        show_calls = [call for call in mock_preview.calls if call[0] == "show"]
        assert len(show_calls) >= 1

        last_call = show_calls[-1]
        assert last_call[1][5] == 0.75

        service.stop()

    @pytest.mark.asyncio
    async def test_multiple_interim_updates_coalesced(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试多个中间文本更新会被合并。"""
        service = OverlayService(logger, config)
        service.start()

        config_high_fps = AgentConfig(overlay_render_fps=60)
        service.configure(config_high_fps)

        await service.submit_interim("a")
        await service.submit_interim("ab")
        await service.submit_interim("abc")
        await asyncio.sleep(0.1)

        show_calls = [call for call in mock_preview.calls if call[0] == "show"]
        texts = [call[1][0] for call in show_calls]

        assert "abc" in texts

        service.stop()


class TestOverlayServiceStateQuery:
    """测试 OverlayService 状态查询。"""

    def test_is_running_returns_false_before_start(
        self,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试启动前 is_running 返回 False。"""
        service = OverlayService(logger, config)
        assert not service.is_running()

    def test_is_running_returns_true_after_start(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试启动后 is_running 返回 True。"""
        service = OverlayService(logger, config)
        service.start()
        assert service.is_running()
        service.stop()

    def test_is_running_returns_false_after_stop(
        self,
        mock_preview: _MockPreview,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试停止后 is_running 返回 False。"""
        service = OverlayService(logger, config)
        service.start()
        service.stop()
        assert not service.is_running()


class TestOverlayServiceErrorHandling:
    """测试 OverlayService 错误处理。"""

    @pytest.mark.asyncio
    async def test_methods_safe_when_not_started(
        self,
        logger: logging.Logger,
        config: AgentConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """测试未启动时调用方法是安全的。"""
        caplog.set_level(logging.WARNING)
        service = OverlayService(logger, config)

        await service.show_microphone()
        await service.stop_microphone()
        await service.hide()
        await service.submit_interim("test")
        await service.submit_final("test")
        await service.update_microphone_level(0.5)

        assert "overlay_service_not_started" in caplog.text

    def test_configure_safe_when_not_started(
        self,
        logger: logging.Logger,
        config: AgentConfig,
    ) -> None:
        """测试未启动时调用 configure 是安全的。"""
        service = OverlayService(logger, config)

        service.configure(AgentConfig(overlay_font_size=20))

        assert not service.is_running()
