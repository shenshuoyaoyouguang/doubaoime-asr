from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

if "pywinauto" not in sys.modules:
    pywinauto_stub = types.ModuleType("pywinauto")
    pywinauto_stub.Desktop = object
    keyboard_stub = types.ModuleType("pywinauto.keyboard")
    keyboard_stub.send_keys = lambda *args, **kwargs: None
    sys.modules["pywinauto"] = pywinauto_stub
    sys.modules["pywinauto.keyboard"] = keyboard_stub

from doubaoime_asr.agent import stable_simple_app
from doubaoime_asr.agent.config import (
    AgentConfig,
    CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT,
    FINAL_COMMIT_SOURCE_RAW,
    INJECTION_POLICY_DIRECT_ONLY,
    POLISH_MODE_OLLAMA,
    STREAMING_TEXT_MODE_SAFE_INLINE,
)
from doubaoime_asr.agent.session_manager import (
    WorkerSession as RuntimeWorkerSession,
    WorkerSessionState,
)
from doubaoime_asr.agent.events import HotkeyPressEvent, WorkerExitEvent
from doubaoime_asr.agent.text_polisher import PolishResult


class _DummyInjector:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str, str]] = []

    def replace_text(self, target, previous_text: str, new_text: str) -> None:
        self.calls.append((target, previous_text, new_text))


class _DummyInjectionManager:
    def __init__(self, logger, *, policy: str) -> None:
        self.logger = logger
        self.policy = policy
        self.injector = _DummyInjector()
        self.captured_target: stable_simple_app.FocusTarget | None = None

    def set_policy(self, policy: str) -> None:
        self.policy = policy

    def capture_target(self):
        return self.captured_target

    async def inject_text(self, target, text: str):
        return SimpleNamespace(
            method="direct",
            target_profile="editor",
            clipboard_touched=False,
            restored_clipboard=False,
        )


class _DummyPreview:
    def __init__(self, *args, **kwargs) -> None:
        self.configured: list[AgentConfig] = []

    def configure(self, config: AgentConfig) -> None:
        self.configured.append(config)

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class _DummyScheduler:
    def __init__(self, *args, **kwargs) -> None:
        self.configured: list[AgentConfig] = []
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def configure(self, config: AgentConfig) -> None:
        self.configured.append(config)

    async def submit_interim(self, text: str) -> None:
        self.calls.append(("interim", (text,)))

    async def submit_final(self, text: str, *, kind: str) -> None:
        self.calls.append(("final", (text, kind)))

    async def show_microphone(self, placeholder_text: str = "正在聆听…") -> None:
        self.calls.append(("microphone", (placeholder_text,)))

    async def update_microphone_level(self, level: float) -> None:
        self.calls.append(("audio_level", (level,)))

    async def stop_microphone(self) -> None:
        self.calls.append(("stop_microphone", ()))

    async def hide(self, reason: str) -> None:
        self.calls.append(("hide", (reason,)))


class _DummyPolisher:
    def __init__(self, logger, config: AgentConfig) -> None:
        self.logger = logger
        self.config = config
        self.result = PolishResult(text="", applied_mode="off", latency_ms=0)
        self.warmup_calls: list[bool] = []

    def configure(self, config: AgentConfig) -> None:
        self.config = config

    async def polish(self, text: str) -> PolishResult:
        if self.result.text:
            return self.result
        return PolishResult(text=text, applied_mode=self.config.polish_mode, latency_ms=0)

    async def warmup(self) -> bool:
        self.warmup_calls.append(True)
        return True


class _DummyCaptureOutputGuard:
    def __init__(self, logger, *, policy: str) -> None:
        self.logger = logger
        self.policy = policy
        self.activate_calls = 0
        self.release_calls = 0

    def configure(self, policy: str) -> None:
        self.policy = policy

    def activate(self) -> bool:
        self.activate_calls += 1
        return True

    def release(self) -> bool:
        self.release_calls += 1
        return True


class _BrokenComposition:
    def __init__(self, *, rendered_text: str = "", final_text: str = "", fail_on: str = "render") -> None:
        self.rendered_text = rendered_text
        self.final_text = final_text
        self.fail_on = fail_on

    def render_interim(self, text: str) -> None:
        if self.fail_on == "render":
            raise RuntimeError("inline render failed")
        self.rendered_text = text

    def finalize(self, text: str) -> None:
        if self.fail_on == "finalize":
            raise RuntimeError("inline finalize failed")
        self.rendered_text = text
        self.final_text = text


def _build_app(monkeypatch: pytest.MonkeyPatch, config: AgentConfig | None = None) -> stable_simple_app.StableVoiceInputApp:
    monkeypatch.setattr(stable_simple_app, "setup_named_logger", lambda *args, **kwargs: logging.getLogger("stable-app-test"))
    monkeypatch.setattr(stable_simple_app, "TextInjectionManager", _DummyInjectionManager)
    monkeypatch.setattr(stable_simple_app, "OverlayPreview", _DummyPreview)
    monkeypatch.setattr(stable_simple_app, "OverlayRenderScheduler", _DummyScheduler)
    monkeypatch.setattr(stable_simple_app, "TextPolisher", _DummyPolisher)
    monkeypatch.setattr(stable_simple_app, "SystemOutputMuteGuard", _DummyCaptureOutputGuard)
    monkeypatch.setattr(stable_simple_app, "is_current_process_elevated", lambda: False)
    return stable_simple_app.StableVoiceInputApp(config or AgentConfig(), enable_tray=False)


