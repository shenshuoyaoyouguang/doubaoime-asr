"""
SessionManager 单元测试。
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
import types

import pytest

from doubaoime_asr.agent.config import AgentConfig
from doubaoime_asr.agent.events import (
    WorkerExitEvent,
    WorkerReadyEvent,
    ReadyEvent,
    StreamingStartedEvent,
    FinishedEvent,
    VoiceInputEvent,
)
from doubaoime_asr.agent.session_manager import (
    WorkerSessionState,
    WorkerSession,
    SessionManager,
)
from doubaoime_asr.agent.input_injector import FocusTarget


def _make_logger() -> logging.Logger:
    return logging.getLogger("session-manager-test")


def _make_config() -> AgentConfig:
    return AgentConfig()


def _make_target(hwnd: int = 1) -> FocusTarget:
    return FocusTarget(hwnd=hwnd, is_terminal=False)


class _FakeProcess:
    """模拟 Worker 进程。"""

    def __init__(self, *, pid: int = 1234, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.stdin = SimpleNamespace(write=lambda data: None, drain=lambda: asyncio.sleep(0))
        self.stdout = None
        self.stderr = None
        self.wait_called = False
        self.kill_called = False

    async def wait(self) -> int:
        self.wait_called = True
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = 9


class TestWorkerSessionState:
    """测试 WorkerSessionState 状态机。"""

    def test_state_enum_values(self):
        """验证状态枚举值。"""
        assert WorkerSessionState.IDLE.value == "idle"
        assert WorkerSessionState.STARTING.value == "starting"
        assert WorkerSessionState.READY.value == "ready"
        assert WorkerSessionState.STREAMING.value == "streaming"
        assert WorkerSessionState.STOPPING.value == "stopping"
        assert WorkerSessionState.TERMINATING.value == "terminating"

    def test_state_count(self):
        """验证状态数量。"""
        assert len(WorkerSessionState) == 6


class TestWorkerSession:
    """测试 WorkerSession 数据类。"""

    def test_session_creation(self):
        """测试会话创建。"""
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        assert session.session_id == 1
        assert session.state == WorkerSessionState.IDLE
        assert session.process_ready is False
        assert session.target is None
        assert session.mode == "inject"

    def test_transition_to(self):
        """测试状态转换。"""
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.transition_to(WorkerSessionState.STARTING)
        assert session.state == WorkerSessionState.STARTING
        session.transition_to(WorkerSessionState.READY)
        assert session.state == WorkerSessionState.READY

    def test_begin_session(self):
        """测试开始会话。"""
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        target = _make_target(hwnd=100)
        session.begin(target, "inject")
        assert session.target == target
        assert session.mode == "inject"
        assert session.state == WorkerSessionState.STREAMING
        assert session.stop_sent is False
        assert session.ready is False
        assert session.streaming_started is False

    def test_clear_active(self):
        """测试清除活跃状态。"""
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        target = _make_target(hwnd=100)
        session.begin(target, "inject")
        session.ready = True
        session.streaming_started = True
        session.clear_active()
        assert session.target is None
        assert session.mode == "inject"
        assert session.state == WorkerSessionState.READY
        assert session.stop_sent is False
        assert session.ready is False
        assert session.streaming_started is False

    def test_clear_active_from_idle_state(self):
        """从 IDLE 状态清除。"""
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.state = WorkerSessionState.IDLE
        session.clear_active()
        assert session.state == WorkerSessionState.IDLE

    def test_mark_stop_sent(self):
        """测试标记停止发送。"""
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.begin(_make_target(), "inject")
        session.pending_stop = True
        session.mark_stop_sent()
        assert session.stop_sent is True
        assert session.pending_stop is False
        assert session.state == WorkerSessionState.STOPPING
        assert session.stop_sent_at is not None
        assert session.finished_at is None

    def test_mark_finished(self):
        """测试标记完成。"""
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.begin(_make_target(), "inject")
        session.mark_finished()
        assert session.state == WorkerSessionState.READY
        assert session.target is None

    def test_segment_text_management(self):
        """测试分段文本管理。"""
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.segment_texts[0] = "第一句"
        session.segment_texts[1] = "第二句"
        session.finalized_segment_indexes.add(0)
        session.active_segment_index = 1
        assert len(session.segment_texts) == 2
        assert 0 in session.finalized_segment_indexes
        assert session.active_segment_index == 1


class TestSessionManager:
    """测试 SessionManager 类。"""

    def test_init(self):
        """测试初始化。"""
        config = _make_config()
        logger = _make_logger()
        manager = SessionManager(config, logger)
        assert manager.config == config
        assert manager.logger == logger
        assert manager._session is None
        assert manager._next_session_id == 0
        assert manager.is_stopping() is False

    def test_init_with_event_handler(self):
        """测试带事件处理器的初始化。"""
        config = _make_config()
        logger = _make_logger()
        events: list[VoiceInputEvent] = []

        def on_event(event: VoiceInputEvent) -> None:
            events.append(event)

        manager = SessionManager(config, logger, on_event=on_event)
        assert manager._on_event is not None

    def test_is_active_returns_false_when_no_session(self):
        """无会话时 is_active 返回 False。"""
        manager = SessionManager(_make_config(), _make_logger())
        assert manager.is_active() is False

    def test_is_ready_returns_false_when_no_session(self):
        """无会话时 is_ready 返回 False。"""
        manager = SessionManager(_make_config(), _make_logger())
        assert manager.is_ready() is False

    def test_is_streaming_returns_false_when_no_session(self):
        """无会话时 is_streaming 返回 False。"""
        manager = SessionManager(_make_config(), _make_logger())
        assert manager.is_streaming() is False

    def test_get_session_returns_none_when_no_session(self):
        """无会话时 get_session 返回 None。"""
        manager = SessionManager(_make_config(), _make_logger())
        assert manager.get_session() is None

    def test_get_state_returns_idle_when_no_session(self):
        """无会话时 get_state 返回 IDLE。"""
        manager = SessionManager(_make_config(), _make_logger())
        assert manager.get_state() == WorkerSessionState.IDLE

    def test_stop_sets_stopping_flag(self):
        """stop 设置停止标志。"""
        manager = SessionManager(_make_config(), _make_logger())
        manager.stop()
        assert manager.is_stopping() is True

    def test_begin_session_raises_when_no_worker(self):
        """无 Worker 时 begin_session 抛出异常。"""
        manager = SessionManager(_make_config(), _make_logger())
        with pytest.raises(RuntimeError, match="worker process not ready"):
            manager.begin_session(_make_target(), "inject")

    def test_clear_session_safe_when_no_worker(self):
        """无 Worker 时 clear_session 安全返回。"""
        manager = SessionManager(_make_config(), _make_logger())
        manager.clear_session()  # 应该安全返回
        assert manager.get_session() is None

    def test_send_command_raises_when_no_worker(self):
        """无 Worker 时 send_command 抛出异常。"""
        manager = SessionManager(_make_config(), _make_logger())
        with pytest.raises(RuntimeError, match="worker process is not running"):
            asyncio.run(manager.send_command("START"))

    def test_send_stop_safe_when_no_worker(self):
        """无 Worker 时 send_stop 安全返回。"""
        manager = SessionManager(_make_config(), _make_logger())
        asyncio.run(manager.send_stop())  # 应该安全返回

    def test_handle_worker_event_returns_none_when_no_session(self):
        """无会话时 handle_worker_event 返回 None。"""
        manager = SessionManager(_make_config(), _make_logger())
        result = manager.handle_worker_event({"type": "ready"})
        assert result is None

    def test_handle_worker_exit_ignores_stale_session(self):
        """处理过期会话的退出事件。"""
        manager = SessionManager(_make_config(), _make_logger())
        # 创建一个会话
        process = _FakeProcess()
        session = WorkerSession(session_id=2, process=process)
        manager._session = session
        # 发送不同 session_id 的退出事件
        manager.handle_worker_exit(1, 0)
        assert manager._session == session  # 会话未被清除

    def test_handle_worker_exit_updates_state(self):
        """处理当前会话的退出事件。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.state = WorkerSessionState.READY
        manager._session = session
        manager.handle_worker_exit(1, 0)
        assert session.state == WorkerSessionState.IDLE


