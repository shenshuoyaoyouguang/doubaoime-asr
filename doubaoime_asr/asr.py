from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
import time
from typing import AsyncIterator, Callable, List, Optional, Union
import uuid
from pydantic import BaseModel, Field
import websockets
from websockets import ClientConnection
from websockets.protocol import State

from .config import ASRConfig
from .audio import AudioEncoder
from .asr_pb2 import FrameState
from .asr_models import (
    ASRAlternative,
    ASRError,
    ASRExtra,
    ASRProbeResult,
    ASRResponse,
    ASRResult,
    ASRTransportError,
    ASRWord,
    OIDecodingInfo,
    ResponseType,
)
from .asr_protocol import (
    build_asr_request as _build_asr_request,
    build_finish_session as _build_finish_session,
    build_start_session as _build_start_session,
    build_start_task as _build_start_task,
    parse_response as _parse_response,
)

# PCM 音频数据的类型别名
AudioChunk = bytes

__all__ = [
    "AudioChunk",
    "ResponseType",
    "ASRWord",
    "OIDecodingInfo",
    "ASRAlternative",
    "ASRResult",
    "ASRExtra",
    "ASRResponse",
    "ASRError",
    "ASRTransportError",
    "ASRProbeResult",
    "DoubaoASR",
    "probe_asr_session",
    "transcribe",
    "transcribe_stream",
    "transcribe_realtime",
]


class _SessionState(BaseModel):
    """
    ASR 会话状态
    """
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    final_text: str = ""
    is_finished: bool = False
    error: Optional[ASRResponse] = None
    transport_closed_unexpectedly: bool = False
    received_final: bool = False
    received_session_finished: bool = False


def _connection_is_open(ws: ClientConnection) -> bool:
    closed = getattr(ws, "closed", None)
    if isinstance(closed, bool):
        return not closed

    state = getattr(ws, "state", None)
    if state is None:
        return True
    try:
        return state == State.OPEN
    except Exception:
        return True


def _latency_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