def _build_session(session_id: int) -> stable_simple_app.WorkerSession:
    async def create_session() -> stable_simple_app.WorkerSession:
        async def done() -> None:
            return None

        stdout_task = asyncio.create_task(done())
        stderr_task = asyncio.create_task(done())
        wait_task = asyncio.create_task(done())
        await asyncio.gather(stdout_task, stderr_task, wait_task)
        return stable_simple_app.WorkerSession(
            session_id=session_id,
            process=SimpleNamespace(returncode=None, stdin=None),
            stdout_task=stdout_task,
            stderr_task=stderr_task,
            wait_task=wait_task,
        )

    return asyncio.run(create_session())


def _build_runtime_session(
    session_id: int,
    *,
    mode: str = "inject",
    target: stable_simple_app.FocusTarget | None = None,
) -> RuntimeWorkerSession:
    session = RuntimeWorkerSession(
        session_id=session_id,
        process=SimpleNamespace(returncode=None, stdin=None),
    )
    session.begin(target, mode)
    session.transition_to(WorkerSessionState.STREAMING)
    return session


def test_build_app_binds_session_manager_event_bridge(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)

    assert app._coordinator.session_manager._on_event is not None


def test_state_property_bridges_delegate_to_coordinator(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    config = AgentConfig(mode="recognize")

    app.config = config
    app.mode = "recognize"
    app.launch_args = ["--console"]
    app._stopping = True
    app._pending_listener_rebind = True
    app._pending_worker_restart = True
    app._pending_polisher_warmup = True
    app._status = "忙碌"

    assert app.config is config
    assert app.mode == "recognize"
    assert app.launch_args == ["--console"]
    assert app._stopping is True
    assert app._pending_listener_rebind is True
    assert app._pending_worker_restart is True
    assert app._pending_polisher_warmup is True
    assert app._status == "忙碌"


def test_session_override_bridge_helpers_delegate_and_preserve_runtime_flow(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    runtime_session = RuntimeWorkerSession(
        session_id=55,
        process=SimpleNamespace(returncode=None, stdin=None),
        state=WorkerSessionState.READY,
    )
    wrapped = app._wrap_session(runtime_session)

    assert app._has_test_session_override() is False
    assert isinstance(wrapped, stable_simple_app._SessionCompatWrapper)
    assert app._uses_runtime_session_flow(wrapped) is True

    app._session = _build_session(56)
    assert app._has_test_session_override() is True
    assert app._uses_runtime_session_flow(wrapped) is False

    app._reset_test_session_override()
    assert app._has_test_session_override() is False


def test_session_setter_syncs_compat_and_runtime_owners(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    compat_session = _build_session(57)
    runtime_session = RuntimeWorkerSession(
        session_id=58,
        process=SimpleNamespace(returncode=None, stdin=None),
        state=WorkerSessionState.READY,
    )
    wrapped = stable_simple_app._SessionCompat.wrap(runtime_session)

    app._session = compat_session
    assert app._coordinator.session_manager._session is compat_session._real

    app._session = wrapped
    assert app._coordinator.session_manager._session is runtime_session


def test_reset_test_session_override_restores_manager_backed_session_view(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    runtime_session = RuntimeWorkerSession(
        session_id=59,
        process=SimpleNamespace(returncode=None, stdin=None),
        state=WorkerSessionState.READY,
    )

    app._session = None
    app._coordinator.session_manager._session = runtime_session
    app._reset_test_session_override()
    session_view = app._session

    assert session_view is not None
    assert isinstance(session_view, stable_simple_app._SessionCompat)
    assert session_view.session_id == 59


def test_compat_singleton_accessors_return_stable_instances(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)

    assert app.injection_manager is app.injection_manager
    assert app.preview is app.preview
    assert app.overlay_scheduler is app.overlay_scheduler


def test_restart_worker_and_handle_release_delegate_to_coordinator(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    app._coordinator.session_manager.restart_worker = AsyncMock()
    app._coordinator._handle_release = AsyncMock()

    asyncio.run(app._restart_worker())
    asyncio.run(app._handle_release())

    app._coordinator.session_manager.restart_worker.assert_awaited_once()
    app._coordinator._handle_release.assert_awaited_once()


def test_build_listener_wraps_hotkey_service(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    start_calls: list[int] = []
    stop_calls: list[bool] = []

    monkeypatch.setattr(
        app._coordinator.hotkey_service,
        "start",
        lambda vk: start_calls.append(vk),
    )
    monkeypatch.setattr(
        app._coordinator.hotkey_service,
        "stop",
        lambda: stop_calls.append(True),
    )

    listener = app._build_listener(object(), 0x77)
    listener.start()
    listener.stop()

    assert start_calls == [0x77]
    assert stop_calls == [True]


def test_emit_forwards_converted_legacy_event(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    app._coordinator._emit = MagicMock()

    app._emit("press")

    event = app._coordinator._emit.call_args.args[0]
    assert isinstance(event, HotkeyPressEvent)


def test_emit_threadsafe_forwards_converted_legacy_event(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    app._coordinator._emit_threadsafe = MagicMock()
    loop = object()

    app._emit_threadsafe(loop, "worker_exit", (12, 9))

    emitted_loop, event = app._coordinator._emit_threadsafe.call_args.args
    assert emitted_loop is loop
    assert isinstance(event, WorkerExitEvent)
    assert event.session_id == 12
    assert event.exit_code == 9


def test_handle_worker_exit_ignores_stale_session(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    current_session = _build_session(2)
    app._session = current_session
    app.set_status("\u7a7a\u95f2")

    asyncio.run(app._handle_worker_exit(1, 7))

    assert app._session is current_session
    assert app._status == "\u7a7a\u95f2"


def test_ensure_worker_timeout_terminates_process_before_dispose(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch, AgentConfig(worker_cold_ready_timeout_ms=4200))
    process = SimpleNamespace(returncode=None, stdout=None, stderr=None, stdin=None)
    terminated_sessions: list[stable_simple_app.WorkerSession] = []

    async def fake_spawn_worker():
        return process

    async def fake_reader(*args, **kwargs):
        await asyncio.Event().wait()

    async def fake_wait_worker(*args, **kwargs):
        while process.returncode is None:
            await asyncio.sleep(0.01)

    async def fake_terminate_session_process(session: stable_simple_app.WorkerSession) -> None:
        terminated_sessions.append(session)
        process.returncode = 9

    monkeypatch.setattr(app, "_spawn_worker", fake_spawn_worker)
    monkeypatch.setattr(app, "_read_worker_stdout", fake_reader)
    monkeypatch.setattr(app, "_read_worker_stderr", fake_reader)
    monkeypatch.setattr(app, "_wait_worker", fake_wait_worker)
    monkeypatch.setattr(app, "_terminate_session_process", fake_terminate_session_process)

    with pytest.raises(RuntimeError, match="did not become ready"):
        asyncio.run(app._ensure_worker())

    assert len(terminated_sessions) == 1
    assert app._session is None


def test_select_worker_ready_timeout_seconds_uses_cold_then_warm(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(
        monkeypatch,
        AgentConfig(worker_ready_timeout_ms=1800, worker_cold_ready_timeout_ms=4200),
    )

    assert app._select_worker_ready_timeout_seconds() == 4.2
    app._coordinator.session_manager._worker_started_once = True
    assert app._select_worker_ready_timeout_seconds() == 1.8


def test_read_worker_stdout_unwraps_runtime_session_owner(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    runtime_session = RuntimeWorkerSession(
        session_id=44,
        process=SimpleNamespace(returncode=None, stdin=None),
        state=WorkerSessionState.READY,
    )
    wrapped = stable_simple_app._SessionCompat.wrap(runtime_session)
    received_sessions: list[RuntimeWorkerSession] = []

    async def fake_read_worker_stdout(stream, session):
        received_sessions.append(session)

    monkeypatch.setattr(
        app._coordinator.session_manager,
        "_read_worker_stdout",
        fake_read_worker_stdout,
    )

    asyncio.run(app._read_worker_stdout(object(), wrapped))

    assert received_sessions == [runtime_session]


def test_apply_pending_listener_rebind_uses_app_rebind_hook(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    app._pending_listener_rebind = True
    rebind_calls: list[int] = []
    monkeypatch.setattr(app, "_rebind_listener", lambda hotkey_vk: rebind_calls.append(hotkey_vk))

    app._apply_pending_listener_rebind("listener_rebind_failed")

    assert rebind_calls == [app.config.effective_hotkey_vk()]
    assert app._pending_listener_rebind is False


def test_status_result_bridge_helpers_delegate_to_coordinator(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    result = object()
    monkeypatch.setattr(app._coordinator, "_activate_capture_output", lambda: "mute")
    monkeypatch.setattr(app._coordinator, "_release_capture_output", lambda: "restore")
    monkeypatch.setattr(app._coordinator, "_mode_display_label", lambda mode=None: f"mode:{mode}")
    monkeypatch.setattr(app._coordinator, "_session_start_status", lambda warning: f"start:{warning}")
    monkeypatch.setattr(app._coordinator, "_status_for_final_result", lambda current, raw: f"status:{raw}:{current is result}")

    assert app._activate_capture_output() == "mute"
    assert app._release_capture_output() == "restore"
    assert app._mode_display_label("inject") == "mode:inject"
    assert app._session_start_status("warn") == "start:warn"
    assert app._status_for_final_result(result, "raw-text") == "status:raw-text:True"


def test_resolve_final_text_and_polisher_change_delegate(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    old_config = AgentConfig(polish_mode="off")
    new_config = AgentConfig(polish_mode=POLISH_MODE_OLLAMA)

    async def fake_resolve_final_text(raw_text: str) -> str:
        return f"resolved:{raw_text}"

    monkeypatch.setattr(app._coordinator, "_resolve_final_text", fake_resolve_final_text)
    monkeypatch.setattr(app._coordinator, "_polisher_config_changed", lambda old, new: (old, new) == (old_config, new_config))

    assert asyncio.run(app._resolve_final_text("hello")) == "resolved:hello"
    assert app._polisher_config_changed(old_config, new_config) is True


def test_terminate_worker_waits_for_killed_process(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(
        monkeypatch,
        AgentConfig(worker_exit_grace_timeout_ms=1500, worker_kill_wait_timeout_ms=700),
    )
    session = _build_session(11)
    wait_for_calls: list[float] = []
    sent_commands: list[str] = []

    class _FakeProcess:
        def __init__(self) -> None:
            self.stdin = object()
            self.returncode = None
            self.kill_calls = 0

        async def wait(self) -> int:
            return int(self.returncode or 0)

        def kill(self) -> None:
            self.kill_calls += 1
            self.returncode = 9

    process = _FakeProcess()
    session.process = process
    app._session = session

    async def fake_send_worker_command(command: str) -> None:
        sent_commands.append(command)

    async def fake_wait_for(awaitable, timeout):
        wait_for_calls.append(timeout)
        if len(wait_for_calls) == 1:
            awaitable.close()
            raise asyncio.TimeoutError
        return await awaitable

    monkeypatch.setattr(app, "_send_worker_command", fake_send_worker_command)
    monkeypatch.setattr(stable_simple_app.asyncio, "wait_for", fake_wait_for)

    asyncio.run(app._terminate_worker())

    assert sent_commands == ["EXIT"]
    assert process.kill_calls == 1
    assert wait_for_calls == [1.5, 0.7]
    assert app._session is None


def test_handle_worker_exit_applies_pending_listener_rebind(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    session = _build_session(12)
    app._session = session
    app._pending_listener_rebind = True
    rebind_calls: list[int] = []
    monkeypatch.setattr(app, "_rebind_listener", lambda hotkey_vk: rebind_calls.append(hotkey_vk))

    asyncio.run(app._handle_worker_exit(12, 1))

    assert rebind_calls == [app.config.effective_hotkey_vk()]
    assert app._pending_listener_rebind is False


def test_apply_config_defers_runtime_mutations_during_active_session(
    monkeypatch: pytest.MonkeyPatch,
):
    old_config = AgentConfig(hotkey="f8", hotkey_vk=0x77, hotkey_display="F8")
    new_config = AgentConfig(
        hotkey="f9",
        hotkey_vk=0x78,
        hotkey_display="F9",
        credential_path="new-credentials.json",
        microphone_device="Mic 2",
        polish_mode=POLISH_MODE_OLLAMA,
        ollama_model="qwen3",
    )
    app = _build_app(monkeypatch, old_config)
    session = _build_session(24)
    session.active = True
    app._session = session

    rebind_calls: list[int] = []
    restart_calls: list[bool] = []
    warmup_calls: list[str] = []
    monkeypatch.setattr(app, "_rebind_listener", lambda hotkey_vk: rebind_calls.append(hotkey_vk))

    async def fake_restart_worker() -> None:
        restart_calls.append(True)

    monkeypatch.setattr(app, "_restart_worker", fake_restart_worker)
    monkeypatch.setattr(app, "_schedule_polisher_warmup", lambda reason: warmup_calls.append(reason))
    monkeypatch.setattr(AgentConfig, "save", lambda self, path=None: Path("config.json"))

    asyncio.run(app._apply_config(new_config))

    assert rebind_calls == []
    assert restart_calls == []
    assert warmup_calls == []
    assert app._pending_listener_rebind is True
    assert app._pending_worker_restart is True
    assert app._pending_polisher_warmup is True
    assert app.config == new_config


def test_apply_config_rolls_back_runtime_changes_after_save_failure(monkeypatch: pytest.MonkeyPatch):
    old_config = AgentConfig(
        hotkey="f8",
        hotkey_vk=0x77,
        hotkey_display="F8",
        injection_policy=INJECTION_POLICY_DIRECT_ONLY,
    )
    new_config = AgentConfig(
        hotkey="f9",
        hotkey_vk=0x78,
        hotkey_display="F9",
        credential_path="new-credentials.json",
        microphone_device="Mic 2",
    )
    app = _build_app(monkeypatch, old_config)

    rebind_calls: list[int] = []
    restart_calls: list[str] = []
    monkeypatch.setattr(app, "_rebind_listener", lambda hotkey_vk: rebind_calls.append(hotkey_vk))

    async def fake_restart_worker() -> None:
        restart_calls.append(app.config.effective_hotkey_display())

    monkeypatch.setattr(app, "_restart_worker", fake_restart_worker)

    def fake_save(self, path=None):
        if self.effective_hotkey_display() == "F9":
            raise OSError("disk full")
        return Path("config.json")

    monkeypatch.setattr(AgentConfig, "save", fake_save)

    asyncio.run(app._apply_config(new_config))

    assert rebind_calls == [0x78, 0x77]
    assert restart_calls == ["F9", "F8"]
    assert app.config == old_config
    assert app.mode == old_config.mode
    assert app.injection_manager.policy == old_config.injection_policy
    assert app.preview.configured[-1] == old_config
    assert app.overlay_scheduler.configured[-1] == old_config
    assert app._status == "\u8bbe\u7f6e\u4fdd\u5b58\u5931\u8d25\uff0c\u5df2\u6062\u590d\u65e7\u914d\u7f6e"


def test_handle_worker_final_uses_polished_text(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(polish_mode=POLISH_MODE_OLLAMA)
    app = _build_app(monkeypatch, config)
    session = _build_session(3)
    session.active = True
    session.mode = "recognize"
    app._session = session
    app.text_polisher.result = PolishResult(text="润色后的文本。", applied_mode="ollama", latency_ms=120)

    injected: list[str] = []

    async def fake_inject(text: str) -> None:
        injected.append(text)

    monkeypatch.setattr(app, "_inject_final", fake_inject)

    asyncio.run(app._handle_worker_event(3, {"type": "final", "text": "原文", "segment_index": 0}))

    assert injected == []
    assert app.overlay_scheduler.calls == [("final", ("原文", "final_raw"))]

    asyncio.run(app._handle_worker_event(3, {"type": "finished"}))

    assert injected == ["润色后的文本。"]
    assert app.overlay_scheduler.calls == [
        ("final", ("原文", "final_raw")),
        ("final", ("润色后的文本。", "final_committed")),
        ("hide", ("finished",)),
    ]
    assert app._status == "最终结果: 润色后的文本。"


def test_handle_worker_final_fallback_status_uses_raw_text(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(polish_mode=POLISH_MODE_OLLAMA)
    app = _build_app(monkeypatch, config)
    session = _build_session(4)
    session.active = True
    session.mode = "recognize"
    app._session = session
    app.text_polisher.result = PolishResult(
        text="原始识别文本",
        applied_mode="raw_fallback",
        latency_ms=800,
        fallback_reason="timeout",
    )

    monkeypatch.setattr(app, "_inject_final", lambda text: asyncio.sleep(0))

    asyncio.run(app._handle_worker_event(4, {"type": "final", "text": "原始识别文本", "segment_index": 0}))
    asyncio.run(app._handle_worker_event(4, {"type": "finished"}))

    assert app.overlay_scheduler.calls == [
        ("final", ("原始识别文本", "final_raw")),
        ("hide", ("finished",)),
    ]
    assert app._status == "润色超时，已使用原文: 原始识别文本"


def test_handle_worker_final_can_commit_raw_text_when_configured(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(polish_mode=POLISH_MODE_OLLAMA, final_commit_source=FINAL_COMMIT_SOURCE_RAW)
    app = _build_app(monkeypatch, config)
    session = _build_session(41)
    session.active = True
    session.mode = "recognize"
    app._session = session
    app.text_polisher.result = PolishResult(text="润色后的文本。", applied_mode="ollama", latency_ms=120)

    injected: list[str] = []

    async def fake_inject(text: str) -> None:
        injected.append(text)

    monkeypatch.setattr(app, "_inject_final", fake_inject)

    asyncio.run(app._handle_worker_event(41, {"type": "final", "text": "原文", "segment_index": 0}))
    asyncio.run(app._handle_worker_event(41, {"type": "finished"}))

    assert injected == ["原文"]
    assert app.overlay_scheduler.calls == [
        ("final", ("原文", "final_raw")),
        ("hide", ("finished",)),
    ]
    assert app._status == "最终提交原文: 原文"


def test_handle_worker_interim_updates_inline_composition(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="inject", streaming_text_mode=STREAMING_TEXT_MODE_SAFE_INLINE, render_debounce_ms=0)
    app = _build_app(monkeypatch, config)
    session = _build_session(9)
    session.active = True
    session.mode = "inject"
    target = stable_simple_app.FocusTarget(hwnd=1, is_terminal=False)
    session.target = target
    session.inline_streaming_enabled = True
    session.composition = stable_simple_app.CompositionSession(app.injection_manager.injector, target)
    app._session = session

    asyncio.run(app._handle_worker_event(9, {"type": "interim", "text": "你好啊", "segment_index": 0}))

    assert app.overlay_scheduler.calls == [("interim", ("你好啊",))]
    assert app.injection_manager.injector.calls == [(target, "", "你好啊")]
    assert session.composition.rendered_text == "你好啊"


def test_handle_worker_event_ignores_stale_session_id(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    session = _build_session(22)
    session.active = True
    app._session = session
    clear_calls: list[int] = []

    async def fake_clear() -> None:
        clear_calls.append(1)

    monkeypatch.setattr(app, "_clear_active_session", fake_clear)

    asyncio.run(app._handle_worker_event(21, {"type": "finished"}))

    assert clear_calls == []
    assert app.overlay_scheduler.calls == []
    assert session.active is True


def test_handle_worker_event_parses_dict_before_forwarding(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    session = _build_session(23)
    app._session = session
    forwarded: list[object] = []

    async def fake_handle_worker_event(event: object) -> None:
        forwarded.append(event)

    monkeypatch.setattr(app._coordinator, "_handle_worker_event", fake_handle_worker_event)

    asyncio.run(app._handle_worker_event(23, {"type": "ready"}))

    assert len(forwarded) == 1
    assert getattr(forwarded[0], "event_type", None) == "ready"


def test_runtime_inline_callbacks_use_injection_service_session(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="inject", streaming_text_mode=STREAMING_TEXT_MODE_SAFE_INLINE, render_debounce_ms=0)
    app = _build_app(monkeypatch, config)
    target = stable_simple_app.FocusTarget(hwnd=21, is_terminal=False)
    runtime_session = _build_runtime_session(21, target=target)
    app._coordinator.session_manager._session = runtime_session
    composition = app._coordinator.injection_service.begin_session(
        target,
        "inject",
        inline_streaming_enabled=True,
    )

    assert composition is not None

    asyncio.run(app._coordinator.injection_service.apply_inline_interim("你好"))
    asyncio.run(app._coordinator._inject_final("你好啊"))

    assert app.injection_manager.injector.calls == [
        (target, "", "你好"),
        (target, "你好", "你好啊"),
    ]
    assert composition.rendered_text == "你好啊"
    assert composition.final_text == "你好啊"


def test_session_compat_wrapper_begin_accepts_runtime_kwargs(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    target = stable_simple_app.FocusTarget(hwnd=31, is_terminal=False)
    runtime_session = RuntimeWorkerSession(
        session_id=31,
        process=SimpleNamespace(returncode=None, stdin=None),
        state=WorkerSessionState.READY,
    )
    wrapped = stable_simple_app._SessionCompat.wrap(runtime_session)

    wrapped.begin(
        target,
        "inject",
        composition=stable_simple_app.CompositionSession(app.injection_manager.injector, target),
        inline_streaming_enabled=True,
    )

    assert runtime_session.target == target
    assert runtime_session.mode == "inject"
    assert runtime_session.state == WorkerSessionState.STREAMING


def test_handle_worker_audio_level_updates_overlay(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    session = _build_session(90)
    session.active = True
    app._session = session

    asyncio.run(app._handle_worker_event(90, {"type": "audio_level", "level": 0.35}))

    assert app.overlay_scheduler.calls == [("audio_level", (0.35,))]


def test_handle_worker_interim_keeps_previous_final_segments(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="inject", streaming_text_mode=STREAMING_TEXT_MODE_SAFE_INLINE, render_debounce_ms=0)
    app = _build_app(monkeypatch, config)
    session = _build_session(91)
    session.active = True
    session.mode = "inject"
    target = stable_simple_app.FocusTarget(hwnd=7, is_terminal=False)
    session.target = target
    session.inline_streaming_enabled = True
    session.composition = stable_simple_app.CompositionSession(app.injection_manager.injector, target)
    app._session = session

    asyncio.run(app._handle_worker_event(91, {"type": "final", "text": "第一句。", "segment_index": 0}))
    asyncio.run(app._handle_worker_event(91, {"type": "interim", "text": "第二", "segment_index": 1}))

    assert app.injection_manager.injector.calls == [
        (target, "", "第一句。"),
        (target, "第一句。", "第一句。第二"),
    ]
    assert session.composition.rendered_text == "第一句。第二"


def test_handle_worker_finished_injects_aggregated_text_once(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="inject")
    app = _build_app(monkeypatch, config)
    session = _build_session(92)
    session.active = True
    session.mode = "inject"
    session.target = stable_simple_app.FocusTarget(hwnd=8, is_terminal=False)
    app._session = session

    injected: list[str] = []

    async def fake_inject_text(target, text: str):
        injected.append(text)
        return SimpleNamespace(
            method="direct",
            target_profile="editor",
            clipboard_touched=False,
            restored_clipboard=False,
        )

    monkeypatch.setattr(app.injection_manager, "inject_text", fake_inject_text, raising=False)

    asyncio.run(app._handle_worker_event(92, {"type": "final", "text": "第一句。", "segment_index": 0}))
    asyncio.run(app._handle_worker_event(92, {"type": "interim", "text": "第二", "segment_index": 1}))
    asyncio.run(app._handle_worker_event(92, {"type": "final", "text": "第二句。", "segment_index": 1}))

    assert injected == []

    asyncio.run(app._handle_worker_event(92, {"type": "finished"}))

    assert injected == ["第一句。第二句。"]
    assert app.overlay_scheduler.calls == [
        ("final", ("第一句。", "final_raw")),
        ("interim", ("第一句。第二",)),
        ("final", ("第一句。第二句。", "final_raw")),
        ("hide", ("finished",)),
    ]


def test_apply_inline_interim_blocks_final_fallback_after_inline_failure(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="inject", streaming_text_mode=STREAMING_TEXT_MODE_SAFE_INLINE, render_debounce_ms=0)
    app = _build_app(monkeypatch, config)
    session = _build_session(10)
    session.active = True
    session.mode = "inject"
    session.target = stable_simple_app.FocusTarget(hwnd=9, is_terminal=False)
    session.inline_streaming_enabled = True
    session.composition = _BrokenComposition(rendered_text="hel", fail_on="render")
    app._session = session

    asyncio.run(app._apply_inline_interim("hello"))

    assert session.inline_streaming_enabled is False
    assert session.final_injection_blocked is True
    assert session.target is None
    assert app._status == "实时上屏失败，仅保留识别"


def test_inject_final_uses_inline_composition_when_available(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="inject", streaming_text_mode=STREAMING_TEXT_MODE_SAFE_INLINE, render_debounce_ms=0)
    app = _build_app(monkeypatch, config)
    session = _build_session(11)
    session.active = True
    session.mode = "inject"
    target = stable_simple_app.FocusTarget(hwnd=2, is_terminal=False)
    session.target = target
    session.inline_streaming_enabled = True
    session.composition = stable_simple_app.CompositionSession(app.injection_manager.injector, target)
    session.composition.render_interim("原文")
    app._session = session

    asyncio.run(app._inject_final("最终文本"))

    assert app.injection_manager.injector.calls == [
        (target, "", "原文"),
        (target, "原文", "最终文本"),
    ]
    assert session.composition.final_text == "最终文本"


def test_inject_final_does_not_fallback_type_when_inline_text_exists(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="inject", streaming_text_mode=STREAMING_TEXT_MODE_SAFE_INLINE, render_debounce_ms=0)
    app = _build_app(monkeypatch, config)
    session = _build_session(12)
    session.active = True
    session.mode = "inject"
    target = stable_simple_app.FocusTarget(hwnd=4, is_terminal=False)
    session.target = target
    session.inline_streaming_enabled = True
    session.composition = _BrokenComposition(rendered_text="hel", fail_on="finalize")
    app._session = session

    inject_calls: list[str] = []

    async def fake_inject_text(target, text: str):
        inject_calls.append(text)
        return SimpleNamespace(
            method="direct",
            target_profile="default",
            clipboard_touched=False,
            restored_clipboard=False,
        )

    monkeypatch.setattr(app.injection_manager, "inject_text", fake_inject_text, raising=False)

    asyncio.run(app._inject_final("hello"))

    assert inject_calls == []
    assert session.inline_streaming_enabled is False
    assert session.final_injection_blocked is True
    assert session.target is None
    assert app._status == "实时上屏失败，仅保留识别"


def test_should_enable_inline_streaming_skips_terminal_targets(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="inject", streaming_text_mode=STREAMING_TEXT_MODE_SAFE_INLINE)
    app = _build_app(monkeypatch, config)

    assert app._should_enable_inline_streaming(stable_simple_app.FocusTarget(hwnd=3, is_terminal=False)) is True
    assert app._should_enable_inline_streaming(stable_simple_app.FocusTarget(hwnd=3, is_terminal=True)) is False


def test_handle_press_activates_capture_output(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="recognize", capture_output_policy=CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT)
    app = _build_app(monkeypatch, config)
    session = _build_session(5)

    async def fake_ensure_worker() -> stable_simple_app.WorkerSession:
        return session

    commands: list[str] = []

    async def fake_send_worker_command(command: str) -> None:
        commands.append(command)

    monkeypatch.setattr(app, "_ensure_worker", fake_ensure_worker)
    monkeypatch.setattr(app, "_send_worker_command", fake_send_worker_command)

    asyncio.run(app._handle_press())

    assert commands == ["START"]
    assert app.capture_output_guard.activate_calls == 1
    assert app.overlay_scheduler.calls == [("microphone", ("正在聆听…",))]
    assert app._status == "启动识别中（仅识别，不自动上屏）…"


def test_handle_press_runtime_path_initializes_injection_service(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="inject", streaming_text_mode=STREAMING_TEXT_MODE_SAFE_INLINE)
    app = _build_app(monkeypatch, config)
    target = stable_simple_app.FocusTarget(hwnd=32, is_terminal=False)
    runtime_session = RuntimeWorkerSession(
        session_id=32,
        process=SimpleNamespace(returncode=None, stdin=None),
        state=WorkerSessionState.READY,
    )
    app._coordinator.session_manager._session = runtime_session
    wrapped = stable_simple_app._SessionCompat.wrap(runtime_session)
    commands: list[str] = []

    async def fake_ensure_worker():
        return wrapped

    async def fake_send_worker_command(command: str) -> None:
        commands.append(command)

    app.injection_manager.captured_target = target
    monkeypatch.setattr(app, "_ensure_worker", fake_ensure_worker)
    monkeypatch.setattr(app, "_send_worker_command", fake_send_worker_command)

    asyncio.run(app._handle_press())
    asyncio.run(app._coordinator.injection_service.apply_inline_interim("你好"))

    composition = app._coordinator.injection_service.get_composition()

    assert commands == ["START"]
    assert app._coordinator.injection_service.get_current_target() == target
    assert app._coordinator.injection_service.is_inline_streaming_enabled() is True
    assert composition is not None
    assert app.injection_manager.injector.calls == [(target, "", "你好")]


def test_send_stop_hides_microphone_hud_before_waiting(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    session = _build_session(14)
    session.active = True
    app._session = session
    commands: list[str] = []

    async def fake_send_worker_command(command: str) -> None:
        commands.append(command)

    monkeypatch.setattr(app, "_send_worker_command", fake_send_worker_command)

    asyncio.run(app._send_stop("worker_stop_sent", "等待最终结果…"))

    assert commands == ["STOP"]
    assert app.overlay_scheduler.calls == [("stop_microphone", ())]
    assert session.stop_sent is True
    assert session.pending_stop is False
    assert app._status == "等待最终结果…"


def test_handle_press_releases_capture_output_when_start_fails(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="recognize", capture_output_policy=CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT)
    app = _build_app(monkeypatch, config)
    session = _build_session(6)

    async def fake_ensure_worker() -> stable_simple_app.WorkerSession:
        return session

    async def fake_send_worker_command(command: str) -> None:
        raise RuntimeError("boom")

    restart_calls: list[bool] = []

    async def fake_restart_worker() -> None:
        restart_calls.append(True)

    monkeypatch.setattr(app, "_ensure_worker", fake_ensure_worker)
    monkeypatch.setattr(app, "_send_worker_command", fake_send_worker_command)
    monkeypatch.setattr(app, "_restart_worker", fake_restart_worker)

    asyncio.run(app._handle_press())

    assert app.capture_output_guard.activate_calls == 1
    assert app.capture_output_guard.release_calls == 1
    assert restart_calls == [True]
    assert app._status == "启动识别失败，请查看 controller.log"


def test_clear_active_session_releases_capture_output(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="recognize", capture_output_policy=CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT)
    app = _build_app(monkeypatch, config)
    session = _build_session(7)
    session.active = True
    app._session = session

    asyncio.run(app._clear_active_session())

    assert session.active is False
    assert app.capture_output_guard.release_calls == 1


def test_handle_worker_exit_releases_capture_output(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="recognize", capture_output_policy=CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT)
    app = _build_app(monkeypatch, config)
    session = _build_session(8)
    app._session = session

    asyncio.run(app._handle_worker_exit(8, 1))

    assert app.capture_output_guard.release_calls == 1


def test_handle_press_blocks_admin_target_before_start(monkeypatch: pytest.MonkeyPatch):
    config = AgentConfig(mode="inject")
    app = _build_app(monkeypatch, config)
    session = _build_session(13)
    app.injection_manager.captured_target = stable_simple_app.FocusTarget(
        hwnd=100,
        process_id=200,
        process_name="WindowsTerminal.exe",
        is_terminal=True,
        terminal_kind="windows_terminal",
        is_elevated=True,
    )

    async def fake_ensure_worker() -> stable_simple_app.WorkerSession:
        return session

    send_calls: list[str] = []

    async def fake_send_worker_command(command: str) -> None:
        send_calls.append(command)

    monkeypatch.setattr(app, "_ensure_worker", fake_ensure_worker)
    monkeypatch.setattr(app, "_send_worker_command", fake_send_worker_command)

    asyncio.run(app._handle_press())

    assert send_calls == []
    assert app._status == "管理员终端需要以管理员身份运行代理；请重新以管理员身份启动代理"


def test_check_foreground_elevation_warns_and_clears(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch, AgentConfig(mode="inject"))
    app.injection_manager.captured_target = stable_simple_app.FocusTarget(
        hwnd=10,
        process_id=20,
        process_name="WindowsTerminal.exe",
        is_terminal=True,
        terminal_kind="windows_terminal",
        is_elevated=True,
    )

    app._check_foreground_elevation()
    assert app._status == "管理员终端需要以管理员身份运行代理；请重新以管理员身份启动代理"

    app.injection_manager.captured_target = stable_simple_app.FocusTarget(
        hwnd=11,
        process_id=21,
        process_name="notepad.exe",
        is_terminal=False,
        is_elevated=False,
    )
    app._check_foreground_elevation()

    assert app._status == "空闲"


def test_runtime_send_stop_if_needed_delegates_once(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    runtime_session = _build_runtime_session(30)
    runtime_session.pending_stop = True
    app._coordinator.session_manager._session = runtime_session
    send_stop_calls: list[int] = []

    async def fake_send_stop() -> None:
        send_stop_calls.append(1)
        runtime_session.mark_stop_sent()

    monkeypatch.setattr(app._coordinator.session_manager, "send_stop", fake_send_stop)

    asyncio.run(app._coordinator._send_stop_if_needed())

    assert send_stop_calls == [1]
    assert runtime_session.stop_sent is True
    assert app.overlay_scheduler.calls == [("stop_microphone", ())]
    assert app._status == "正在转写…"


def test_coordinator_restart_hook_uses_wrapper_compat(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    calls: list[bool] = []

    async def fake_restart() -> None:
        calls.append(True)

    monkeypatch.setattr(app, "_handle_restart_as_admin", fake_restart)

    asyncio.run(app._coordinator._handle_restart_as_admin())

    assert calls == [True]


def test_handle_restart_as_admin_stops_after_success(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    app.launch_args = ["--console"]
    restart_calls: list[tuple[tuple[str, ...], str, bool]] = []

    def fake_restart_as_admin(args, *, executable, frozen):
        restart_calls.append((tuple(args), executable, frozen))
        return True

    monkeypatch.setattr(stable_simple_app, "restart_as_admin", fake_restart_as_admin)

    asyncio.run(app._handle_restart_as_admin())

    assert restart_calls == [(("--console",), sys.executable, False)]
    assert app._stopping is True
    assert app._status == "正在以管理员身份重启…"



def test_handle_restart_as_admin_returns_when_already_elevated(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    monkeypatch.setattr(stable_simple_app, "is_current_process_elevated", lambda: True)
    restart_calls: list[bool] = []

    def fake_restart_as_admin(*args, **kwargs):
        restart_calls.append(True)
        return True

    monkeypatch.setattr(stable_simple_app, "restart_as_admin", fake_restart_as_admin)

    asyncio.run(app._handle_restart_as_admin())

    assert restart_calls == []
    assert app._stopping is False
    assert app._process_elevated is True
    assert app._status == "进程已在管理员模式运行"

def test_handle_restart_as_admin_reports_declined(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    monkeypatch.setattr(stable_simple_app, "restart_as_admin", lambda *args, **kwargs: False)

    asyncio.run(app._handle_restart_as_admin())

    assert app._stopping is False
    assert app._status == "管理员重启已取消或被系统拒绝"
