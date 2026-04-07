from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Literal

from .composition import CompositionSession
from .config import AgentConfig
from .input_injector import FocusTarget


Mode = Literal["recognize", "inject"]


class WorkerSession:
    """稳定版 compat WorkerSession 包装。"""

    def __init__(
        self,
        session_id: int,
        process: Any = None,
        stdout_task: Any = None,
        stderr_task: Any = None,
        wait_task: Any = None,
    ) -> None:
        from .session_manager import WorkerSession as _RealSession

        self._real = _RealSession(session_id=session_id, process=process)
        self._real.stdout_task = stdout_task
        self._real.stderr_task = stderr_task
        self._real.wait_task = wait_task
        self._composition: Any = None
        self._inline_streaming_enabled = False
        self._final_injection_blocked = False

    @property
    def session_id(self) -> int:
        return self._real.session_id

    @property
    def process(self) -> Any:
        return self._real.process

    @process.setter
    def process(self, value: Any) -> None:
        self._real.process = value

    @property
    def active(self) -> bool:
        from .session_manager import WorkerSessionState

        return self._real.state == WorkerSessionState.STREAMING

    @property
    def state(self) -> Any:
        return self._real.state

    @property
    def process_ready(self) -> bool:
        return self._real.process_ready

    @property
    def stop_sent(self) -> bool:
        return self._real.stop_sent

    @property
    def pending_stop(self) -> bool:
        return self._real.pending_stop

    @property
    def ready(self) -> bool:
        return self._real.ready

    @property
    def streaming_started(self) -> bool:
        return self._real.streaming_started

    @property
    def target(self) -> FocusTarget | None:
        return self._real.target

    @property
    def mode(self) -> Mode:
        return self._real.mode

    @property
    def composition(self) -> Any:
        return self._composition

    @property
    def inline_streaming_enabled(self) -> bool:
        return self._inline_streaming_enabled

    @property
    def final_injection_blocked(self) -> bool:
        return self._final_injection_blocked

    @property
    def segment_texts(self) -> dict[int, str]:
        return self._real.segment_texts

    @property
    def finalized_segment_indexes(self) -> set[int]:
        return self._real.finalized_segment_indexes

    @property
    def active_segment_index(self) -> int | None:
        return self._real.active_segment_index

    @property
    def last_displayed_raw_final_text(self) -> str:
        return getattr(self._real, "last_displayed_raw_final_text", "")

    @property
    def stdout_task(self) -> Any:
        return self._real.stdout_task

    @property
    def stderr_task(self) -> Any:
        return self._real.stderr_task

    @property
    def wait_task(self) -> Any:
        return self._real.wait_task

    @active.setter
    def active(self, value: bool) -> None:
        from .session_manager import WorkerSessionState

        if value:
            self._real.transition_to(WorkerSessionState.STREAMING)
        else:
            self._real.transition_to(WorkerSessionState.READY)

    @process_ready.setter
    def process_ready(self, value: bool) -> None:
        self._real.process_ready = value

    @stop_sent.setter
    def stop_sent(self, value: bool) -> None:
        self._real.stop_sent = value

    @pending_stop.setter
    def pending_stop(self, value: bool) -> None:
        self._real.pending_stop = value

    @ready.setter
    def ready(self, value: bool) -> None:
        self._real.ready = value

    @streaming_started.setter
    def streaming_started(self, value: bool) -> None:
        self._real.streaming_started = value

    @target.setter
    def target(self, value: FocusTarget | None) -> None:
        self._real.target = value

    @mode.setter
    def mode(self, value: Mode) -> None:
        self._real.mode = value

    @composition.setter
    def composition(self, value: Any) -> None:
        self._composition = value

    @inline_streaming_enabled.setter
    def inline_streaming_enabled(self, value: bool) -> None:
        self._inline_streaming_enabled = value

    @final_injection_blocked.setter
    def final_injection_blocked(self, value: bool) -> None:
        self._final_injection_blocked = value

    @active_segment_index.setter
    def active_segment_index(self, value: int | None) -> None:
        self._real.active_segment_index = value

    @last_displayed_raw_final_text.setter
    def last_displayed_raw_final_text(self, value: str) -> None:
        setattr(self._real, "last_displayed_raw_final_text", value)

    @stdout_task.setter
    def stdout_task(self, value: Any) -> None:
        self._real.stdout_task = value

    @stderr_task.setter
    def stderr_task(self, value: Any) -> None:
        self._real.stderr_task = value

    @wait_task.setter
    def wait_task(self, value: Any) -> None:
        self._real.wait_task = value

    def begin(
        self,
        target: FocusTarget | None,
        mode: Mode,
        *,
        composition: Any = None,
        inline_streaming_enabled: bool = False,
    ) -> None:
        self._real.begin(target, mode)
        self._composition = composition
        self._inline_streaming_enabled = inline_streaming_enabled
        self._final_injection_blocked = False

    def clear_active(self) -> None:
        self._real.clear_active()
        self._composition = None
        self._inline_streaming_enabled = False
        self._final_injection_blocked = False


