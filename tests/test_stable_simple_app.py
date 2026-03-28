from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

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