class TestSessionManagerEvents:
    """测试 SessionManager 事件处理。"""

    def test_handle_worker_ready_event(self):
        """处理 worker_ready 事件。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.state = WorkerSessionState.STARTING
        manager._session = session

        result = manager.handle_worker_event({"type": "worker_ready"})
        assert isinstance(result, WorkerReadyEvent)
        assert session.process_ready is True
        assert session.state == WorkerSessionState.READY

    def test_handle_ready_event(self):
        """处理 ready 事件。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.state = WorkerSessionState.STREAMING
        manager._session = session

        result = manager.handle_worker_event({"type": "ready"})
        assert isinstance(result, ReadyEvent)
        assert session.ready is True

    def test_handle_streaming_started_event(self):
        """处理 streaming_started 事件。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.state = WorkerSessionState.STREAMING
        manager._session = session

        result = manager.handle_worker_event({"type": "streaming_started", "chunks": 5, "bytes": 1000})
        assert isinstance(result, StreamingStartedEvent)
        assert session.streaming_started is True

    def test_handle_finished_event(self):
        """处理 finished 事件。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.begin(_make_target(), "inject")
        session.mark_stop_sent()
        manager._session = session

        result = manager.handle_worker_event({"type": "finished"})
        assert isinstance(result, FinishedEvent)
        assert session.state == WorkerSessionState.READY
        assert session.stop_sent_at is not None
        assert session.finished_at is not None
        assert session.finished_at >= session.stop_sent_at

    def test_handle_worker_event_returns_parsed_event(self):
        """handle_worker_event 返回解析后的事件对象。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.state = WorkerSessionState.STREAMING
        manager._session = session

        result = manager.handle_worker_event({"type": "ready"})
        assert result is not None
        assert isinstance(result, ReadyEvent)

    @pytest.mark.asyncio
    async def test_handle_worker_event_callback_in_read_stdout(self):
        """测试事件回调在 _read_worker_stdout 中被调用。"""
        events: list[VoiceInputEvent] = []

        def on_event(event: VoiceInputEvent) -> None:
            events.append(event)

        manager = SessionManager(_make_config(), _make_logger(), on_event=on_event)
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.state = WorkerSessionState.READY
        manager._session = session
        manager._loop = asyncio.get_running_loop()

        # 创建一个模拟的 stdout stream
        from unittest.mock import AsyncMock, MagicMock

        # 模拟 stream.readline 返回事件行
        mock_stream = MagicMock()
        mock_stream.readline = AsyncMock(side_effect=[
            b'{"type": "ready"}\n',
            b'',  # EOF
        ])

        await manager._read_worker_stdout(mock_stream, session)

        assert len(events) >= 1
        assert isinstance(events[0], ReadyEvent)


class TestSessionManagerProcessManagement:
    """测试进程管理。"""

    @pytest.mark.asyncio
    async def test_terminate_worker_with_no_session(self):
        """无会话时 terminate_worker 安全返回。"""
        manager = SessionManager(_make_config(), _make_logger())
        await manager.terminate_worker()
        assert manager._session is None

    @pytest.mark.asyncio
    async def test_restart_worker_when_stopping(self):
        """停止状态下 restart_worker 不启动。"""
        manager = SessionManager(_make_config(), _make_logger())
        manager.stop()

        # Mock ensure_worker to track calls
        ensure_calls = []
        original_ensure = manager.ensure_worker

        async def fake_ensure():
            ensure_calls.append(True)
            process = _FakeProcess(returncode=None)
            session = WorkerSession(session_id=1, process=process)
            session.process_ready = True
            manager._session = session
            return session

        manager.ensure_worker = fake_ensure

        await manager.restart_worker()
        assert len(ensure_calls) == 0  # stopping 状态下不调用


class TestSessionManagerStateQueries:
    """测试状态查询方法。"""

    def test_is_active_true_when_streaming(self):
        """STREAMING 状态时 is_active 返回 True。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.state = WorkerSessionState.STREAMING
        manager._session = session
        assert manager.is_active() is True

    def test_is_active_false_when_ready(self):
        """READY 状态时 is_active 返回 False。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.state = WorkerSessionState.READY
        manager._session = session
        assert manager.is_active() is False

    def test_is_ready_true_when_ready_state(self):
        """READY 状态时 is_ready 返回 True。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.state = WorkerSessionState.READY
        manager._session = session
        assert manager.is_ready() is True

    def test_is_streaming_true_when_streaming_state(self):
        """STREAMING 状态时 is_streaming 返回 True。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.state = WorkerSessionState.STREAMING
        manager._session = session
        assert manager.is_streaming() is True


class TestSessionManagerCommands:
    """测试命令发送。"""

    @pytest.mark.asyncio
    async def test_send_command_updates_session_state(self):
        """发送 START 命令不改变状态（状态由 begin_session 管理）。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.state = WorkerSessionState.READY
        manager._session = session

        # send_command 只发送命令，不改变状态
        await manager.send_command("START")
        assert session.state == WorkerSessionState.READY  # 状态不变

    @pytest.mark.asyncio
    async def test_send_stop_marks_stop_sent(self):
        """发送 STOP 命令标记 stop_sent。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.begin(_make_target(), "inject")
        manager._session = session

        await manager.send_stop()
        assert session.stop_sent is True
        assert session.state == WorkerSessionState.STOPPING


class TestWorkerSessionStateTransitions:
    """测试完整状态转换流程。"""

    def test_full_lifecycle_transitions(self):
        """测试完整生命周期状态转换。"""
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)

        # IDLE -> STARTING
        session.transition_to(WorkerSessionState.STARTING)
        assert session.state == WorkerSessionState.STARTING

        # STARTING -> READY (worker_ready)
        session.process_ready = True
        session.transition_to(WorkerSessionState.READY)
        assert session.state == WorkerSessionState.READY

        # READY -> STREAMING (begin session)
        session.begin(_make_target(), "inject")
        assert session.state == WorkerSessionState.STREAMING

        # STREAMING -> STOPPING (send STOP)
        session.mark_stop_sent()
        assert session.state == WorkerSessionState.STOPPING

        # STOPPING -> READY (finished)
        session.mark_finished()
        assert session.state == WorkerSessionState.READY

    def test_terminate_from_streaming(self):
        """从 STREAMING 状态终止。"""
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        session.begin(_make_target(), "inject")
        session.transition_to(WorkerSessionState.TERMINATING)
        assert session.state == WorkerSessionState.TERMINATING


class TestSessionManagerInternalMethods:
    """测试内部方法。"""

    def test_build_worker_env(self):
        """测试构建 Worker 环境变量。"""
        manager = SessionManager(_make_config(), _make_logger())
        env = manager._build_worker_env()
        assert "PYTHONIOENCODING" in env
        assert env["PYTHONIOENCODING"] == "utf-8"

    def test_build_worker_command_frozen(self):
        """测试构建 frozen 模式 Worker 命令。"""
        import sys
        original_frozen = getattr(sys, "frozen", False)
        try:
            sys.frozen = True  # type: ignore
            config = AgentConfig(credential_path="test.json", microphone_device=1)
            manager = SessionManager(config, _make_logger())
            cmd = manager._build_worker_command()
            assert sys.executable in cmd
            assert "--worker" in cmd
            assert "--credential-path" in cmd
            assert "--mic-device" in cmd
        finally:
            if not original_frozen:
                delattr(sys, "frozen")

    def test_build_worker_command_unfrozen(self):
        """测试构建普通模式 Worker 命令。"""
        import sys
        original_frozen = getattr(sys, "frozen", False)
        try:
            if hasattr(sys, "frozen"):
                delattr(sys, "frozen")
            config = AgentConfig(credential_path="test.json")
            manager = SessionManager(config, _make_logger())
            cmd = manager._build_worker_command()
            assert "-m" in cmd
            assert "doubaoime_asr.agent.stable_main" in cmd
            assert "--worker" in cmd
        finally:
            if original_frozen:
                sys.frozen = original_frozen  # type: ignore

    @pytest.mark.asyncio
    async def test_dispose_worker_with_no_session(self):
        """无会话时 _dispose_worker 安全返回。"""
        manager = SessionManager(_make_config(), _make_logger())
        await manager._dispose_worker()
        assert manager._session is None

    @pytest.mark.asyncio
    async def test_dispose_worker_cancels_tasks(self):
        """测试 _dispose_worker 取消任务。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()

        async def fake_task():
            await asyncio.sleep(10)

        session = WorkerSession(session_id=1, process=process)
        session.stdout_task = asyncio.create_task(fake_task())
        session.stderr_task = asyncio.create_task(fake_task())
        session.wait_task = asyncio.create_task(fake_task())
        manager._session = session

        await manager._dispose_worker()
        assert manager._session is None
        assert session.stdout_task.cancelled() or session.stdout_task.done()
        assert session.stderr_task.cancelled() or session.stderr_task.done()

    @pytest.mark.asyncio
    async def test_read_worker_stderr(self):
        """测试读取 stderr。"""
        from unittest.mock import AsyncMock, MagicMock

        manager = SessionManager(_make_config(), _make_logger())

        mock_stream = MagicMock()
        mock_stream.readline = AsyncMock(side_effect=[
            b"error message\n",
            b"",  # EOF
        ])

        await manager._read_worker_stderr(mock_stream)
        # 应该正常完成

    @pytest.mark.asyncio
    async def test_read_worker_stdout_with_invalid_json(self):
        """测试读取 stdout 无效 JSON。"""
        from unittest.mock import AsyncMock, MagicMock

        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        manager._session = session

        mock_stream = MagicMock()
        mock_stream.readline = AsyncMock(side_effect=[
            b"invalid json line\n",
            b"",  # EOF
        ])

        await manager._read_worker_stdout(mock_stream, session)
        # 应该正常完成，无效行被忽略

    @pytest.mark.asyncio
    async def test_terminate_session_process_sends_exit(self):
        """测试终止进程发送 EXIT 命令。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        session = WorkerSession(session_id=1, process=process)
        manager._session = session

        commands_sent = []

        async def track_send_command(cmd: str):
            commands_sent.append(cmd)

        manager.send_command = track_send_command

        await manager._terminate_session_process(session)
        assert "EXIT" in commands_sent

    @pytest.mark.asyncio
    async def test_terminate_session_process_kills_on_timeout(self):
        """测试超时后强制终止进程。"""
        manager = SessionManager(_make_config(), _make_logger())
        process = _FakeProcess()
        process.returncode = None  # 进程不会自然退出
        session = WorkerSession(session_id=1, process=process)
        manager._session = session

        async def fake_wait_for(awaitable, timeout):
            if timeout == 2:
                close = getattr(awaitable, "close", None)
                if callable(close):
                    close()
                raise asyncio.TimeoutError()
            return await awaitable

        with pytest.MonkeyPatch.context() as m:
            m.setattr(asyncio, "wait_for", fake_wait_for)
            await manager._terminate_session_process(session)

        assert process.kill_called

    @pytest.mark.asyncio
    async def test_wait_worker_calls_on_event(self):
        """测试 _wait_worker 调用事件回调。"""
        events: list[VoiceInputEvent] = []

        def on_event(event: VoiceInputEvent) -> None:
            events.append(event)

        manager = SessionManager(_make_config(), _make_logger(), on_event=on_event)
        process = _FakeProcess()
        manager._loop = asyncio.get_running_loop()

        await manager._wait_worker(process, 1)

        assert len(events) == 1
        assert isinstance(events[0], WorkerExitEvent)
        assert events[0].session_id == 1