class DoubaoASR:
    """
    豆包输入法 ASR 客户端
    """
    def __init__(self, config: Optional[ASRConfig] = None):
        self.config = config
        self._encoder = AudioEncoder(self.config)
    
    async def __aenter__(self) -> DoubaoASR:
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    async def transcribe(self, audio: Union[str, Path, bytes], *, realtime = False, on_interim: Callable[[str], None] = None) -> str:
        """
        非流式语音识别

        :param audio: 音频文件路径或 PCM 字节数据
        :param on_interim: 可选的中间结果回调
        :return: 最终识别文本
        """
        final_text = ""
        async for response in self.transcribe_stream(audio, realtime=realtime):
            if response.type == ResponseType.INTERIM_RESULT and on_interim:
                on_interim(response.text)
            elif response.type == ResponseType.FINAL_RESULT:
                final_text = response.text
            elif response.type == ResponseType.ERROR:
                raise ASRError(response.error_msg, response)
        return final_text
    
    async def transcribe_stream(self, audio: Union[str, Path, bytes], *, realtime: bool = False) -> AsyncIterator[ASRResponse]:
        """
        流式语音识别（完整音频）

        :param audio: 音频文件路径或 PCM 字节数据
        :param realtime: 是否按实时速度发送
        :return: ASR 响应流，包括中间结果和最终结果
        """
        if isinstance(audio, (str, Path)):
            pcm_data = self._encoder.convert_audio_to_pcm(
                audio, self.config.sample_rate, self.config.channels,
            )
        else:
            pcm_data = audio

        opus_frames = self._encoder.pcm_to_opus_frames(pcm_data)
        state = _SessionState()

        try:
            async with websockets.connect(
                self.config.ws_url,
                additional_headers=self.config.headers,
                open_timeout=self.config.connect_timeout,
            ) as ws:
                # 初始化会话
                async for resp in self._initialize_session(ws, state):
                    yield resp

                # 响应队列
                response_queue: asyncio.Queue[Optional[ASRResponse]] = asyncio.Queue()

                # 启动发送和接收任务
                send_task = asyncio.create_task(
                    self._send_audio(ws, opus_frames, state, realtime)
                )
                recv_task = asyncio.create_task(
                    self._receive_responses(ws, state, response_queue)
                )

                try:
                    # 从队列中获取服务器响应
                    while True:
                        try:
                            resp = await asyncio.wait_for(
                                response_queue.get(),
                                timeout=self.config.recv_timeout,
                            )
                            if resp is None: # 结束标记
                                break

                            # 心跳包仅用于重置超时，不转发给用户
                            if resp.type == ResponseType.HEARTBEAT:
                                continue

                            yield resp
                            if resp.type == ResponseType.ERROR:
                                break

                        except asyncio.TimeoutError:
                            break

                    await send_task
                finally:
                    send_task.cancel()
                    recv_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await send_task
                    with contextlib.suppress(asyncio.CancelledError):
                        await recv_task

                if state.transport_closed_unexpectedly:
                    raise ASRTransportError("WebSocket 在最终结果前意外断开")

        except websockets.exceptions.WebSocketException as e:
            raise ASRTransportError(f"WebSocket 错误: {e}") from e


    async def transcribe_realtime(
        self,
        audio_source: AsyncIterator[AudioChunk],
    ) -> AsyncIterator[ASRResponse]:
        """
        实时流式语音识别（支持麦克风等持续音频源）

        :param audio_source: PCM 音频数据的异步迭代器
            - 每个 chunk 应为 16-bit PCM 数据
            - 采样率和声道数应与 config 中配置一致
            - 迭代器结束时会自动发送 FinishSession
        :return: ASR 响应流
        """
        state = _SessionState()

        try:
            async with websockets.connect(
                self.config.ws_url,
                additional_headers=self.config.headers,
                open_timeout=self.config.connect_timeout,
            ) as ws:
                # 初始化会话
                async for resp in self._initialize_session(ws, state):
                    yield resp

                # 响应队列
                response_queue: asyncio.Queue[Optional[ASRResponse]] = asyncio.Queue()

                # 启动发送和接收任务
                send_task = asyncio.create_task(
                    self._send_audio_realtime(ws, audio_source, state)
                )
                recv_task = asyncio.create_task(
                    self._receive_responses(ws, state, response_queue)
                )

                try:
                    while True:
                        if send_task.done():
                            send_exc = send_task.exception()
                            if send_exc is not None:
                                raise send_exc
                            resp = await asyncio.wait_for(
                                response_queue.get(),
                                timeout=self.config.recv_timeout,
                            )
                        else:
                            resp = await response_queue.get()
                        if resp is None:
                            break

                        if resp.type == ResponseType.HEARTBEAT:
                            continue

                        yield resp
                        if resp.type == ResponseType.ERROR:
                            break

                    await send_task
                except asyncio.TimeoutError as exc:
                    raise ASRTransportError(
                        f"等待最终结果超时({self.config.recv_timeout:.1f}s)"
                    ) from exc
                finally:
                    send_task.cancel()
                    recv_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await send_task
                    with contextlib.suppress(asyncio.CancelledError):
                        await recv_task

                if state.transport_closed_unexpectedly:
                    raise ASRTransportError("WebSocket 在最终结果前意外断开")

        except websockets.exceptions.WebSocketException as e:
            raise ASRTransportError(f"WebSocket 错误: {e}") from e

    async def _send_audio_realtime(
        self,
        ws: ClientConnection,
        audio_source: AsyncIterator[AudioChunk],
        state: _SessionState,
    ):
        """
        从异步迭代器读取 PCM 数据并实时发送
        """
        timestamp_ms = int(time.time() * 1000)
        frame_index = 0
        pcm_buffer = b""

        samples_per_frame = (
            self.config.sample_rate * self.config.frame_duration_ms // 1000
        )
        bytes_per_frame = samples_per_frame * 2  # 16-bit

        try:
            # 使用超时机制防止迭代器阻塞导致会话泄漏
            while not state.is_finished:
                try:
                    chunk = await asyncio.wait_for(
                        audio_source.__anext__(),
                        timeout=self.config.frame_duration_ms * 2 / 1000
                    )
                except asyncio.TimeoutError:
                    continue
                except StopAsyncIteration:
                    break

                pcm_buffer += chunk

                # 当缓冲区有足够数据时，编码并发送
                while len(pcm_buffer) >= bytes_per_frame:
                    pcm_frame = pcm_buffer[:bytes_per_frame]
                    pcm_buffer = pcm_buffer[bytes_per_frame:]

                    # 编码为 Opus
                    opus_frame = self._encoder.encoder.encode(pcm_frame, samples_per_frame)

                    # 确定帧状态（实时模式下不知道最后一帧，使用 FIRST/MIDDLE）
                    if frame_index == 0:
                        frame_state = FrameState.FRAME_STATE_FIRST
                    else:
                        frame_state = FrameState.FRAME_STATE_MIDDLE

                    msg = _build_asr_request(
                        opus_frame,
                        state.request_id,
                        frame_state,
                        timestamp_ms + frame_index * self.config.frame_duration_ms,
                    )
                    await ws.send(msg)
                    frame_index += 1

        finally:
            # 【关键修复】无论正常结束还是异常中断，都必须发送结束帧和FinishSession
            # 否则服务器端会话会泄漏，导致 ExceededConcurrentQuota 错误
            if not state.is_finished and _connection_is_open(ws):
                try:
                    if pcm_buffer:
                        # 处理剩余数据
                        if len(pcm_buffer) < bytes_per_frame:
                            pcm_buffer += b"\x00" * (bytes_per_frame - len(pcm_buffer))
                        opus_frame = self._encoder.encoder.encode(pcm_buffer, samples_per_frame)
                        msg = _build_asr_request(
                            opus_frame,
                            state.request_id,
                            FrameState.FRAME_STATE_LAST,
                            timestamp_ms + frame_index * self.config.frame_duration_ms,
                        )
                        await ws.send(msg)
                    elif frame_index > 0:
                        # 发送空的LAST帧
                        silent_frame = b"\x00" * bytes_per_frame
                        opus_frame = self._encoder.encoder.encode(silent_frame, samples_per_frame)
                        msg = _build_asr_request(
                            opus_frame,
                            state.request_id,
                            FrameState.FRAME_STATE_LAST,
                            timestamp_ms + frame_index * self.config.frame_duration_ms,
                        )
                        await ws.send(msg)

                    # 发送FinishSession，这是最重要的！
                    await ws.send(_build_finish_session(state.request_id, self.config.get_token()))
                except (websockets.exceptions.WebSocketException, OSError, RuntimeError):
                    pass
    
    async def _initialize_session(self, ws: ClientConnection, state: _SessionState) -> AsyncIterator[ASRResponse]:
        """
        初始化 ASR 会话
        """
        token = self.config.get_token()

        # StartTask
        await ws.send(_build_start_task(state.request_id, token))
        resp = await ws.recv()
        parsed = _parse_response(resp)
        if parsed.type == ResponseType.ERROR:
            raise ASRError(f'StartTask 失败：{parsed.error_msg}', parsed)
        yield parsed

        # StartSession
        await ws.send(
            _build_start_session(state.request_id, token, self.config.session_config())
        )
        resp = await ws.recv()
        parsed = _parse_response(resp)
        if parsed.type == ResponseType.ERROR:
            raise ASRError(f'StartSession 失败：{parsed.error_msg}', parsed)
        yield parsed

    async def _send_audio(
        self,
        ws: ClientConnection,
        opus_frames: List[bytes],
        state: _SessionState,
        realtime: bool,
    ):
        """
        发送音频帧
        """
        timestamp_ms = int(time.time() * 1000)
        frame_interval = self.config.frame_duration_ms / 1000.0

        for i, opus_frame in enumerate(opus_frames):
            if state.is_finished:
                break

            if i == 0:
                frame_state = FrameState.FRAME_STATE_FIRST
            elif i == len(opus_frames) - 1:
                frame_state = FrameState.FRAME_STATE_LAST
            else:
                frame_state = FrameState.FRAME_STATE_MIDDLE
            
            msg = _build_asr_request(
                opus_frame,
                state.request_id,
                frame_state,
                timestamp_ms + i * self.config.frame_duration_ms,
            )
            await ws.send(msg)

            if realtime:
                await asyncio.sleep(frame_interval)
        
        # FinishSession
        await ws.send(_build_finish_session(state.request_id, self.config.get_token()))
    
    async def _receive_responses(
        self,
        ws: ClientConnection,
        state: _SessionState,
        queue: asyncio.Queue[Optional[ASRResponse]],
    ):
        """
        接受响应并放入队列
        """
        try:
            while not state.is_finished:
                response = await ws.recv()
                resp = _parse_response(response)

                if resp.type == ResponseType.ERROR:
                    state.error = resp
                    state.is_finished = True
                    await queue.put(resp)
                    break
                elif resp.type == ResponseType.HEARTBEAT:
                    # 心跳包也放入队列，用于重置超时计时器
                    await queue.put(resp)
                elif resp.type == ResponseType.SESSION_FINISHED:
                    state.received_session_finished = True
                    state.is_finished = True
                    await queue.put(resp)
                    break
                elif resp.type == ResponseType.FINAL_RESULT:
                    state.received_final = True
                    state.final_text = resp.text
                    await queue.put(resp)
                else:
                    await queue.put(resp)

        except websockets.exceptions.ConnectionClosed:
            if not state.received_final and not state.received_session_finished:
                state.transport_closed_unexpectedly = True
            state.is_finished = True
        finally:
            # 结束信号
            await queue.put(None)


