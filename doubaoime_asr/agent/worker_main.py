from __future__ import annotations

import argparse
import asyncio
from array import array
import contextlib
from datetime import datetime
import functools
import logging
import math
import os
from pathlib import Path
import sys
import threading
import time
from typing import AsyncIterator

from doubaoime_asr.asr import ResponseType, transcribe_realtime
from doubaoime_asr.config import ASRConfig

from .config import AgentConfig
from .protocol import encode_event
from .runtime_logging import setup_named_logger

LOW_LATENCY_PREBUFFER_MIN_FRAMES = 1
LOW_LATENCY_PREBUFFER_TIMEOUT_S = 0.18


def _configure_stdio_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _emit_stdout(event_type: str, **payload) -> None:
    print(encode_event(event_type, **payload), flush=True)


def _response_segment_index(response) -> int | None:
    if not getattr(response, "results", None):
        return None
    indexes = [result.index for result in response.results]
    if not indexes:
        return None
    return max(indexes)


def _measure_audio_level(frame_bytes: bytes) -> float:
    if not frame_bytes:
        return 0.0
    try:
        samples = array("h")
        samples.frombytes(frame_bytes)
    except (BufferError, ValueError):
        return 0.0
    if not samples:
        return 0.0
    if sys.byteorder != "little":
        samples.byteswap()
    energy = sum(sample * sample for sample in samples) / len(samples)
    rms = math.sqrt(energy) / 32768.0
    return max(0.0, min(1.0, rms))


def build_worker_log_path(path_arg: str | None = None) -> Path:
    if path_arg:
        return Path(path_arg)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return AgentConfig.default_worker_log_dir() / f"worker-{timestamp}-{os.getpid()}.log"


def add_worker_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-log-path", help=argparse.SUPPRESS)