class _InjectionManagerCompat:
    """注入管理器 compat 包装。"""

    def __init__(self, injection_service: Any) -> None:
        self._service = injection_service

    @property
    def policy(self) -> str:
        return self._service.get_injection_policy()

    @policy.setter
    def policy(self, value: str) -> None:
        if hasattr(self._service, "_manager") and hasattr(self._service._manager, "policy"):
            self._service._manager.policy = value

    @property
    def injector(self) -> Any:
        return self._service._manager.injector

    @property
    def captured_target(self) -> FocusTarget | None:
        return getattr(self._service._manager, "captured_target", None)

    @captured_target.setter
    def captured_target(self, value: FocusTarget | None) -> None:
        if hasattr(self._service._manager, "captured_target"):
            self._service._manager.captured_target = value

    def set_policy(self, policy: str) -> None:
        self._service.configure(self._service._config)

    def capture_target(self) -> FocusTarget | None:
        return self._service.capture_target()

    async def inject_text(self, target: FocusTarget, text: str) -> Any:
        self._service.begin_session(target, "inject")
        result = await self._service.inject_final(text)
        if result is None:
            return SimpleNamespace(
                method="direct",
                target_profile="editor",
                clipboard_touched=False,
                restored_clipboard=False,
            )
        return result


class _PreviewCompat:
    """浮层预览 compat 包装。"""

    def __init__(self, overlay_service: Any) -> None:
        self._service = overlay_service
        self.configured: list[AgentConfig] = []

    def configure(self, config: AgentConfig) -> None:
        self.configured.append(config)
        self._service.configure(config)

    def start(self) -> None:
        self._service.start()

    def stop(self) -> None:
        self._service.stop()


class _SchedulerCompat:
    """调度器 compat 包装。"""

    def __init__(self, service: Any, *, raw_scheduler: bool = False) -> None:
        self._service = service
        self._raw = raw_scheduler
        self.configured: list[AgentConfig] = []
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def configure(self, config: AgentConfig) -> None:
        self.configured.append(config)
        if hasattr(self._service, "configure"):
            self._service.configure(config)

    async def submit_interim(self, text: str) -> None:
        self.calls.append(("interim", (text,)))
        await self._service.submit_interim(text)

    async def submit_final(self, text: str, *, kind: str) -> None:
        self.calls.append(("final", (text, kind)))
        await self._service.submit_final(text, kind=kind)

    async def show_microphone(self, placeholder_text: str = "正在聆听…") -> None:
        self.calls.append(("microphone", (placeholder_text,)))
        await self._service.show_microphone(placeholder_text)

    async def update_microphone_level(self, level: float) -> None:
        self.calls.append(("audio_level", (level,)))
        await self._service.update_microphone_level(level)

    async def stop_microphone(self) -> None:
        self.calls.append(("stop_microphone", ()))
        await self._service.stop_microphone()

    async def hide(self, reason: str) -> None:
        self.calls.append(("hide", (reason,)))
        await self._service.hide(reason)


class _SessionCompat:
    """SessionManager session compat 包装。"""

    def __init__(self, session_manager: Any) -> None:
        self._manager = session_manager

    @property
    def session_id(self) -> int:
        session = self._manager.get_session()
        return session.session_id if session else 0

    @property
    def active(self) -> bool:
        return self._manager.is_streaming()

    @property
    def process(self) -> Any:
        session = self._manager.get_session()
        return session.process if session else None

    @property
    def process_ready(self) -> bool:
        session = self._manager.get_session()
        return session.process_ready if session else False

    @property
    def stop_sent(self) -> bool:
        session = self._manager.get_session()
        return session.stop_sent if session else False

    @property
    def pending_stop(self) -> bool:
        session = self._manager.get_session()
        return session.pending_stop if session else False

    @property
    def ready(self) -> bool:
        session = self._manager.get_session()
        return session.ready if session else False

    @property
    def streaming_started(self) -> bool:
        session = self._manager.get_session()
        return session.streaming_started if session else False

    @property
    def target(self) -> FocusTarget | None:
        session = self._manager.get_session()
        return session.target if session else None

    @property
    def mode(self) -> Mode:
        session = self._manager.get_session()
        return session.mode if session else "inject"

    @property
    def composition(self) -> Any:
        session = self._manager.get_session()
        return getattr(session, "composition", None) if session else None

    @property
    def inline_streaming_enabled(self) -> bool:
        session = self._manager.get_session()
        return bool(getattr(session, "inline_streaming_enabled", False)) if session else False

    @property
    def final_injection_blocked(self) -> bool:
        session = self._manager.get_session()
        return bool(getattr(session, "final_injection_blocked", False)) if session else False

    @staticmethod
    def wrap(session: Any) -> "_SessionCompatWrapper":
        return _SessionCompatWrapper(session)