async def probe_asr_session(config: ASRConfig) -> ASRProbeResult:
    """执行最小可用握手，用于会话启动前的健康检查。"""
    started_at = time.perf_counter()
    state = _SessionState()

    try:
        await asyncio.to_thread(config.ensure_credentials)
        token = config.get_token()
        session_config = config.session_config()
    except Exception as exc:
        return ASRProbeResult(
            ok=False,
            stage="credentials",
            message=str(exc) or exc.__class__.__name__,
            latency_ms=_latency_ms(started_at),
        )

    try:
        async with websockets.connect(
            config.ws_url,
            additional_headers=config.headers,
            open_timeout=config.connect_timeout,
        ) as ws:
            await ws.send(_build_start_task(state.request_id, token))
            start_task_response = _parse_response(await ws.recv())
            if start_task_response.type == ResponseType.ERROR:
                return ASRProbeResult(
                    ok=False,
                    stage="start_task",
                    message=start_task_response.error_msg or "StartTask 失败",
                    latency_ms=_latency_ms(started_at),
                )

            await ws.send(_build_start_session(state.request_id, token, session_config))
            start_session_response = _parse_response(await ws.recv())
            if start_session_response.type == ResponseType.ERROR:
                return ASRProbeResult(
                    ok=False,
                    stage="start_session",
                    message=start_session_response.error_msg or "StartSession 失败",
                    latency_ms=_latency_ms(started_at),
                )

            with contextlib.suppress(Exception):
                await ws.send(_build_finish_session(state.request_id, token))
    except websockets.exceptions.WebSocketException as exc:
        return ASRProbeResult(
            ok=False,
            stage="connect",
            message=str(exc) or exc.__class__.__name__,
            latency_ms=_latency_ms(started_at),
        )
    except Exception as exc:
        return ASRProbeResult(
            ok=False,
            stage="connect",
            message=str(exc) or exc.__class__.__name__,
            latency_ms=_latency_ms(started_at),
        )

    return ASRProbeResult(
        ok=True,
        stage="ok",
        latency_ms=_latency_ms(started_at),
    )

