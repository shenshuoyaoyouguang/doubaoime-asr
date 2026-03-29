from __future__ import annotations

import asyncio
import io
import logging
from types import SimpleNamespace

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

        async def start(self) -> None:
            return None

        async def wait_for_prebuffer(self, min_frames: int, timeout_s: float) -> bool:
            self.wait_args = (min_frames, timeout_s)
            return True

        async def iter_chunks(self):
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
    assert capture.stop_requested is True
    assert emitted[0][0] == "streaming_started"