class _SessionCompatWrapper(_SessionCompat):
    """独立 runtime session compat 包装。"""

    def __init__(self, session: Any) -> None:
        self._session = session
        self._composition = getattr(session, "composition", None)
        self._inline_streaming_enabled = bool(
            getattr(session, "inline_streaming_enabled", False)
        )
        self._final_injection_blocked = bool(
            getattr(session, "final_injection_blocked", False)
        )

    @property
    def session_id(self) -> int:
        return self._session.session_id

    @property
    def active(self) -> bool:
        from .session_manager import WorkerSessionState

        return self._session.state == WorkerSessionState.STREAMING

    @property
    def process(self) -> Any:
        return self._session.process

    @property
    def process_ready(self) -> bool:
        return self._session.process_ready

    @property
    def stop_sent(self) -> bool:
        return self._session.stop_sent

    @property
    def pending_stop(self) -> bool:
        return self._session.pending_stop

    @property
    def ready(self) -> bool:
        return self._session.ready

    @property
    def streaming_started(self) -> bool:
        return self._session.streaming_started

    @property
    def target(self) -> FocusTarget | None:
        return self._session.target

    @property
    def mode(self) -> Mode:
        return self._session.mode

    @property
    def composition(self) -> Any:
        return getattr(self._session, "composition", self._composition)

    @property
    def inline_streaming_enabled(self) -> bool:
        return bool(
            getattr(
                self._session,
                "inline_streaming_enabled",
                self._inline_streaming_enabled,
            )
        )

    @property
    def final_injection_blocked(self) -> bool:
        return bool(
            getattr(
                self._session,
                "final_injection_blocked",
                self._final_injection_blocked,
            )
        )

    @property
    def stdout_task(self) -> Any:
        return self._session.stdout_task

    @property
    def stderr_task(self) -> Any:
        return self._session.stderr_task

    @property
    def wait_task(self) -> Any:
        return self._session.wait_task

    def begin(
        self,
        target: FocusTarget | None,
        mode: Mode,
        *,
        composition: Any = None,
        inline_streaming_enabled: bool = False,
    ) -> None:
        self._session.begin(target, mode)
        self._composition = composition
        self._inline_streaming_enabled = inline_streaming_enabled
        self._final_injection_blocked = False


def resolve_session_owner(session: Any) -> Any:
    """解包 compat/runtime session，返回真实 session owner。"""
    return getattr(session, "_real", None) or getattr(session, "_session", None) or session


def get_managed_session(
    session_manager: Any,
    *,
    test_session: Any,
    unset: object,
) -> Any:
    """统一读取 app 当前会话，兼容 test override 与 SessionManager。"""
    if test_session is unset:
        session = session_manager.get_session()
        if session is None:
            return None
        return _SessionCompat(session_manager)
    return test_session


def sync_managed_session(session_manager: Any, value: Any) -> None:
    """将 app._session 写回 SessionManager 的真实 owner。"""
    if value is None:
        session_manager._session = None
        return
    session_manager._session = resolve_session_owner(value)


class _ListenerCompat:
    """热键监听 compat 包装。"""

    def __init__(self, hotkey_service: Any, vk: int) -> None:
        self._service = hotkey_service
        self._vk = vk

    def start(self) -> None:
        self._service.start(self._vk)

    def stop(self) -> None:
        self._service.stop()


def _convert_legacy_event(kind: str, payload: object) -> Any:
    """旧 tuple 风格事件 -> 新类型化事件。"""
    from .events import (
        ConfigChangeEvent,
        HotkeyPressEvent,
        HotkeyReleaseEvent,
        RestartAsAdminEvent,
        StopEvent,
        WorkerExitEvent,
        parse_worker_event,
    )

    if kind == "press":
        return HotkeyPressEvent()
    if kind == "release":
        return HotkeyReleaseEvent()
    if kind == "apply_config":
        return ConfigChangeEvent(config=payload)
    if kind == "restart_as_admin":
        return RestartAsAdminEvent()
    if kind == "stop":
        return StopEvent()
    if kind == "worker_exit" and isinstance(payload, tuple) and len(payload) == 2:
        return WorkerExitEvent(session_id=int(payload[0]), exit_code=int(payload[1]))
    if kind == "worker_event" and isinstance(payload, tuple) and len(payload) == 2:
        event_data = payload[1]
        if isinstance(event_data, dict):
            return parse_worker_event(event_data)
    return None


__all__ = [
    "Mode",
    "WorkerSession",
    "get_managed_session",
    "resolve_session_owner",
    "sync_managed_session",
    "_InjectionManagerCompat",
    "_PreviewCompat",
    "_SchedulerCompat",
    "_SessionCompat",
    "_SessionCompatWrapper",
    "_ListenerCompat",
    "_convert_legacy_event",
    "CompositionSession",
]