# =============
# 便捷函数
# =============


async def transcribe(
    audio: str | Path | bytes,
    *,
    config: ASRConfig | None = None,
    on_interim: Callable[[str], None] | None = None,
    realtime: bool = False,
) -> str:
    """
    便捷函数：非流式语音识别

    Args:
        audio: 音频文件路径或 PCM 字节数据
        config: ASR 配置（可选）
        on_interim: 中间结果回调（可选）
        realtime: 是否模拟实时语音输入
            - True: 按音频实际时长发送，每帧间插入延迟，模拟实时的语音输入
            - False（默认）: 尽快发送所有帧，会更快拿到结果（不知道会不会被风控）

    Returns:
        最终识别文本
    """
    async with DoubaoASR(config) as asr:
        return await asr.transcribe(audio, on_interim=on_interim, realtime=realtime)


async def transcribe_stream(
    audio: str | Path | bytes,
    *,
    config: ASRConfig | None = None,
    realtime: bool = False,
) -> AsyncIterator[ASRResponse]:
    """
    便捷函数：流式语音识别（完整音频）

    Args:
        audio: 音频文件路径或 PCM 字节数据
        config: ASR 配置（可选）
        realtime: 是否模拟实时语音输入
            - True: 按音频实际时长发送，每帧间插入延迟，模拟实时的语音输入
            - False（默认）: 尽快发送所有帧，会更快拿到结果（不知道会不会被风控）

    Yields:
        ASRResponse 对象
    """
    async with DoubaoASR(config) as asr:
        async for resp in asr.transcribe_stream(audio, realtime=realtime):
            yield resp


async def transcribe_realtime(
    audio_source: AsyncIterator[AudioChunk],
    *,
    config: ASRConfig | None = None,
) -> AsyncIterator[ASRResponse]:
    """
    便捷函数：实时流式语音识别（支持麦克风等持续音频源）

    Args:
        audio_source: PCM 音频数据的异步迭代器
            - 每个 chunk 应为 16-bit PCM 数据
            - 采样率和声道数应与 config 中配置一致
        config: ASR 配置（可选）

    Yields:
        ASRResponse 对象
    """
    async with DoubaoASR(config) as asr:
        async for resp in asr.transcribe_realtime(audio_source):
            yield resp
