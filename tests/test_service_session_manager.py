from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from doubaoime_asr.agent.config import AgentConfig
from doubaoime_asr.agent.events import (
    FallbackRequiredEvent,
    FinalResultEvent,
    FinishedEvent,
    InterimResultEvent,
    ServiceResolvedFinalEvent,
    WorkerStatusEvent,
)
from doubaoime_asr.agent.service_protocol import encode_service_event
from doubaoime_asr.agent.service_session_manager import ServiceSessionManager
from doubaoime_asr.agent.session_manager import WorkerSession, WorkerSessionState


class _FakeProcess:
    def __init__(self, *, pid: int = 1234, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.stdin_writes: list[bytes] = []
        self.stdin = SimpleNamespace(
            write=lambda data: self.stdin_writes.append(data),
            drain=lambda: asyncio.sleep(0),
            close=lambda: None,
            wait_closed=lambda: asyncio.sleep(0),
        )
        self.stdout = None
        self.stderr = None

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.returncode = 9


def _make_config() -> AgentConfig:
    return AgentConfig()


def _make_logger() -> logging.Logger:
    return logging.getLogger("service-session-manager-test")


def test_service_session_manager_maps_service_ready() -> None:
    manager = ServiceSessionManager(_make_config(), _make_logger())
    process = _FakeProcess()
    session = WorkerSession(session_id=1, process=process, state=WorkerSessionState.STARTING)

    event = manager._map_service_message_to_event(  # type: ignore[attr-defined]
        __import__("doubaoime_asr.agent.service_protocol", fromlist=["decode_service_message"]).decode_service_message(
            encode_service_event("service_ready")
        ),
        session,
    )

    assert isinstance(event, WorkerStatusEvent)
    assert session.process_ready is True
    assert session.state == WorkerSessionState.READY


def test_service_session_manager_maps_interim_and_final_raw() -> None:
    manager = ServiceSessionManager(_make_config(), _make_logger())
    session = WorkerSession(session_id=1, process=_FakeProcess(), state=WorkerSessionState.STREAMING)
    decode_service_message = __import__(
        "doubaoime_asr.agent.service_protocol", fromlist=["decode_service_message"]
    ).decode_service_message

    interim = manager._map_service_message_to_event(  # type: ignore[attr-defined]
        decode_service_message(encode_service_event("interim", text="你好", segment_index=2)),
        session,
    )
    final_raw = manager._map_service_message_to_event(  # type: ignore[attr-defined]
        decode_service_message(encode_service_event("final_raw", text="最终文本", segment_index=3)),
        session,
    )

    assert isinstance(interim, InterimResultEvent)
    assert interim.text == "你好"
    assert interim.segment_index == 2
    assert isinstance(final_raw, FinalResultEvent)
    assert final_raw.text == "最终文本"
    assert final_raw.segment_index == 3


def test_service_session_manager_maps_worker_finished_status() -> None:
    manager = ServiceSessionManager(_make_config(), _make_logger())
    session = WorkerSession(session_id=1, process=_FakeProcess(), state=WorkerSessionState.STOPPING)
    session.stop_sent_at = 1.0
    decode_service_message = __import__(
        "doubaoime_asr.agent.service_protocol", fromlist=["decode_service_message"]
    ).decode_service_message

    finished = manager._map_service_message_to_event(  # type: ignore[attr-defined]
        decode_service_message(encode_service_event("status", code="worker_finished", message="done")),
        session,
    )

    assert isinstance(finished, FinishedEvent)
    assert session.state == WorkerSessionState.READY


def test_service_session_manager_maps_final_resolved_and_fallback_required() -> None:
    manager = ServiceSessionManager(_make_config(), _make_logger())
    session = WorkerSession(session_id=1, process=_FakeProcess(), state=WorkerSessionState.STREAMING)
    decode_service_message = __import__(
        "doubaoime_asr.agent.service_protocol", fromlist=["decode_service_message"]
    ).decode_service_message

    final_resolved = manager._map_service_message_to_event(  # type: ignore[attr-defined]
        decode_service_message(
            encode_service_event(
                "final_resolved",
                text="最终文本",
                raw_text="原文",
                applied_mode="light",
                fallback_reason=None,
                committed_source="polished",
            )
        ),
        session,
    )
    fallback_required = manager._map_service_message_to_event(  # type: ignore[attr-defined]
        decode_service_message(
            encode_service_event("fallback_required", reason="timeout", source="text_polisher")
        ),
        session,
    )

    assert isinstance(final_resolved, ServiceResolvedFinalEvent)
    assert final_resolved.text == "最终文本"
    assert final_resolved.committed_source == "polished"
    assert isinstance(fallback_required, FallbackRequiredEvent)
    assert fallback_required.reason == "timeout"


def test_service_session_manager_send_command_encodes_protocol() -> None:
    manager = ServiceSessionManager(_make_config(), _make_logger())
    process = _FakeProcess()
    session = WorkerSession(session_id=7, process=process, state=WorkerSessionState.READY)
    manager._session = session

    asyncio.run(manager.send_command("START"))

    raw = process.stdin_writes[0].decode("utf-8").strip()
    assert '"kind": "command"' in raw
    assert '"name": "start"' in raw
    assert '"session_id": "7"' in raw
