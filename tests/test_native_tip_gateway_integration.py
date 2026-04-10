from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from doubaoime_asr.agent.config import AgentConfig
from doubaoime_asr.agent.events import FinalResultEvent, FinishedEvent, InterimResultEvent
from doubaoime_asr.agent.input_injector import FocusTarget
from doubaoime_asr.agent.session_manager import WorkerSessionState
from doubaoime_asr.agent import stable_simple_app
from doubaoime_asr.agent.tip_gateway import NamedPipeTipGateway


class _Dummy:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def configure(self, *args, **kwargs) -> None:
        return None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def activate(self) -> bool:
        return True

    def release(self) -> bool:
        return True


def _native_gateway_host_path() -> Path:
    return Path("build/native_tip/Release/native_tip_gateway_host.exe")


@pytest.fixture
def native_gateway_host() -> Path:
    host_path = _native_gateway_host_path()
    if not host_path.exists():
        subprocess.run(
            [
                "cmake",
                "-S",
                "native_tip",
                "-B",
                "build/native_tip",
            ],
            check=True,
        )
        subprocess.run(
            [
                "cmake",
                "--build",
                "build/native_tip",
                "--config",
                "Release",
                "--target",
                "native_tip_gateway_host",
            ],
            check=True,
        )
    if not host_path.exists():
        pytest.fail("native_tip_gateway_host.exe was not produced")
    return host_path


def test_stable_app_uses_named_pipe_gateway_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stable_simple_app, "setup_named_logger", lambda *args, **kwargs: logging.getLogger("stable-app-tip-gateway"))
    monkeypatch.setattr(stable_simple_app, "TextInjectionManager", _Dummy)
    monkeypatch.setattr(stable_simple_app, "OverlayPreview", _Dummy)
    monkeypatch.setattr(stable_simple_app, "OverlayRenderScheduler", _Dummy)
    monkeypatch.setattr(stable_simple_app, "TextPolisher", _Dummy)
    monkeypatch.setattr(stable_simple_app, "SystemOutputMuteGuard", _Dummy)
    monkeypatch.setattr(stable_simple_app, "is_current_process_elevated", lambda: False)
    monkeypatch.setenv("DOUBAO_TIP_GATEWAY_PIPE_NAME", r"\\.\pipe\doubao-tip-env-test")
    app = stable_simple_app.StableVoiceInputApp(AgentConfig(), enable_tray=False)
    try:
        assert isinstance(app._coordinator._tip_gateway, NamedPipeTipGateway)
        assert app._coordinator._tip_gateway.pipe_name == r"\\.\pipe\doubao-tip-env-test"
    finally:
        app.stop()
        monkeypatch.delenv("DOUBAO_TIP_GATEWAY_PIPE_NAME", raising=False)


@pytest.mark.asyncio
async def test_stable_app_coordinator_can_drive_native_gateway_host(
    native_gateway_host: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe_name = rf"\\.\pipe\doubao-tip-host-{int(time.time() * 1000)}"
    active_context_id = "hwnd:202"
    process = subprocess.Popen(
        [str(native_gateway_host), pipe_name, "8", active_context_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        monkeypatch.setattr(stable_simple_app, "setup_named_logger", lambda *args, **kwargs: logging.getLogger("stable-app-native-tip-host"))
        monkeypatch.setattr(stable_simple_app, "TextInjectionManager", _Dummy)
        monkeypatch.setattr(stable_simple_app, "OverlayPreview", _Dummy)
        monkeypatch.setattr(stable_simple_app, "OverlayRenderScheduler", _Dummy)
        monkeypatch.setattr(stable_simple_app, "TextPolisher", _Dummy)
        monkeypatch.setattr(stable_simple_app, "SystemOutputMuteGuard", _Dummy)
        monkeypatch.setattr(stable_simple_app, "is_current_process_elevated", lambda: False)
        monkeypatch.setenv("DOUBAO_TIP_GATEWAY_PIPE_NAME", pipe_name)

        app = stable_simple_app.StableVoiceInputApp(AgentConfig(), enable_tray=False)
        gateway = app._coordinator._tip_gateway
        assert isinstance(gateway, NamedPipeTipGateway)

        try:
            coordinator = app._coordinator
            fake_session = SimpleNamespace(
                session_id=701,
                state=WorkerSessionState.READY,
                mode="inject",
                stop_sent=False,
                stop_sent_at=None,
                finished_at=None,
            )
            target = FocusTarget(hwnd=101, focus_hwnd=202, process_id=1, process_name="notepad.exe")

            coordinator._asr_preflight.ensure_available = AsyncMock(
                return_value=SimpleNamespace(ok=True, message=None, stage="ok")
            )
            coordinator.session_manager.ensure_worker = AsyncMock(return_value=fake_session)
            coordinator.session_manager.send_command = AsyncMock()
            coordinator.session_manager._session = fake_session

            def _begin_session(captured_target, mode: str) -> None:
                fake_session.target = captured_target
                fake_session.mode = mode
                fake_session.state = WorkerSessionState.STREAMING

            coordinator.session_manager.begin_session = MagicMock(side_effect=_begin_session)
            coordinator.overlay_service.show_microphone = AsyncMock()
            coordinator.overlay_service.submit_interim = AsyncMock()
            coordinator.overlay_service.submit_final = AsyncMock()
            coordinator.overlay_service.hide = AsyncMock()
            coordinator.injection_service.capture_target = MagicMock(return_value=target)
            coordinator.injection_service.target_requires_admin = MagicMock(return_value=False)
            coordinator.injection_service.should_enable_inline_streaming = MagicMock(return_value=False)
            coordinator.injection_service.begin_session = MagicMock(return_value=None)
            coordinator.injection_service.apply_inline_interim = AsyncMock()
            coordinator._resolve_final_text = AsyncMock(
                return_value=SimpleNamespace(text="native committed", applied_mode="light", fallback_reason=None)
            )
            coordinator._inject_final = AsyncMock()
            coordinator._clear_active_session = AsyncMock()

            await coordinator._handle_press()
            assert coordinator.session_manager.send_command.await_count == 1
            assert coordinator.session_manager.send_command.await_args.args == ("START",)
            assert coordinator._tip_primary_active is True
            assert coordinator._tip_session_id == str(fake_session.session_id)
            assert fake_session.state == WorkerSessionState.STREAMING
            assert getattr(fake_session, "target", None) == target

            await coordinator._handle_worker_event(InterimResultEvent(text="native interim"))
            coordinator.overlay_service.submit_interim.assert_not_awaited()
            coordinator.injection_service.apply_inline_interim.assert_not_awaited()
            await coordinator._handle_worker_event(FinalResultEvent(text="native raw", segment_index=0))

            await coordinator._handle_worker_event(FinishedEvent())
            coordinator._inject_final.assert_not_awaited()
            assert "fallback" not in coordinator.get_status().lower()
        finally:
            app.stop()
            coordinator.hotkey_service.stop()
            coordinator.overlay_service.stop()
            monkeypatch.delenv("DOUBAO_TIP_GATEWAY_PIPE_NAME", raising=False)
    finally:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
