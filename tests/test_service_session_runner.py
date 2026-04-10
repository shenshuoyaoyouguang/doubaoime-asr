import asyncio

from doubaoime_asr.agent.service_session_runner import ServiceSessionRunner


def test_service_session_runner_start_then_stop() -> None:
    runner = ServiceSessionRunner()

    events = asyncio.run(runner.start(session_id="s-1", requested_timeout_ms=900))
    assert runner.state.active_session_id == "s-1"
    assert runner.state.requested_timeout_ms == 900
    assert events[0]["code"] == "session_start_accepted"

    events = asyncio.run(runner.finish("stop", session_id="s-1"))
    assert runner.state.active_session_id is None
    assert runner.state.requested_timeout_ms is None
    assert events[0]["code"] == "session_stopped"


def test_service_session_runner_rejects_mismatched_cancel() -> None:
    runner = ServiceSessionRunner()
    asyncio.run(runner.start(session_id="s-1", requested_timeout_ms=100))

    events = asyncio.run(runner.finish("cancel", session_id="other"))
    assert events[0]["type"] == "error"
    assert "session_id mismatch" in str(events[0]["message"])


def test_service_session_runner_exit_cancels_active_session() -> None:
    runner = ServiceSessionRunner()
    asyncio.run(runner.start(session_id="s-1", requested_timeout_ms=100))

    should_exit, events = asyncio.run(runner.exit(requested_by="leader"))
    assert should_exit is True
    assert runner.state.active_session_id is None
    assert events[0]["code"] == "session_cancelled_on_exit"
    assert events[-1]["type"] == "service_exiting"
