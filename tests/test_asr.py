from __future__ import annotations

import pytest
import websockets

from doubaoime_asr import asr
from doubaoime_asr.asr import (
    ASRProbeResult,
    ASRResponse,
    ASRTransportError,
    DoubaoASR,
    ResponseType,
    probe_asr_session,
    transcribe_realtime,
)
from doubaoime_asr.config import ASRConfig


class _FakeWebSocket:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.sent: list[bytes] = []

    async def send(self, message: bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> object:
        if not self._responses:
            raise AssertionError("unexpected recv")
        return self._responses.pop(0)


class _FakeConnect:
    def __init__(self, ws: _FakeWebSocket) -> None:
        self._ws = ws

    async def __aenter__(self) -> _FakeWebSocket:
        return self._ws

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _make_config() -> ASRConfig:
    config = ASRConfig(device_id="device-id", token="token")
    config.ensure_credentials = lambda: None  # type: ignore[method-assign]
    return config


@pytest.mark.asyncio
async def test_probe_asr_session_success(monkeypatch: pytest.MonkeyPatch):
    config = _make_config()
    ws = _FakeWebSocket([b"task", b"session"])
    responses = [
        ASRResponse(type=ResponseType.TASK_STARTED),
        ASRResponse(type=ResponseType.SESSION_STARTED),
    ]

    monkeypatch.setattr(asr.websockets, "connect", lambda *args, **kwargs: _FakeConnect(ws))
    monkeypatch.setattr(asr, "_parse_response", lambda raw: responses.pop(0))

    result = await probe_asr_session(config)

    assert result.ok is True
    assert result.stage == "ok"
    assert result.latency_ms >= 0
    assert len(ws.sent) == 3


@pytest.mark.asyncio
async def test_probe_asr_session_returns_start_task_error(monkeypatch: pytest.MonkeyPatch):
    config = _make_config()
    ws = _FakeWebSocket([b"task"])
    monkeypatch.setattr(asr.websockets, "connect", lambda *args, **kwargs: _FakeConnect(ws))
    monkeypatch.setattr(
        asr,
        "_parse_response",
        lambda raw: ASRResponse(type=ResponseType.ERROR, error_msg="bad task"),
    )

    result = await probe_asr_session(config)

    assert result.ok is False
    assert result.stage == "start_task"
    assert result.message == "bad task"


@pytest.mark.asyncio
async def test_probe_asr_session_returns_start_session_error(monkeypatch: pytest.MonkeyPatch):
    config = _make_config()
    ws = _FakeWebSocket([b"task", b"session"])
    responses = [
        ASRResponse(type=ResponseType.TASK_STARTED),
        ASRResponse(type=ResponseType.ERROR, error_msg="bad session"),
    ]
    monkeypatch.setattr(asr.websockets, "connect", lambda *args, **kwargs: _FakeConnect(ws))
    monkeypatch.setattr(asr, "_parse_response", lambda raw: responses.pop(0))

    result = await probe_asr_session(config)

    assert result.ok is False
    assert result.stage == "start_session"
    assert result.message == "bad session"


@pytest.mark.asyncio
async def test_probe_asr_session_returns_connect_error(monkeypatch: pytest.MonkeyPatch):
    config = _make_config()

    def fake_connect(*args, **kwargs):
        raise websockets.exceptions.WebSocketException("boom")

    monkeypatch.setattr(asr.websockets, "connect", fake_connect)

    result = await probe_asr_session(config)

    assert result == ASRProbeResult(
        ok=False,
        stage="connect",
        message=result.message,
        latency_ms=result.latency_ms,
    )
    assert "boom" in result.message


@pytest.mark.asyncio
async def test_transcribe_realtime_raises_transport_error_on_unexpected_close(monkeypatch: pytest.MonkeyPatch):
    config = _make_config()
    ws = _FakeWebSocket([])

    async def fake_initialize(self, ws, state):
        if False:
            yield None

    async def fake_send_audio_realtime(self, ws, audio_source, state):
        return None

    async def fake_receive_responses(self, ws, state, queue):
        state.transport_closed_unexpectedly = True
        await queue.put(None)

    monkeypatch.setattr(asr.websockets, "connect", lambda *args, **kwargs: _FakeConnect(ws))
    monkeypatch.setattr(DoubaoASR, "_initialize_session", fake_initialize)
    monkeypatch.setattr(DoubaoASR, "_send_audio_realtime", fake_send_audio_realtime)
    monkeypatch.setattr(DoubaoASR, "_receive_responses", fake_receive_responses)

    async def empty_source():
        if False:
            yield b""

    with pytest.raises(ASRTransportError):
        async for _ in transcribe_realtime(empty_source(), config=config):
            pass


@pytest.mark.asyncio
async def test_transcribe_realtime_does_not_raise_after_final(monkeypatch: pytest.MonkeyPatch):
    config = _make_config()
    ws = _FakeWebSocket([])

    async def fake_initialize(self, ws, state):
        if False:
            yield None

    async def fake_send_audio_realtime(self, ws, audio_source, state):
        return None

    async def fake_receive_responses(self, ws, state, queue):
        state.received_final = True
        await queue.put(None)

    monkeypatch.setattr(asr.websockets, "connect", lambda *args, **kwargs: _FakeConnect(ws))
    monkeypatch.setattr(DoubaoASR, "_initialize_session", fake_initialize)
    monkeypatch.setattr(DoubaoASR, "_send_audio_realtime", fake_send_audio_realtime)
    monkeypatch.setattr(DoubaoASR, "_receive_responses", fake_receive_responses)

    async def empty_source():
        if False:
            yield b""

    seen = []
    async for item in transcribe_realtime(empty_source(), config=config):
        seen.append(item)

    assert seen == []
