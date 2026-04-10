import asyncio
import logging

import pytest

from doubaoime_asr.agent.service_host import ServiceHost
from doubaoime_asr.agent.service_runtime import ServiceRuntime
from doubaoime_asr.agent.service_session_runner import ServiceSessionRunner


class FakeTransport:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit_event(self, event_type: str, **payload: object) -> None:
        self.events.append((event_type, payload))


class FakeWorkerSession:
    def __init__(self, *, emit_during_start: dict[str, object] | None = None) -> None:
        self.callback = None
        self.calls: list[str] = []
        self.emit_during_start = emit_during_start

    def set_event_callback(self, callback):
        self.callback = callback

    async def ensure_worker(self) -> None:
        self.calls.append("ensure_worker")

    def begin_session(self) -> None:
        self.calls.append("begin_session")

    async def start_session(self) -> None:
        self.calls.append("start_session")
        if self.emit_during_start is not None:
            assert self.callback is not None
            self.callback(dict(self.emit_during_start))

    async def stop_session(self) -> None:
        self.calls.append("stop_session")

    async def terminate_worker(self) -> None:
        self.calls.append("terminate_worker")

    def emit(self, event_type: str, **payload: object) -> None:
        assert self.callback is not None
        self.callback({"type": event_type, **payload})


def test_service_host_emits_ready() -> None:
    transport = FakeTransport()
    host = ServiceHost(logger=logging.getLogger("service-host-test"), transport=transport)  # type: ignore[arg-type]

    host.emit_ready()

    assert transport.events[0][0] == "service_ready"
    assert transport.events[0][1]["protocol_version"] == 1


def test_service_host_rejects_version_mismatch() -> None:
    transport = FakeTransport()
    host = ServiceHost(logger=logging.getLogger("service-host-test"), transport=transport)  # type: ignore[arg-type]

    should_exit = asyncio.run(host.handle_raw_message('{"version":2,"kind":"command","name":"ping","payload":{}}'))

    assert should_exit is False
    assert transport.events[0][0] == "error"
    assert "unsupported service protocol version" in str(transport.events[0][1]["message"])


def test_service_host_routes_runtime_events() -> None:
    transport = FakeTransport()
    host = ServiceHost(logger=logging.getLogger("service-host-test"), transport=transport)  # type: ignore[arg-type]

    should_exit = asyncio.run(host.handle_raw_message('{"version":1,"kind":"command","name":"ping","payload":{}}'))

    assert should_exit is False
    assert transport.events[0][0] == "pong"


def test_service_host_run_emits_ready_and_exits() -> None:
    async def _run() -> list[tuple[str, dict[str, object]]]:
        transport = FakeTransport()
        host = ServiceHost(logger=logging.getLogger("service-host-test"), transport=transport)  # type: ignore[arg-type]
        queue: asyncio.Queue[str] = asyncio.Queue()
        await queue.put('{"version":1,"kind":"command","name":"exit","payload":{}}')
        result = await host.run(queue)
        assert result == 0
        return transport.events

    events = asyncio.run(_run())
    assert events[0][0] == "service_ready"
    assert events[-1][0] == "service_exiting"


def test_service_host_emits_worker_bridge_event() -> None:
    transport = FakeTransport()
    host = ServiceHost(logger=logging.getLogger("service-host-test"), transport=transport)  # type: ignore[arg-type]

    host.emit_worker_event({"type": "interim", "text": "你好", "segment_index": 1}, session_id="s-1")

    assert transport.events[0][0] == "interim"
    assert transport.events[0][1]["text"] == "你好"
    assert transport.events[0][1]["segment_index"] == 1


@pytest.mark.asyncio
async def test_service_host_start_invokes_worker_session_adapter() -> None:
    transport = FakeTransport()
    worker_session = FakeWorkerSession()
    runtime = ServiceRuntime(session_runner=ServiceSessionRunner(worker_session=worker_session))
    host = ServiceHost(
        logger=logging.getLogger("service-host-test"),
        transport=transport,  # type: ignore[arg-type]
        runtime=runtime,
    )

    should_exit = await host.handle_raw_message(
        '{"version":1,"kind":"command","name":"start","session_id":"s-1","payload":{"timeout_ms":500}}'
    )

    assert should_exit is False
    assert worker_session.calls == ["ensure_worker", "begin_session", "start_session"]
    assert host.runtime.state.active_session_id == "s-1"
    assert transport.events[-1][0] == "status"
    assert transport.events[-1][1]["code"] == "session_start_accepted"


