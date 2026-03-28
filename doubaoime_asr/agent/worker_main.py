from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import logging
import os
from pathlib import Path
import sys
import threading
from typing import AsyncIterator

from doubaoime_asr.asr import transcribe_realtime, ResponseType
from doubaoime_asr.config import ASRConfig

from .config import AgentConfig
from .protocol import encode_event
from .runtime_logging import setup_named_logger


def _configure_stdio_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _emit_stdout(event_type: str, **payload) -> None:
    print(encode_event(event_type, **payload), flush=True)


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
            await asyncio.sleep(0.02)
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
                data = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            yield data

    async def wait_closed(self) -> None:
        await self.closed_event.wait()
        if self._task is not None:
            await self._task

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
                    _emit_stdout,
                    "status",
                    message=f"[Mic] 状态: {status}",
                )
            data = indata.tobytes()
            self.chunk_count += 1
            self.bytes_captured += len(data)
            if not self.first_chunk_event.is_set():
                self._loop.call_soon_threadsafe(self.first_chunk_event.set)
            self._loop.call_soon_threadsafe(self._queue.put_nowait, data)

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
                    await asyncio.sleep(0.05)
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
    capture: BufferedAudioCapture,
    logger: logging.Logger,
) -> threading.Thread:
    def reader() -> None:
        try:
            for line in sys.stdin:
                command = line.strip().upper()
                if command == "STOP":
                    logger.info("stdin_command=STOP")
                    loop.call_soon_threadsafe(capture.request_stop)
                    break
        except Exception:
            logger.exception("stdin_reader_failed")
            loop.call_soon_threadsafe(capture.request_stop)

    thread = threading.Thread(target=reader, name="doubao-worker-stdin", daemon=True)
    thread.start()
    return thread


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

    capture = BufferedAudioCapture(
        sample_rate=config.sample_rate,
        channels=config.channels,
        frame_duration_ms=config.frame_duration_ms,
        device=device,
        logger=logger,
    )
    loop = asyncio.get_running_loop()
    _start_stdin_reader(loop, capture, logger)

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
        buffered_ok = await capture.wait_for_prebuffer(min_frames=5, timeout_s=0.5)
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
                _emit_stdout("interim", text=response.text)
            elif response.type == ResponseType.FINAL_RESULT:
                _emit_stdout("final", text=response.text)
            elif response.type == ResponseType.ERROR:
                _emit_stdout("error", message=response.error_msg or "语音识别失败")
                return 3

        _emit_stdout("finished")
        return 0
    except Exception as exc:
        logger.exception("worker_failed")
        _emit_stdout("error", message=str(exc) or exc.__class__.__name__)
        return 4
    finally:
        capture.request_stop()
        await capture.wait_closed()
