from __future__ import annotations

import json
from typing import Optional

from .asr_models import (
    ASRAlternative,
    ASRExtra,
    ASRResponse,
    ASRResult,
    ASRWord,
    OIDecodingInfo,
    ResponseType,
)
from .asr_pb2 import AsrRequest, AsrResponse as AsrResponsePb, FrameState
from .config import SessionConfig


def build_start_task(request_id: str, token: str) -> bytes:
    """构建 StartTask 消息 pb 数据。"""
    request = AsrRequest()
    request.token = token
    request.service_name = "ASR"
    request.method_name = "StartTask"
    request.request_id = request_id
    return request.SerializeToString()


def build_start_session(request_id: str, token: str, config: SessionConfig) -> bytes:
    """构建 StartSession 消息 pb 数据。"""
    request = AsrRequest()
    request.token = token
    request.service_name = "ASR"
    request.method_name = "StartSession"
    request.request_id = request_id
    request.payload = config.model_dump_json()
    return request.SerializeToString()


def build_finish_session(request_id: str, token: str) -> bytes:
    """构建 FinishSession 消息 pb 数据。"""
    request = AsrRequest()
    request.token = token
    request.service_name = "ASR"
    request.method_name = "FinishSession"
    request.request_id = request_id
    return request.SerializeToString()


def build_asr_request(
    audio_data: bytes,
    request_id: str,
    frame_state: FrameState,
    timestamp_ms: int,
) -> bytes:
    """构建音频帧请求。"""
    request = AsrRequest()
    request.service_name = "ASR"
    request.method_name = "TaskRequest"
    request.payload = json.dumps({"extra": {}, "timestamp_ms": timestamp_ms})
    request.audio_data = audio_data
    request.request_id = request_id
    request.frame_state = frame_state
    return request.SerializeToString()


def parse_response(data: bytes) -> ASRResponse:
    """解析 ASR 响应（使用 protobuf）。"""
    pb = AsrResponsePb()
    pb.ParseFromString(data)

    if pb.message_type == "TaskStarted":
        return ASRResponse(type=ResponseType.TASK_STARTED)
    if pb.message_type == "SessionStarted":
        return ASRResponse(type=ResponseType.SESSION_STARTED)
    if pb.message_type == "SessionFinished":
        return ASRResponse(type=ResponseType.SESSION_FINISHED)
    if pb.message_type in ("TaskFailed", "SessionFailed"):
        return ASRResponse(type=ResponseType.ERROR, error_msg=pb.status_message)
    if not pb.result_json:
        return ASRResponse(type=ResponseType.UNKNOWN)

    try:
        json_data = json.loads(pb.result_json)
    except json.JSONDecodeError:
        return ASRResponse(type=ResponseType.UNKNOWN)

    results_raw = json_data.get("results")
    extra_raw = json_data.get("extra", {})
    parsed_extra = _parse_extra(extra_raw)

    if results_raw is None:
        return ASRResponse(
            type=ResponseType.HEARTBEAT,
            packet_number=extra_raw.get("packet_number", -1),
            raw_json=json_data,
            extra=parsed_extra,
        )

    parsed_results = [_parse_result(result) for result in results_raw]

    if extra_raw.get("vad_start"):
        return ASRResponse(
            type=ResponseType.VAD_START,
            vad_start=True,
            raw_json=json_data,
            results=parsed_results,
            extra=parsed_extra,
        )

    text = ""
    is_interim = True
    vad_finished = False
    nonstream_result = False
    for result in results_raw:
        if result.get("text"):
            text = result.get("text")
        if result.get("is_interim") is False:
            is_interim = False
        if result.get("is_vad_finished"):
            vad_finished = True
        if result.get("extra", {}).get("nonstream_result"):
            nonstream_result = True

    if nonstream_result or (not is_interim and vad_finished):
        return ASRResponse(
            type=ResponseType.FINAL_RESULT,
            text=text,
            is_final=True,
            vad_finished=vad_finished,
            raw_json=json_data,
            results=parsed_results,
            extra=parsed_extra,
        )

    return ASRResponse(
        type=ResponseType.INTERIM_RESULT,
        text=text,
        is_final=False,
        raw_json=json_data,
        results=parsed_results,
        extra=parsed_extra,
    )


def _parse_word(data: dict) -> ASRWord:
    return ASRWord(
        word=data.get("word", ""),
        start_time=data.get("start_time", 0.0),
        end_time=data.get("end_time", 0.0),
    )


def _parse_oi_decoding_info(data: Optional[dict]) -> Optional[OIDecodingInfo]:
    if data is None:
        return None
    return OIDecodingInfo(
        oi_former_word_num=data.get("oi_former_word_num", 0),
        oi_latter_word_num=data.get("oi_latter_word_num", 0),
        oi_words=data.get("oi_words"),
    )


def _parse_alternative(data: dict) -> ASRAlternative:
    return ASRAlternative(
        text=data.get("text", ""),
        start_time=data.get("start_time", 0.0),
        end_time=data.get("end_time", 0.0),
        words=[_parse_word(word) for word in data.get("words", [])],
        semantic_related_to_prev=data.get("semantic_related_to_prev"),
        oi_decoding_info=_parse_oi_decoding_info(data.get("oi_decoding_info")),
    )


def _parse_result(data: dict) -> ASRResult:
    return ASRResult(
        text=data.get("text", ""),
        start_time=data.get("start_time", 0.0),
        end_time=data.get("end_time", 0.0),
        confidence=data.get("confidence", 0.0),
        alternatives=[_parse_alternative(item) for item in data.get("alternatives", [])],
        is_interim=data.get("is_interim", True),
        is_vad_finished=data.get("is_vad_finished", False),
        index=data.get("index", 0),
    )


def _parse_extra(data: dict) -> ASRExtra:
    return ASRExtra(
        audio_duration=data.get("audio_duration"),
        model_avg_rtf=data.get("model_avg_rtf"),
        model_send_first_response=data.get("model_send_first_response"),
        speech_adaptation_version=data.get("speech_adaptation_version"),
        model_total_process_time=data.get("model_total_process_time"),
        packet_number=data.get("packet_number"),
        vad_start=data.get("vad_start"),
        req_payload=data.get("req_payload"),
    )