@pytest.mark.asyncio
async def test_service_host_worker_events_flow_through_live_runner() -> None:
    transport = FakeTransport()
    worker_session = FakeWorkerSession()
    host = ServiceHost(
        logger=logging.getLogger("service-host-test"),
        transport=transport,  # type: ignore[arg-type]
        runtime=ServiceRuntime(
            session_runner=ServiceSessionRunner(worker_session=worker_session),
        ),
    )

    await host.handle_raw_message('{"version":1,"kind":"command","name":"start","session_id":"s-1","payload":{}}')
    worker_session.emit("interim", text="你好", segment_index=1)
    worker_session.emit("final", text="世界", segment_index=1)
    await host.handle_raw_message('{"version":1,"kind":"command","name":"stop","session_id":"s-1","payload":{}}')
    worker_session.emit("finished")
    await asyncio.sleep(0)

    assert [event for event, _ in transport.events if event in {"interim", "final_raw"}] == ["interim", "final_raw"]
    assert any(event == "final_resolved" for event, _ in transport.events)
    assert transport.events[-2][0] == "status"
    assert transport.events[-2][1]["code"] == "worker_finished"
    assert transport.events[-1][1]["code"] == "session_stopped"
    assert host.runtime.state.active_session_id is None


@pytest.mark.asyncio
async def test_service_host_exit_terminates_worker_before_service_exiting() -> None:
    transport = FakeTransport()
    worker_session = FakeWorkerSession()
    host = ServiceHost(
        logger=logging.getLogger("service-host-test"),
        transport=transport,  # type: ignore[arg-type]
        runtime=ServiceRuntime(
            session_runner=ServiceSessionRunner(worker_session=worker_session),
        ),
    )

    await host.handle_raw_message('{"version":1,"kind":"command","name":"start","session_id":"s-1","payload":{}}')
    should_exit = await host.handle_raw_message('{"version":1,"kind":"command","name":"exit","session_id":"leader","payload":{}}')

    assert should_exit is True
    assert "terminate_worker" in worker_session.calls
    assert transport.events[-1][0] == "service_exiting"


@pytest.mark.asyncio
async def test_service_host_rejects_second_start_until_worker_session_finishes() -> None:
    transport = FakeTransport()
    worker_session = FakeWorkerSession()
    host = ServiceHost(
        logger=logging.getLogger("service-host-test"),
        transport=transport,  # type: ignore[arg-type]
        runtime=ServiceRuntime(
            session_runner=ServiceSessionRunner(worker_session=worker_session),
        ),
    )

    await host.handle_raw_message('{"version":1,"kind":"command","name":"start","session_id":"s-1","payload":{}}')
    await host.handle_raw_message('{"version":1,"kind":"command","name":"stop","session_id":"s-1","payload":{}}')
    should_exit = await host.handle_raw_message('{"version":1,"kind":"command","name":"start","session_id":"s-2","payload":{}}')

    assert should_exit is False
    assert transport.events[-1][0] == "error"
    assert "service busy" in str(transport.events[-1][1]["message"])

    worker_session.emit("finished")
    await host.handle_raw_message('{"version":1,"kind":"command","name":"start","session_id":"s-2","payload":{}}')

    assert host.runtime.state.active_session_id == "s-2"


@pytest.mark.asyncio
async def test_service_host_clears_active_session_on_worker_exit() -> None:
    transport = FakeTransport()
    worker_session = FakeWorkerSession()
    host = ServiceHost(
        logger=logging.getLogger("service-host-test"),
        transport=transport,  # type: ignore[arg-type]
        runtime=ServiceRuntime(session_runner=ServiceSessionRunner(worker_session=worker_session)),
    )

    await host.handle_raw_message('{"version":1,"kind":"command","name":"start","session_id":"s-1","payload":{}}')
    worker_session.emit("worker_exit", code=11)

    assert host.runtime.state.active_session_id is None
    assert transport.events[-2][0] == "error"
    assert transport.events[-2][1]["code"] == "worker_exit"
    assert transport.events[-1][1]["code"] == "session_aborted_worker_exit"


@pytest.mark.asyncio
async def test_service_host_keeps_worker_event_emitted_during_start() -> None:
    transport = FakeTransport()
    worker_session = FakeWorkerSession(emit_during_start={"type": "interim", "text": "首帧", "segment_index": 1})
    host = ServiceHost(
        logger=logging.getLogger("service-host-test"),
        transport=transport,  # type: ignore[arg-type]
        runtime=ServiceRuntime(session_runner=ServiceSessionRunner(worker_session=worker_session)),
    )

    await host.handle_raw_message('{"version":1,"kind":"command","name":"start","session_id":"s-1","payload":{}}')

    assert transport.events[0][0] == "interim"
    assert transport.events[0][1]["text"] == "首帧"
