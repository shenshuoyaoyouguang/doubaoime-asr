import asyncio
import logging

from doubaoime_asr.agent.config import AgentConfig
from doubaoime_asr.agent.service_protocol import decode_service_message, encode_service_command
from doubaoime_asr.agent.service_runtime import ServiceRuntime
from doubaoime_asr.agent.service_session_runner import ServiceSessionRunner
from doubaoime_asr.agent.text_polisher import PolishResult


def test_service_runtime_tracks_start_then_cancel() -> None:
    runtime = ServiceRuntime()

    should_exit, events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("start", session_id="s-1", timeout_ms=500))
    ))
    assert should_exit is False
    assert runtime.state.active_session_id == "s-1"
    assert events[0]["type"] == "status"
    assert events[0]["code"] == "session_start_accepted"

    should_exit, events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("cancel", session_id="s-1"))
    ))
    assert should_exit is False
    assert runtime.state.active_session_id is None
    assert events[0]["type"] == "status"
    assert events[0]["code"] == "session_canceled"


class _FakeLiveWorkerSession:
    def __init__(self, *, emit_during_start: dict[str, object] | None = None) -> None:
        self.callback = None
        self.emit_during_start = emit_during_start

    def set_event_callback(self, callback):
        self.callback = callback

    async def ensure_worker(self) -> None:
        return None

    def begin_session(self) -> None:
        return None

    async def start_session(self) -> None:
        if self.emit_during_start is not None:
            assert self.callback is not None
            self.callback(dict(self.emit_during_start))

    async def stop_session(self) -> None:
        return None

    async def terminate_worker(self) -> None:
        return None

    def emit(self, event_type: str, **payload: object) -> None:
        assert self.callback is not None
        self.callback({"type": event_type, **payload})


class _FakePolisher:
    def __init__(self, result: PolishResult) -> None:
        self.result = result

    async def polish(self, text: str) -> PolishResult:
        return PolishResult(
            text=self.result.text or text,
            applied_mode=self.result.applied_mode,
            latency_ms=self.result.latency_ms,
            fallback_reason=self.result.fallback_reason,
        )


def test_service_runtime_tracks_live_worker_stop_until_finished() -> None:
    captured_events: list[list[dict[str, object]]] = []
    worker_session = _FakeLiveWorkerSession()
    runtime = ServiceRuntime(
        emit_events=captured_events.append,
        session_runner=ServiceSessionRunner(worker_session=worker_session),
    )
    should_exit, _events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("start", session_id="s-1", timeout_ms=500))
    ))
    assert should_exit is False
    assert runtime.state.active_session_id == "s-1"

    should_exit, events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("stop", session_id="s-1"))
    ))
    assert should_exit is False
    assert runtime.state.active_session_id == "s-1"
    assert events[0]["type"] == "status"
    assert events[0]["code"] == "session_stopped_requested"

    worker_session.emit("finished")
    asyncio.run(asyncio.sleep(0))

    assert runtime.state.active_session_id is None
    flattened = [event for batch in captured_events for event in batch]
    assert not any(event["type"] == "final_resolved" for event in flattened)
    assert flattened[-1]["code"] == "session_stopped"


def test_service_runtime_clears_active_session_on_worker_exit() -> None:
    captured_events: list[list[dict[str, object]]] = []
    worker_session = _FakeLiveWorkerSession()
    runtime = ServiceRuntime(
        emit_events=captured_events.append,
        session_runner=ServiceSessionRunner(worker_session=worker_session),
    )

    should_exit, _events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("start", session_id="s-1", timeout_ms=500))
    ))
    assert should_exit is False
    assert runtime.state.active_session_id == "s-1"

    worker_session.emit("worker_exit", code=23)

    assert runtime.state.active_session_id is None
    flattened = [event for batch in captured_events for event in batch]
    assert flattened[-2]["type"] == "error"
    assert flattened[-2]["code"] == "worker_exit"
    assert flattened[-1]["code"] == "session_aborted_worker_exit"


def test_service_runtime_keeps_early_worker_event_during_start() -> None:
    captured_events: list[list[dict[str, object]]] = []
    worker_session = _FakeLiveWorkerSession(
        emit_during_start={"type": "interim", "text": "首帧", "segment_index": 1},
    )
    runtime = ServiceRuntime(
        emit_events=captured_events.append,
        session_runner=ServiceSessionRunner(worker_session=worker_session),
    )

    should_exit, events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("start", session_id="s-1", timeout_ms=500))
    ))

    assert should_exit is False
    assert events[0]["code"] == "session_start_accepted"
    flattened = [event for batch in captured_events for event in batch]
    assert flattened[0]["type"] == "interim"
    assert flattened[0]["session_id"] == "s-1"