class BufferedAudioCapture:
    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int,
        frame_duration_ms: int,
        device: int | str | None,
        logger: logging.Logger,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_duration_ms = frame_duration_ms
        self.device = device
        self.logger = logger

        self.chunk_count = 0
        self.bytes_captured = 0
        self.ready_event = asyncio.Event()
        self.closed_event = asyncio.Event()
        self.first_chunk_event = asyncio.Event()
        self.stop_event = asyncio.Event()

        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._startup_error: Exception | None = None
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_level_emit_at = 0.0
        self._last_emitted_level = 0.0
        self._smoothed_level = 0.0

    async def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._capture_loop())
        await self.ready_event.wait()
        if self._startup_error is not None:
            raise self._startup_error

    async def wait_for_prebuffer(self, min_frames: int, timeout_s: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if self._startup_error is not None:
                raise self._startup_error
            if self.chunk_count >= min_frames:
                return True
            if self.stop_event.is_set() and self.chunk_count > 0:
                return True
            await asyncio.sleep(0.01)
        return self.chunk_count > 0

    def request_stop(self) -> None:
        self.stop_event.set()

    async def iter_chunks(self) -> AsyncIterator[bytes]:
        while True:
            if self._startup_error is not None:
                raise self._startup_error
            if self.stop_event.is_set() and self._queue.empty():
                break
            try:
                data = await asyncio.wait_for(self._queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            yield data

    async def wait_closed(self) -> None:
        await self.closed_event.wait()
        if self._task is not None:
            await self._task

    def _emit_audio_level(self, frame_bytes: bytes) -> None:
        if self._loop is None:
            return
        now = time.perf_counter()
        level = _measure_audio_level(frame_bytes)
        self._smoothed_level += (level - self._smoothed_level) * 0.35
        throttled = now - self._last_level_emit_at < 1.0 / 30.0
        small_change = abs(self._smoothed_level - self._last_emitted_level) < 0.015
        if throttled and small_change:
            return
        self._last_level_emit_at = now
        self._last_emitted_level = self._smoothed_level
        self._loop.call_soon_threadsafe(
            functools.partial(_emit_stdout, "audio_level", level=round(self._smoothed_level, 4))
        )

    async def _capture_loop(self) -> None:
        import sounddevice as sd
        import numpy as np

        samples_per_frame = self.sample_rate * self.frame_duration_ms // 1000

        def audio_callback(indata, frames, time_info, status):
            if self._loop is None:
                return
            if status:
                self.logger.info("mic_status=%s", status)
                self._loop.call_soon_threadsafe(
                    functools.partial(_emit_stdout, "status", message=f"[Mic] 状态: {status}")
                )
            data = indata.tobytes()
            self.chunk_count += 1
            self.bytes_captured += len(data)
            if not self.first_chunk_event.is_set():
                self._loop.call_soon_threadsafe(self.first_chunk_event.set)
            self._loop.call_soon_threadsafe(self._queue.put_nowait, data)
            self._emit_audio_level(data)

        try:
            self.logger.info(
                "opening_microphone device=%s sample_rate=%s channels=%s frame_duration_ms=%s",
                self.device,
                self.sample_rate,
                self.channels,
                self.frame_duration_ms,
            )
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=np.int16,
                blocksize=samples_per_frame,
                device=self.device,
                callback=audio_callback,
            ):
                self.logger.info("microphone_opened")
                _emit_stdout("ready")
                self.ready_event.set()
                while not self.stop_event.is_set():
                    await asyncio.sleep(0.02)
        except Exception as exc:
            self._startup_error = exc
            self.logger.exception("microphone_open_failed")
            self.ready_event.set()
        finally:
            self.logger.info(
                "microphone_closed chunks=%s bytes=%s stop_requested=%s",
                self.chunk_count,
                self.bytes_captured,
                self.stop_event.is_set(),
            )
            self.closed_event.set()


def _start_stdin_reader(
    loop: asyncio.AbstractEventLoop,
    command_queue: asyncio.Queue[str],
    logger: logging.Logger,
) -> threading.Thread:
    def reader() -> None:
        saw_exit = False
        try:
            for line in sys.stdin:
                command = line.strip().upper()
                if not command:
                    continue
                logger.info("stdin_command=%s", command)
                loop.call_soon_threadsafe(command_queue.put_nowait, command)
                if command == "EXIT":
                    saw_exit = True
                    break
        except Exception:
            logger.exception("stdin_reader_failed")
            loop.call_soon_threadsafe(command_queue.put_nowait, "EXIT")
            return
        if not saw_exit:
            logger.info("stdin_eof")
            loop.call_soon_threadsafe(command_queue.put_nowait, "EXIT")

    thread = threading.Thread(target=reader, name="doubao-worker-stdin", daemon=True)
    thread.start()
    return thread


async def _run_single_session(
    *,
    capture: BufferedAudioCapture,
    config: ASRConfig,
    logger: logging.Logger,
) -> int:
    try:
        await capture.start()
    except OSError as exc:
        logger.exception("microphone_start_failed")
        _emit_stdout("error", message=f"麦克风初始化失败: {exc}")
        return 2
    except Exception as exc:
        logger.exception("capture_start_failed")
        _emit_stdout("error", message=str(exc) or exc.__class__.__name__)
        return 4

    try:
        buffered_ok = await capture.wait_for_prebuffer(
            min_frames=LOW_LATENCY_PREBUFFER_MIN_FRAMES,
            timeout_s=LOW_LATENCY_PREBUFFER_TIMEOUT_S,
        )
        logger.info(
            "prebuffer_ready=%s chunks=%s bytes=%s stop_requested=%s",
            buffered_ok,
            capture.chunk_count,
            capture.bytes_captured,
            capture.stop_event.is_set(),
        )
        if not buffered_ok:
            _emit_stdout("status", message="未采集到音频，请按住热键后说话")
            _emit_stdout("finished")
            return 0

        _emit_stdout("streaming_started", chunks=capture.chunk_count, bytes=capture.bytes_captured)

        async for response in transcribe_realtime(capture.iter_chunks(), config=config):
            logger.info("response=%s text=%s", response.type.name, response.text)
            if response.type == ResponseType.TASK_STARTED:
                _emit_stdout("status", message="任务已启动")
            elif response.type == ResponseType.SESSION_STARTED:
                _emit_stdout("status", message="会话已启动，开始说话…")
            elif response.type == ResponseType.INTERIM_RESULT and response.text:
                _emit_stdout(
                    "interim",
                    text=response.text,
                    segment_index=_response_segment_index(response),
                )
            elif response.type == ResponseType.FINAL_RESULT:
                _emit_stdout(
                    "final",
                    text=response.text,
                    segment_index=_response_segment_index(response),
                )
            elif response.type == ResponseType.ERROR:
                _emit_stdout("error", message=response.error_msg or "语音识别失败")
                return 3

        _emit_stdout("finished")
        return 0
    except Exception as exc:
        logger.exception("worker_session_failed")
        _emit_stdout("error", message=str(exc) or exc.__class__.__name__)
        return 4
    finally:
        capture.request_stop()
        await capture.wait_closed()


async def run_worker(args: argparse.Namespace) -> int:
    _configure_stdio_utf8()
    log_path = build_worker_log_path(args.worker_log_path)
    logger = setup_named_logger(f"doubaoime_asr.agent.worker.{id(log_path)}", log_path)

    config = ASRConfig(
        credential_path=args.credential_path or AgentConfig.default().credential_path
    )
    device = None
    if getattr(args, "mic_device", None):
        device = int(args.mic_device) if str(args.mic_device).isdigit() else args.mic_device

    loop = asyncio.get_running_loop()
    command_queue: asyncio.Queue[str] = asyncio.Queue()
    stdin_thread = _start_stdin_reader(loop, command_queue, logger)

    active_capture: BufferedAudioCapture | None = None
    active_task: asyncio.Task[int] | None = None

    def reset_active_state(task: asyncio.Task[int]) -> None:
        nonlocal active_capture, active_task
        with_context = None
        try:
            with_context = task.result()
        except Exception:
            logger.exception("worker_session_task_failed")
        finally:
            active_capture = None
            active_task = None
            logger.info("worker_session_closed result=%s", with_context)

    _emit_stdout("worker_ready")
    logger.info("worker_ready")

    try:
        while True:
            command = await command_queue.get()

            if command == "START":
                if active_task is not None and not active_task.done():
                    logger.info("worker_start_ignored reason=session_active")
                    continue
                active_capture = BufferedAudioCapture(
                    sample_rate=config.sample_rate,
                    channels=config.channels,
                    frame_duration_ms=config.frame_duration_ms,
                    device=device,
                    logger=logger,
                )
                active_task = asyncio.create_task(
                    _run_single_session(capture=active_capture, config=config, logger=logger)
                )
                active_task.add_done_callback(reset_active_state)
            elif command == "STOP":
                if active_capture is not None:
                    active_capture.request_stop()
            elif command == "EXIT":
                if active_capture is not None:
                    active_capture.request_stop()
                if active_task is not None:
                    with contextlib.suppress(Exception):
                        await active_task
                return 0
    finally:
        if active_capture is not None:
            active_capture.request_stop()
        if active_task is not None:
            with_context = None
            try:
                with_context = await active_task
            except Exception:
                logger.exception("worker_shutdown_task_failed")
            logger.info("worker_shutdown_result=%s", with_context)
        if stdin_thread.is_alive():
            stdin_thread.join(timeout=1)
