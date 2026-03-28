from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable


class BufferedMicrophoneCapture:
    """
    识别稳定优先的本地预缓冲麦克风采集器。

    行为目标：
    - 按下热键后立即开始本地采音
    - 先缓冲少量 PCM 帧，再启动 ASR 会话
    - 松键后停止继续采音，但允许已缓冲数据继续送完
    """

    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int,
        frame_duration_ms: int,
        device: int | str | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_duration_ms = frame_duration_ms
        self.device = device
        self.on_status = on_status

        self.chunk_count = 0
        self.bytes_captured = 0
        self.first_chunk_received = False

        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[bytes] | None = None
        self._started = asyncio.Event()
        self._closed = asyncio.Event()
        self._stop_requested = False
        self._startup_error: Exception | None = None
        self._capture_task: asyncio.Task[None] | None = None
    async def start(self) -> None:
        if self._capture_task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._capture_task = asyncio.create_task(self._capture_loop())
        await self._started.wait()
        if self._startup_error is not None:
            raise self._startup_error

    def stop(self) -> None:
        self._stop_requested = True

    async def wait_until_buffered(
        self,
        *,
        min_frames: int = 5,
        timeout_s: float = 0.5,
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if self._startup_error is not None:
                raise self._startup_error
            if self.chunk_count >= min_frames:
                return True
            if self._stop_requested and self.chunk_count > 0:
                return True
            await asyncio.sleep(0.02)
        return self.chunk_count > 0

    async def iter_chunks(self) -> AsyncIterator[bytes]:
        assert self._queue is not None
        while True:
            if self._startup_error is not None:
                raise self._startup_error
            if self._stop_requested and self._queue.empty():
                break
            try:
                data = await asyncio.wait_for(self._queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            yield data

    async def wait_closed(self) -> None:
        if self._capture_task is None:
            return
        await self._closed.wait()
        await self._capture_task

    def _handle_status(self, status_text: str) -> None:
        if self.on_status is not None:
            self.on_status(f"[Mic] 状态: {status_text}")

    def _handle_chunk(self, data: bytes) -> None:
        assert self._queue is not None
        self.chunk_count += 1
        self.bytes_captured += len(data)
        self.first_chunk_received = True
        self._queue.put_nowait(data)

    async def _capture_loop(self) -> None:
        import sounddevice as sd

        samples_per_frame = self.sample_rate * self.frame_duration_ms // 1000

        def audio_callback(indata, frames, time_info, status):
            if self._loop is None:
                return
            if status:
                self._loop.call_soon_threadsafe(
                    self._handle_status,
                    str(status),
                )
            data = indata.tobytes()
            self._loop.call_soon_threadsafe(self._handle_chunk, data)

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=np.int16,
                blocksize=samples_per_frame,
                device=self.device,
                callback=audio_callback,
            ):
                self._started.set()
                while not self._stop_requested:
                    await asyncio.sleep(0.05)
        except Exception as exc:
            self._startup_error = exc
            self._started.set()
        finally:
            self._closed.set()
