from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import types

import pytest

if "pywinauto" not in sys.modules:
    pywinauto_stub = types.ModuleType("pywinauto")
    pywinauto_stub.Desktop = object
    keyboard_stub = types.ModuleType("pywinauto.keyboard")
    keyboard_stub.send_keys = lambda *args, **kwargs: None
    sys.modules["pywinauto"] = pywinauto_stub
    sys.modules["pywinauto.keyboard"] = keyboard_stub

from doubaoime_asr.agent import stable_simple_app
from doubaoime_asr.agent.config import AgentConfig, INJECTION_POLICY_DIRECT_ONLY


class _DummyInjectionManager:
    def __init__(self, logger, *, policy: str) -> None:
        self.logger = logger
        self.policy = policy

    def set_policy(self, policy: str) -> None:
        self.policy = policy


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

    def configure(self, config: AgentConfig) -> None:
        self.configured.append(config)

    async def hide(self, reason: str) -> None:
        return None


def _build_app(monkeypatch: pytest.MonkeyPatch, config: AgentConfig | None = None) -> stable_simple_app.StableVoiceInputApp:
    monkeypatch.setattr(stable_simple_app, "setup_named_logger", lambda *args, **kwargs: logging.getLogger("stable-app-test"))
    monkeypatch.setattr(stable_simple_app, "TextInjectionManager", _DummyInjectionManager)
    monkeypatch.setattr(stable_simple_app, "OverlayPreview", _DummyPreview)
    monkeypatch.setattr(stable_simple_app, "OverlayRenderScheduler", _DummyScheduler)
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


def test_handle_worker_exit_ignores_stale_session(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    current_session = _build_session(2)
    app._session = current_session
    app.set_status("\u7a7a\u95f2")

    asyncio.run(app._handle_worker_exit(1, 7))

    assert app._session is current_session
    assert app._status == "\u7a7a\u95f2"


def test_ensure_worker_timeout_terminates_process_before_dispose(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
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


def test_terminate_worker_waits_for_killed_process(monkeypatch: pytest.MonkeyPatch):
    app = _build_app(monkeypatch)
    session = _build_session(11)
    wait_for_calls: list[int] = []
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
    assert wait_for_calls == [2, 2]
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