def test_service_runtime_emits_polished_final_resolved_on_finished() -> None:
    captured_events: list[list[dict[str, object]]] = []
    worker_session = _FakeLiveWorkerSession()
    runner = ServiceSessionRunner(
        config=AgentConfig(),
        logger=logging.getLogger("service-runtime-test"),
        emit_events=captured_events.append,
        worker_session=worker_session,
    )
    runner._text_polisher = _FakePolisher(
        PolishResult(text="润色后的文本。", applied_mode="light", latency_ms=3)
    )
    runtime = ServiceRuntime(emit_events=captured_events.append, session_runner=runner)

    should_exit, _events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("start", session_id="s-1", timeout_ms=500))
    ))
    assert should_exit is False

    worker_session.emit("final", text="原文", segment_index=1)
    worker_session.emit("finished")
    asyncio.run(asyncio.sleep(0))

    flattened = [event for batch in captured_events for event in batch]
    final_resolved = next(event for event in flattened if event["type"] == "final_resolved")
    assert final_resolved["text"] == "润色后的文本。"
    assert final_resolved["committed_source"] == "polished"


def test_service_runtime_emits_raw_final_resolved_when_config_commits_raw() -> None:
    captured_events: list[list[dict[str, object]]] = []
    worker_session = _FakeLiveWorkerSession()
    config = AgentConfig(final_commit_source="raw")
    runner = ServiceSessionRunner(
        config=config,
        logger=logging.getLogger("service-runtime-test"),
        emit_events=captured_events.append,
        worker_session=worker_session,
    )
    runner._text_polisher = _FakePolisher(
        PolishResult(text="润色后的文本。", applied_mode="light", latency_ms=3)
    )
    runtime = ServiceRuntime(emit_events=captured_events.append, session_runner=runner)

    should_exit, _events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("start", session_id="s-1", timeout_ms=500))
    ))
    assert should_exit is False

    worker_session.emit("final", text="原文", segment_index=1)
    worker_session.emit("finished")
    asyncio.run(asyncio.sleep(0))

    flattened = [event for batch in captured_events for event in batch]
    final_resolved = next(event for event in flattened if event["type"] == "final_resolved")
    assert final_resolved["text"] == "原文"
    assert final_resolved["committed_source"] == "raw"


def test_service_runtime_emits_fallback_required_on_polisher_raw_fallback() -> None:
    captured_events: list[list[dict[str, object]]] = []
    worker_session = _FakeLiveWorkerSession()
    runner = ServiceSessionRunner(
        config=AgentConfig(),
        logger=logging.getLogger("service-runtime-test"),
        emit_events=captured_events.append,
        worker_session=worker_session,
    )
    runner._text_polisher = _FakePolisher(
        PolishResult(text="原文", applied_mode="raw_fallback", latency_ms=4, fallback_reason="timeout")
    )
    runtime = ServiceRuntime(emit_events=captured_events.append, session_runner=runner)

    should_exit, _events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("start", session_id="s-1", timeout_ms=500))
    ))
    assert should_exit is False

    worker_session.emit("final", text="原文", segment_index=1)
    worker_session.emit("finished")
    asyncio.run(asyncio.sleep(0))

    flattened = [event for batch in captured_events for event in batch]
    fallback_required = next(event for event in flattened if event["type"] == "fallback_required")
    assert fallback_required["reason"] == "timeout"
    final_resolved = next(event for event in flattened if event["type"] == "final_resolved")
    assert final_resolved["text"] == "原文"


def test_service_runtime_tracks_start_then_stop() -> None:
    runtime = ServiceRuntime()

    asyncio.run(runtime.handle_command(decode_service_message(encode_service_command("start", session_id="s-1"))))
    should_exit, events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("stop", session_id="s-1"))
    ))

    assert should_exit is False
    assert runtime.state.active_session_id is None
    assert events[0]["type"] == "status"
    assert events[0]["code"] == "session_stopped"


def test_service_runtime_rejects_second_session_while_busy() -> None:
    runtime = ServiceRuntime()
    asyncio.run(runtime.handle_command(decode_service_message(encode_service_command("start", session_id="s-1"))))

    should_exit, events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("start", session_id="s-2"))
    ))

    assert should_exit is False
    assert events[0]["type"] == "error"
    assert "service busy" in str(events[0]["message"])


def test_service_runtime_exit_cancels_active_session() -> None:
    runtime = ServiceRuntime()
    asyncio.run(runtime.handle_command(decode_service_message(encode_service_command("start", session_id="s-1"))))

    should_exit, events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("exit", session_id="leader"))
    ))

    assert should_exit is True
    assert runtime.state.active_session_id is None
    assert events[0]["type"] == "status"
    assert events[0]["code"] == "session_cancelled_on_exit"
    assert events[-1]["type"] == "service_exiting"


def test_service_runtime_pong_reports_live_mode_when_worker_adapter_present() -> None:
    runtime = ServiceRuntime(session_runner=ServiceSessionRunner(worker_session=_FakeLiveWorkerSession()))

    should_exit, events = asyncio.run(runtime.handle_command(
        decode_service_message(encode_service_command("ping", session_id="s-1"))
    ))

    assert should_exit is False
    assert events[0]["type"] == "pong"
    assert events[0]["skeleton"] is False
