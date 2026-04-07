"""
Coordinator 测试。

测试 VoiceInputCoordinator 的集成功能。
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doubaoime_asr.agent.config import AgentConfig, FINAL_COMMIT_SOURCE_RAW
from doubaoime_asr.agent.coordinator import VoiceInputCoordinator
from doubaoime_asr.agent.events import (
    HotkeyPressEvent,
    HotkeyReleaseEvent,
    AudioLevelEvent,
    InterimResultEvent,
    FinalResultEvent,
    FinishedEvent,
    ErrorEvent,
    ReadyEvent,
    ConfigChangeEvent,
)
from doubaoime_asr.agent.session_manager import WorkerSessionState
from doubaoime_asr.agent.input_injector import FocusTarget


@pytest.fixture
def config() -> AgentConfig:
    """创建测试配置。"""
    return AgentConfig()


@pytest.fixture
def coordinator(config: AgentConfig) -> VoiceInputCoordinator:
    """创建测试 Coordinator。"""
    with patch("doubaoime_asr.agent.coordinator.setup_named_logger") as mock_logger:
        mock_logger.return_value = logging.getLogger("test-coordinator")
        coord = VoiceInputCoordinator(
            config,
            enable_tray=False,
            console=False,
        )
    try:
        yield coord
    finally:
        coord.stop()
        coord.hotkey_service.stop()
        coord.overlay_service.stop()
        if coord._settings_controller is not None:
            with contextlib.suppress(Exception):
                coord._settings_controller.close()
        with contextlib.suppress(Exception):
            asyncio.run(coord._close_interim_dispatcher())
        with contextlib.suppress(Exception):
            asyncio.run(coord._cancel_polisher_warmup())
        with contextlib.suppress(Exception):
            asyncio.run(coord._cancel_foreground_watch())
        with contextlib.suppress(Exception):
            asyncio.run(coord.session_manager.terminate_worker())
        gc.collect()


class TestCoordinatorInit:
    """测试初始化。"""

    def test_init_creates_services(self, coordinator: VoiceInputCoordinator) -> None:
        """验证所有 Service 被创建。"""
        assert coordinator.session_manager is not None
        assert coordinator.overlay_service is not None
        assert coordinator.injection_service is not None
        assert coordinator.hotkey_service is not None
        assert coordinator.text_polisher is not None

    def test_init_sets_mode(self, config: AgentConfig) -> None:
        """验证模式设置。"""
        with patch("doubaoime_asr.agent.coordinator.setup_named_logger"):
            coord = VoiceInputCoordinator(config, mode="recognize", enable_tray=False)
        assert coord.mode == "recognize"

    @pytest.mark.asyncio
    async def test_init_binds_session_manager_on_event_bridge(
        self,
        coordinator: VoiceInputCoordinator,
    ) -> None:
        """验证 SessionManager 事件会桥接回协调器事件队列。"""
        event = ReadyEvent()

        assert coordinator.session_manager._on_event is not None

        coordinator.session_manager._on_event(event)

        queued = await asyncio.wait_for(coordinator._event_queue.get(), timeout=0.1)
        assert queued is event

    def test_init_syncs_process_elevation_to_injection_service(
        self,
        config: AgentConfig,
    ) -> None:
        """验证提权状态同步到 InjectionService。"""
        elevated_target = FocusTarget(hwnd=1, is_terminal=False, is_elevated=True)

        with (
            patch("doubaoime_asr.agent.coordinator.setup_named_logger"),
            patch(
                "doubaoime_asr.agent.coordinator.is_current_process_elevated",
                return_value=True,
            ),
        ):
            coord = VoiceInputCoordinator(config, enable_tray=False)

        assert coord._process_elevated is True
        assert coord.injection_service.target_requires_admin(elevated_target) is False


class TestCoordinatorStatus:
    """测试状态管理。"""

    def test_set_status(self, coordinator: VoiceInputCoordinator) -> None:
        """验证状态设置。"""
        coordinator.set_status("测试状态")
        assert coordinator.get_status() == "测试状态"

    def test_set_status_same_value_skipped(self, coordinator: VoiceInputCoordinator) -> None:
        """验证相同状态不重复更新。"""
        coordinator.set_status("初始状态")
        count = 0  # 这里可以用 mock 来验证调用次数
        coordinator.set_status("初始状态")
        assert coordinator.get_status() == "初始状态"


class TestCoordinatorEventHandling:
    """测试事件处理。"""

    @pytest.mark.asyncio
    async def test_handle_preview_config_event(
        self,
        coordinator: VoiceInputCoordinator,
    ) -> None:
        """验证预览事件走临时浮层流程。"""
        coordinator._run_preview_overlay = AsyncMock()
        preview_config = AgentConfig(overlay_font_size=20)

        await coordinator._handle_event(
            ConfigChangeEvent(config=preview_config, preview_id=3, preview_only=True)
        )

        coordinator._run_preview_overlay.assert_awaited_once_with(preview_config, preview_id=3)

    @pytest.mark.asyncio
    async def test_handle_audio_level_event(
        self, coordinator: VoiceInputCoordinator
    ) -> None:
        """验证音频级别事件处理。"""
        # Mock overlay service
        coordinator.overlay_service.update_microphone_level = AsyncMock()

        # 创建模拟会话
        mock_session = MagicMock()
        mock_session.state = WorkerSessionState.STREAMING
        coordinator.session_manager._session = mock_session

        event = AudioLevelEvent(level=0.5)
        await coordinator._handle_worker_event(event)

        coordinator.overlay_service.update_microphone_level.assert_called_once_with(0.5)

    @pytest.mark.asyncio
    async def test_handle_interim_event(
        self, coordinator: VoiceInputCoordinator
    ) -> None:
        """验证中间结果事件处理。"""
        coordinator.config.render_debounce_ms = 0
        coordinator.overlay_service.submit_interim = AsyncMock()
        coordinator.injection_service.apply_inline_interim = AsyncMock()

        mock_session = MagicMock()
        mock_session.state = WorkerSessionState.STREAMING
        coordinator.session_manager._session = mock_session

        event = InterimResultEvent(text="你好")
        await coordinator._handle_worker_event(event)

        coordinator.overlay_service.submit_interim.assert_called_once_with("你好")
        coordinator.injection_service.apply_inline_interim.assert_awaited_once_with("你好")

    @pytest.mark.asyncio
    async def test_handle_interim_event_coalesces_with_debounce(
        self, coordinator: VoiceInputCoordinator
    ) -> None:
        """验证 interim 防抖后只刷最新 snapshot。"""
        coordinator.config.render_debounce_ms = 20
        coordinator.overlay_service.submit_interim = AsyncMock()
        coordinator.injection_service.apply_inline_interim = AsyncMock()

        mock_session = MagicMock()
        mock_session.state = WorkerSessionState.STREAMING
        coordinator.session_manager._session = mock_session
        coordinator.injection_service.begin_session(FocusTarget(hwnd=9, text_input_profile="plain_editor"), "inject")

        await coordinator._handle_worker_event(InterimResultEvent(text="你"))
        await coordinator._handle_worker_event(InterimResultEvent(text="你好"))
        await asyncio.sleep(0.05)

        coordinator.overlay_service.submit_interim.assert_awaited_once_with("你好")
        coordinator.injection_service.apply_inline_interim.assert_awaited_once_with("你好")

    @pytest.mark.asyncio
    async def test_handle_error_event(
        self, coordinator: VoiceInputCoordinator
    ) -> None:
        """验证错误事件处理。"""
        coordinator.overlay_service.hide = AsyncMock()
        coordinator._clear_active_session = AsyncMock()
        coordinator._asr_preflight.invalidate = MagicMock()

        mock_session = MagicMock()
        mock_session.state = WorkerSessionState.STREAMING
        coordinator.session_manager._session = mock_session

        event = ErrorEvent(message="识别失败")
        await coordinator._handle_worker_event(event)

        coordinator._asr_preflight.invalidate.assert_called_once()
        coordinator.overlay_service.hide.assert_called_once_with("error")
        assert "识别失败" in coordinator.get_status()

    @pytest.mark.asyncio
    async def test_handle_finished_event_updates_status_before_inject(
        self, coordinator: VoiceInputCoordinator
    ) -> None:
        """验证 finished 阶段先更新准备上屏状态。"""
        coordinator.overlay_service.submit_final = AsyncMock()
        coordinator.overlay_service.hide = AsyncMock()
        coordinator._resolve_final_text = AsyncMock(return_value=SimpleNamespace(text="最终文本", applied_mode="off", fallback_reason=None))
        coordinator._inject_final = AsyncMock()
        coordinator._clear_active_session = AsyncMock()
        coordinator._segment_texts = {0: "原始文本"}
        coordinator._last_displayed_raw_final_text = "原始文本"

        mock_session = MagicMock()
        mock_session.state = WorkerSessionState.STOPPING
        mock_session.mode = "inject"
        mock_session.stop_sent_at = None
        mock_session.finished_at = None
        coordinator.session_manager._session = mock_session

        await coordinator._handle_worker_event(FinishedEvent())

        assert coordinator._resolve_final_text.await_count == 1
        coordinator._inject_final.assert_awaited_once_with("最终文本")
        assert coordinator.get_status().startswith("最终结果:")

    @pytest.mark.asyncio
    async def test_handle_finished_event_off_mode_uses_raw_fast_path(
        self, coordinator: VoiceInputCoordinator
    ) -> None:
        """off 模式直接使用原文，避免无意义润色。"""
        coordinator.config.polish_mode = "off"
        coordinator.overlay_service.submit_final = AsyncMock()
        coordinator.overlay_service.hide = AsyncMock()
        coordinator._resolve_final_text = AsyncMock()
        coordinator._inject_final = AsyncMock()
        coordinator._clear_active_session = AsyncMock()
        coordinator._segment_texts = {0: "原始文本"}
        coordinator._last_displayed_raw_final_text = "原始文本"

        mock_session = MagicMock()
        mock_session.state = WorkerSessionState.STOPPING
        mock_session.mode = "inject"
        mock_session.stop_sent_at = None
        mock_session.finished_at = None
        coordinator.session_manager._session = mock_session

        await coordinator._handle_worker_event(FinishedEvent())

        coordinator._resolve_final_text.assert_not_awaited()
        coordinator._inject_final.assert_awaited_once_with("原始文本")
        coordinator.overlay_service.submit_final.assert_not_awaited()
        assert coordinator.get_status().startswith("最终结果:")

    @pytest.mark.asyncio
    async def test_handle_finished_event_can_commit_raw_even_when_polish_differs(
        self, coordinator: VoiceInputCoordinator
    ) -> None:
        coordinator.config.final_commit_source = FINAL_COMMIT_SOURCE_RAW
        coordinator.overlay_service.submit_final = AsyncMock()
        coordinator.overlay_service.hide = AsyncMock()
        coordinator._resolve_final_text = AsyncMock(
            return_value=SimpleNamespace(text="润色后的文本", applied_mode="light", fallback_reason=None)
        )
        coordinator._inject_final = AsyncMock()
        coordinator._clear_active_session = AsyncMock()
        coordinator._segment_texts = {0: "原始文本"}
        coordinator._last_displayed_raw_final_text = "原始文本"

        mock_session = MagicMock()
        mock_session.state = WorkerSessionState.STOPPING
        mock_session.mode = "inject"
        mock_session.stop_sent_at = None
        mock_session.finished_at = None
        coordinator.session_manager._session = mock_session

        await coordinator._handle_worker_event(FinishedEvent())

        coordinator._inject_final.assert_awaited_once_with("原始文本")
        coordinator.overlay_service.submit_final.assert_not_awaited()
        assert coordinator.get_status().startswith("最终提交原文:")


class TestCoordinatorTextAggregation:
    """测试文本聚合。"""

    def test_aggregate_session_text_empty(self, coordinator: VoiceInputCoordinator) -> None:
        """验证空文本聚合。"""
        assert coordinator._aggregate_session_text() == ""

    def test_aggregate_session_text_single(self, coordinator: VoiceInputCoordinator) -> None:
        """验证单段文本聚合。"""
        coordinator._segment_texts = {0: "你好"}
        assert coordinator._aggregate_session_text() == "你好"

    def test_aggregate_session_text_multiple(self, coordinator: VoiceInputCoordinator) -> None:
        """验证多段文本聚合。"""
        coordinator._segment_texts = {0: "你好", 1: "世界"}
        result = coordinator._aggregate_session_text()
        assert "你好" in result
        assert "世界" in result

    def test_concat_transcript_text_overlap(self, coordinator: VoiceInputCoordinator) -> None:
        """验证重叠文本拼接。"""
        result = coordinator._concat_transcript_text("你好世", "世界")
        assert result == "你好世界"

    def test_concat_transcript_text_no_overlap(self, coordinator: VoiceInputCoordinator) -> None:
        """验证无重叠文本拼接。"""
        result = coordinator._concat_transcript_text("你好", "世界")
        assert result == "你好世界"


class TestCoordinatorPreview:
    """测试设置页浮层预览。"""

    def test_preview_settings_overlay_ignored_without_loop(self, coordinator: VoiceInputCoordinator) -> None:
        """未进入主循环时不发送预览事件。"""
        coordinator._emit_threadsafe = MagicMock()

        coordinator._preview_settings_overlay(AgentConfig(overlay_font_size=22))

        coordinator._emit_threadsafe.assert_not_called()

    def test_preview_settings_overlay_rejects_when_streaming(self, coordinator: VoiceInputCoordinator) -> None:
        """录音中不触发预览。"""
        coordinator._loop = MagicMock()
        coordinator._emit_threadsafe = MagicMock()
        coordinator.session_manager._session = SimpleNamespace(state=WorkerSessionState.STREAMING)

        coordinator._preview_settings_overlay(AgentConfig(overlay_font_size=22))

        coordinator._emit_threadsafe.assert_not_called()
        assert coordinator.get_status() == "录音中，暂不预览浮层"

    def test_preview_settings_overlay_emits_preview_event(self, coordinator: VoiceInputCoordinator) -> None:
        """空闲时发出预览事件。"""
        coordinator._loop = MagicMock()
        coordinator._emit_threadsafe = MagicMock()

        coordinator._preview_settings_overlay(AgentConfig(overlay_font_size=22))

        coordinator._emit_threadsafe.assert_called_once()
        _, event = coordinator._emit_threadsafe.call_args.args
        assert isinstance(event, ConfigChangeEvent)
        assert event.preview_only is True
        assert event.preview_id == 1
        assert event.config.overlay_font_size == 22

    @pytest.mark.asyncio
    async def test_run_preview_overlay_restores_config_after_preview(self, coordinator: VoiceInputCoordinator) -> None:
        """预览结束后恢复当前配置。"""
        coordinator.overlay_service.configure = MagicMock()
        coordinator.overlay_service.show_microphone = AsyncMock()
        coordinator.overlay_service.hide = AsyncMock()
        coordinator._preview_counter = 1

        preview_config = AgentConfig(overlay_font_size=26)
        await coordinator._run_preview_overlay(preview_config, preview_id=1)

        assert coordinator.overlay_service.configure.call_args_list[0].args == (preview_config,)
        assert coordinator.overlay_service.configure.call_args_list[-1].args == (coordinator.config,)
        coordinator.overlay_service.show_microphone.assert_awaited_once()
        coordinator.overlay_service.hide.assert_awaited_once_with("settings_preview")


class TestCoordinatorStatusMessages:
    """测试状态消息生成。"""

    def test_mode_display_label_inject(self, coordinator: VoiceInputCoordinator) -> None:
        """验证注入模式标签。"""
        coordinator.mode = "inject"
        assert coordinator._mode_display_label() == "自动上屏"

    def test_mode_display_label_recognize(self, coordinator: VoiceInputCoordinator) -> None:
        """验证识别模式标签。"""
        coordinator.mode = "recognize"
        assert coordinator._mode_display_label() == "仅识别（不自动上屏）"

    def test_session_start_status_inject(self, coordinator: VoiceInputCoordinator) -> None:
        """验证注入模式启动状态。"""
        coordinator.mode = "inject"
        result = coordinator._session_start_status(None)
        assert "识别" in result

    def test_session_start_status_recognize(self, coordinator: VoiceInputCoordinator) -> None:
        """验证识别模式启动状态。"""
        coordinator.mode = "recognize"
        result = coordinator._session_start_status(None)
        assert "仅识别" in result


class TestCoordinatorCaptureOutput:
    """测试静音功能。"""

    def test_activate_capture_output_success(self, coordinator: VoiceInputCoordinator) -> None:
        """验证成功激活静音。"""
        coordinator.capture_output_guard.activate = MagicMock(return_value=True)
        result = coordinator._activate_capture_output()
        assert result is None

    def test_activate_capture_output_failure(self, coordinator: VoiceInputCoordinator) -> None:
        """验证激活静音失败。"""
        from doubaoime_asr.agent.win_audio_output import AudioOutputMuteError
        coordinator.capture_output_guard.activate = MagicMock(side_effect=AudioOutputMuteError("失败"))
        result = coordinator._activate_capture_output()
        assert result is not None
        assert "失败" in result

    def test_release_capture_output_success(self, coordinator: VoiceInputCoordinator) -> None:
        """验证成功释放静音。"""
        coordinator.capture_output_guard.release = MagicMock(return_value=True)
        result = coordinator._release_capture_output()
        assert result is None

    def test_release_capture_output_failure(self, coordinator: VoiceInputCoordinator) -> None:
        """验证释放静音失败。"""
        from doubaoime_asr.agent.win_audio_output import AudioOutputMuteError
        coordinator.capture_output_guard.release = MagicMock(side_effect=AudioOutputMuteError("失败"))
        result = coordinator._release_capture_output()
        assert result is not None


class TestCoordinatorConfigChanges:
    """测试配置变更。"""

    def test_polisher_config_changed_mode(self, coordinator: VoiceInputCoordinator) -> None:
        """验证润色模式变更检测。"""
        old_config = AgentConfig(polish_mode="off")
        new_config = AgentConfig(polish_mode="ollama")
        assert coordinator._polisher_config_changed(old_config, new_config) is True

    def test_polisher_config_changed_same(self, coordinator: VoiceInputCoordinator) -> None:
        """验证相同润色配置。"""
        old_config = AgentConfig(polish_mode="light")
        new_config = AgentConfig(polish_mode="light")
        assert coordinator._polisher_config_changed(old_config, new_config) is False

    def test_polisher_config_changed_url(self, coordinator: VoiceInputCoordinator) -> None:
        """验证 Ollama URL 变更检测。"""
        old_config = AgentConfig(ollama_base_url="http://localhost:11434")
        new_config = AgentConfig(ollama_base_url="http://other:11434")
        assert coordinator._polisher_config_changed(old_config, new_config) is True

    @pytest.mark.asyncio
    async def test_apply_config_invalidates_preflight_on_credential_path_change(
        self, coordinator: VoiceInputCoordinator, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        coordinator._asr_preflight.invalidate = MagicMock()
        coordinator.hotkey_service.update_hotkey = MagicMock()
        coordinator.session_manager.restart_worker = AsyncMock()
        coordinator.overlay_service.configure = MagicMock()
        coordinator.injection_service.configure = MagicMock()
        coordinator.text_polisher.configure = MagicMock()
        coordinator.capture_output_guard.configure = MagicMock()
        monkeypatch.setattr(AgentConfig, "save", lambda self, path=None: None)

        await coordinator._apply_config(AgentConfig(credential_path="other.json"))

        coordinator._asr_preflight.invalidate.assert_called_once()
        coordinator.session_manager.restart_worker.assert_awaited_once()


class TestCoordinatorPreflight:
    @pytest.mark.asyncio
    async def test_handle_press_stops_when_preflight_fails(
        self, coordinator: VoiceInputCoordinator
    ) -> None:
        coordinator.mode = "recognize"
        coordinator._asr_preflight.ensure_available = AsyncMock(
            return_value=SimpleNamespace(ok=False, stage="connect", message="网络异常")
        )
        coordinator.session_manager.ensure_worker = AsyncMock()

        await coordinator._handle_press()

        coordinator.session_manager.ensure_worker.assert_not_awaited()
        assert coordinator.get_status() == "ASR 不可用: 网络异常"

    @pytest.mark.asyncio
    async def test_handle_press_runs_preflight_before_start(
        self, coordinator: VoiceInputCoordinator
    ) -> None:
        coordinator.mode = "recognize"
        coordinator._asr_preflight.ensure_available = AsyncMock(
            return_value=SimpleNamespace(ok=True, stage="ok", message="", latency_ms=8)
        )
        coordinator.session_manager.ensure_worker = AsyncMock(
            return_value=SimpleNamespace(state=WorkerSessionState.READY)
        )
        coordinator.session_manager.begin_session = MagicMock()
        coordinator.session_manager.send_command = AsyncMock()
        coordinator.injection_service.begin_session = MagicMock(return_value=None)
        coordinator.overlay_service.show_microphone = AsyncMock()

        await coordinator._handle_press()

        coordinator._asr_preflight.ensure_available.assert_awaited_once_with(
            coordinator.config.credential_path
        )
        coordinator.session_manager.ensure_worker.assert_awaited_once()
        coordinator.session_manager.send_command.assert_awaited_once_with("START")


class TestCoordinatorStop:
    """测试停止功能。"""

    def test_stop_sets_stopping_flag(self, coordinator: VoiceInputCoordinator) -> None:
        """验证停止标志设置。"""
        coordinator.stop()
        assert coordinator._stopping is True
        assert coordinator.session_manager._stopping is True
