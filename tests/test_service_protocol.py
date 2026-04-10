import asyncio
import logging
from types import SimpleNamespace

import pytest

from doubaoime_asr.agent import service_main, stable_main
from doubaoime_asr.agent.service_protocol import (
    SERVICE_PROTOCOL_VERSION,
    ServiceProtocolError,
    decode_service_message,
    encode_service_command,
    encode_service_event,
)


class _Thread:
    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        return None


def test_service_protocol_command_roundtrip_preserves_version_and_payload() -> None:
    line = encode_service_command(
        "start",
        session_id="session-1",
        timeout_ms=1500,
        reason="smoke",
    )

    message = decode_service_message(line)

    assert message.version == SERVICE_PROTOCOL_VERSION
    assert message.kind == "command"
    assert message.name == "start"
    assert message.session_id == "session-1"
    assert message.payload == {"timeout_ms": 1500, "reason": "smoke"}


def test_service_protocol_event_defaults_payload_to_empty_object() -> None:
    message = decode_service_message(encode_service_event("service_ready"))

    assert message.kind == "event"
    assert message.name == "service_ready"
    assert message.payload == {}


def test_service_protocol_rejects_invalid_version() -> None:
    with pytest.raises(ServiceProtocolError, match="positive integer"):
        decode_service_message('{"version":0,"kind":"command","name":"ping"}')



def test_run_service_emits_ready_and_handles_ping_start_cancel_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted: list[tuple[str, dict[str, object]]] = []

    class FakeTransport:
        def __init__(self, *, logger, loop) -> None:
            self._loop = loop

        def start_reader(self, line_queue) -> None:
            for raw in (
                encode_service_command("ping"),
                encode_service_command("start", session_id="s-1", timeout_ms=1500),
                encode_service_command("cancel", session_id="s-1"),
                encode_service_command("exit"),
            ):
                self._loop.call_soon_threadsafe(line_queue.put_nowait, raw)

        def emit_event(self, event_type: str, **payload: object) -> None:
            emitted.append((event_type, payload))

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        service_main,
        "build_service_transport",
        lambda **kwargs: FakeTransport(logger=kwargs["logger"], loop=kwargs["loop"]),
    )
    monkeypatch.setattr(service_main, "setup_named_logger", lambda *args, **kwargs: logging.getLogger("service-main-test"))

    result = asyncio.run(
        service_main.run_service(
            SimpleNamespace(
                service_log_path=None,
                service_protocol_version=SERVICE_PROTOCOL_VERSION,
            )
        )
    )

    assert result == 0
    assert emitted[0][0] == "service_ready"
    assert any(event_type == "pong" for event_type, _ in emitted)
    assert any(
        event_type == "status" and payload.get("code") == "session_start_accepted"
        for event_type, payload in emitted
    )
    assert any(
        event_type == "status" and payload.get("code") == "session_canceled"
        for event_type, payload in emitted
    )
    assert emitted[-1][0] == "service_exiting"



def test_run_service_rejects_incoming_version_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted: list[tuple[str, dict[str, object]]] = []

    class FakeTransport:
        def __init__(self, *, logger, loop) -> None:
            self._loop = loop

        def start_reader(self, line_queue) -> None:
            self._loop.call_soon_threadsafe(
                line_queue.put_nowait,
                encode_service_command("ping", version=SERVICE_PROTOCOL_VERSION + 1),
            )
            self._loop.call_soon_threadsafe(
                line_queue.put_nowait,
                encode_service_command("exit"),
            )

        def emit_event(self, event_type: str, **payload: object) -> None:
            emitted.append((event_type, payload))

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        service_main,
        "build_service_transport",
        lambda **kwargs: FakeTransport(logger=kwargs["logger"], loop=kwargs["loop"]),
    )
    monkeypatch.setattr(service_main, "setup_named_logger", lambda *args, **kwargs: logging.getLogger("service-main-test"))

    result = asyncio.run(
        service_main.run_service(
            SimpleNamespace(
                service_log_path=None,
                service_protocol_version=SERVICE_PROTOCOL_VERSION,
            )
        )
    )

    assert result == 0
    assert any(
        event_type == "error" and "unsupported service protocol version" in str(payload.get("message", ""))
        for event_type, payload in emitted
    )


def test_stable_main_dispatches_service_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_service(args) -> int:
        assert args.service is True
        assert args.worker is False
        return 17

    async def fail_run_worker(args) -> int:
        raise AssertionError("worker mode should not run when --service is used")

    monkeypatch.setattr(stable_main, "run_service", fake_run_service)
    monkeypatch.setattr(stable_main, "run_worker", fail_run_worker)

    result = stable_main.main(["--service"])

    assert result == 17
