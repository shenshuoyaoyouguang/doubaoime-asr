from __future__ import annotations

import asyncio
import io
import logging
from types import SimpleNamespace

from doubaoime_asr.asr import ASRResponse, ASRTransportError, ResponseType
from doubaoime_asr.agent import worker_main


def test_start_stdin_reader_enqueues_exit_on_eof(monkeypatch):
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()
    monkeypatch.setattr(worker_main.sys, "stdin", io.StringIO(""))

    try:
        thread = worker_main._start_stdin_reader(loop, queue, logging.getLogger("worker-main-test"))
        thread.join(timeout=1)
        assert not thread.is_alive()
        loop.run_until_complete(asyncio.sleep(0))
        assert loop.run_until_complete(queue.get()) == "EXIT"
    finally:
        loop.close()


def test_measure_audio_level_returns_zero_for_silence():
    assert worker_main._measure_audio_level(b"\x00\x00" * 160) == 0.0


def test_measure_audio_level_detects_nonzero_signal():
    samples = (1000).to_bytes(2, "little", signed=True) * 160

    assert worker_main._measure_audio_level(samples) > 0.0


def test_emit_audio_level_uses_threadsafe_callback_without_loop_kwargs():
    emitted: list[tuple[str, dict[str, object]]] = []

    class _Loop:
        def call_soon_threadsafe(self, callback, *args):
            callback(*args)

    capture = worker_main.BufferedAudioCapture(
        sample_rate=16000,
        channels=1,
        frame_duration_ms=20,
        device=None,
        logger=logging.getLogger("worker-main-test"),
    )
    capture._loop = _Loop()

    original_emit = worker_main._emit_stdout
    worker_main._emit_stdout = lambda event_type, **payload: emitted.append((event_type, payload))
    try:
        capture._emit_audio_level((1000).to_bytes(2, "little", signed=True) * 160)
    finally:
        worker_main._emit_stdout = original_emit

    assert emitted
    assert emitted[-1][0] == "audio_level"
    assert "level" in emitted[-1][1]


def test_run_single_session_uses_safer_low_latency_prebuffer(monkeypatch):
    class _Capture:
        def __init__(self) -> None:
            self.wait_args: tuple[int, float] | None = None
            self.stop_requested = False
            self.iter_from_calls: list[int] = []

        async def start(self) -> None:
            return None

        async def wait_for_prebuffer(self, min_frames: int, timeout_s: float) -> bool:
            self.wait_args = (min_frames, timeout_s)
            return True

        async def iter_chunks_from(self, start_index: int = 0):
            self.iter_from_calls.append(start_index)
            if False:
                yield b""

        def request_stop(self) -> None:
            self.stop_requested = True

        async def wait_closed(self) -> None:
            return None

        chunk_count = 1
        bytes_captured = 320
        stop_event = SimpleNamespace(is_set=lambda: False)

    async def fake_transcribe_realtime(chunks, config):
        async for _ in chunks:
            break
        if False:
            yield None

    emitted: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(worker_main, "transcribe_realtime", fake_transcribe_realtime)
    monkeypatch.setattr(worker_main, "_emit_stdout", lambda event_type, **payload: emitted.append((event_type, payload)))

    capture = _Capture()
    result = asyncio.run(
        worker_main._run_single_session(
            capture=capture,
            config=SimpleNamespace(),
            logger=logging.getLogger("worker-main-test"),
        )
    )

    assert result == 0
    assert capture.wait_args == (
        worker_main.LOW_LATENCY_PREBUFFER_MIN_FRAMES,
        worker_main.LOW_LATENCY_PREBUFFER_TIMEOUT_S,
    )
    assert capture.iter_from_calls == [0]
    assert capture.stop_requested is True
    assert emitted[0][0] == "streaming_started"


def test_buffered_audio_capture_iter_chunks_from_replays_history():
    async def run() -> list[bytes]:
        capture = worker_main.BufferedAudioCapture(
            sample_rate=16000,
            channels=1,
            frame_duration_ms=20,
            device=None,
            logger=logging.getLogger("worker-main-test"),
        )
        capture._record_chunk(b"a")
        capture._record_chunk(b"b")
        capture.closed_event.set()
        output: list[bytes] = []
        async for chunk in capture.iter_chunks_from(1):
            output.append(chunk)
        return output

    assert asyncio.run(run()) == [b"b"]


def test_run_single_session_retries_transport_error(monkeypatch):
    class _Capture:
        def __init__(self) -> None:
            self.stop_requested = False
            self.iter_from_calls: list[int] = []

        async def start(self) -> None:
            return None

        async def wait_for_prebuffer(self, min_frames: int, timeout_s: float) -> bool:
            return True

        async def iter_chunks_from(self, start_index: int = 0):
            self.iter_from_calls.append(start_index)
            if False:
                yield b""

        def request_stop(self) -> None:
            self.stop_requested = True

        async def wait_closed(self) -> None:
            return None

        chunk_count = 1
        bytes_captured = 320
        stop_event = SimpleNamespace(is_set=lambda: False)

    attempts = 0
    emitted: list[tuple[str, dict[str, object]]] = []

    async def fake_transcribe_realtime(chunks, config):
        nonlocal attempts
        attempts += 1
        async for _ in chunks:
            break
        if attempts == 1:
            raise ASRTransportError("boom")
        if False:
            yield None

    monkeypatch.setattr(worker_main, "transcribe_realtime", fake_transcribe_realtime)
    monkeypatch.setattr(worker_main, "_emit_stdout", lambda event_type, **payload: emitted.append((event_type, payload)))

    capture = _Capture()
    result = asyncio.run(
        worker_main._run_single_session(
            capture=capture,
            config=SimpleNamespace(),
            logger=logging.getLogger("worker-main-test"),
        )
    )

    assert result == 0
    assert attempts == 2
    assert capture.iter_from_calls == [0, 0]
    assert any(event == "status" and "正在重试" in payload["message"] for event, payload in emitted)
    assert emitted[-1][0] == "finished"


def test_run_single_session_does_not_retry_after_final(monkeypatch):
    class _Capture:
        def __init__(self) -> None:
            self.stop_requested = False
            self.iter_from_calls: list[int] = []

        async def start(self) -> None:
            return None

        async def wait_for_prebuffer(self, min_frames: int, timeout_s: float) -> bool:
            return True

        async def iter_chunks_from(self, start_index: int = 0):
            self.iter_from_calls.append(start_index)
            if False:
                yield b""

        def request_stop(self) -> None:
            self.stop_requested = True

        async def wait_closed(self) -> None:
            return None

        chunk_count = 1
        bytes_captured = 320
        stop_event = SimpleNamespace(is_set=lambda: False)

    emitted: list[tuple[str, dict[str, object]]] = []

    async def fake_transcribe_realtime(chunks, config):
        async for _ in chunks:
            break
        yield ASRResponse(
            type=ResponseType.FINAL_RESULT,
            text="最终文本",
            results=[SimpleNamespace(index=0)],
        )
        raise ASRTransportError("boom")

    monkeypatch.setattr(worker_main, "transcribe_realtime", fake_transcribe_realtime)
    monkeypatch.setattr(worker_main, "_emit_stdout", lambda event_type, **payload: emitted.append((event_type, payload)))

    capture = _Capture()
    result = asyncio.run(
        worker_main._run_single_session(
            capture=capture,
            config=SimpleNamespace(),
            logger=logging.getLogger("worker-main-test"),
        )
    )

    assert result == 4
    assert capture.iter_from_calls == [0]
    assert any(event == "final" for event, _ in emitted)
    assert emitted[-1][0] == "error"
