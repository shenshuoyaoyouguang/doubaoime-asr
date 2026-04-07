from __future__ import annotations

import json

from doubaoime_asr import ASRResponse as PackageASRResponse
from doubaoime_asr import ResponseType as PackageResponseType
from doubaoime_asr.asr import ASRResponse as ModuleASRResponse
from doubaoime_asr.asr import ResponseType as ModuleResponseType
from doubaoime_asr.asr_pb2 import AsrRequest, AsrResponse, FrameState
from doubaoime_asr.asr_protocol import (
    build_asr_request,
    build_finish_session,
    build_start_session,
    build_start_task,
    parse_response,
)
from doubaoime_asr.config import ASRConfig


def _make_config() -> ASRConfig:
    config = ASRConfig(device_id="device-id", token="token")
    config.ensure_credentials = lambda: None  # type: ignore[method-assign]
    return config


def _serialize_response(
    *,
    message_type: str,
    result_json: str = "",
    status_message: str = "",
) -> bytes:
    response = AsrResponse()
    response.message_type = message_type
    response.result_json = result_json
    response.status_message = status_message
    return response.SerializeToString()


def test_build_start_task_preserves_wire_fields():
    message = AsrRequest()
    message.ParseFromString(build_start_task("request-1", "token-1"))

    assert message.token == "token-1"
    assert message.service_name == "ASR"
    assert message.method_name == "StartTask"
    assert message.request_id == "request-1"


def test_build_start_session_uses_session_config_json():
    config = _make_config().session_config()

    message = AsrRequest()
    message.ParseFromString(build_start_session("request-2", "token-2", config))

    assert message.token == "token-2"
    assert message.method_name == "StartSession"
    assert message.payload == config.model_dump_json()


def test_build_finish_session_preserves_wire_fields():
    message = AsrRequest()
    message.ParseFromString(build_finish_session("request-3", "token-3"))

    assert message.token == "token-3"
    assert message.method_name == "FinishSession"
    assert message.request_id == "request-3"


def test_build_asr_request_preserves_audio_frame_and_metadata():
    message = AsrRequest()
    message.ParseFromString(
        build_asr_request(
            b"audio-frame",
            "request-4",
            FrameState.FRAME_STATE_LAST,
            123456,
        )
    )

    assert message.service_name == "ASR"
    assert message.method_name == "TaskRequest"
    assert message.audio_data == b"audio-frame"
    assert message.request_id == "request-4"
    assert message.frame_state == FrameState.FRAME_STATE_LAST
    assert json.loads(message.payload) == {"extra": {}, "timestamp_ms": 123456}


def test_parse_response_preserves_public_response_types():
    parsed = parse_response(_serialize_response(message_type="TaskStarted"))

    assert isinstance(parsed, PackageASRResponse)
    assert PackageASRResponse is ModuleASRResponse
    assert PackageResponseType is ModuleResponseType
    assert parsed.type is PackageResponseType.TASK_STARTED


def test_parse_response_handles_status_error_messages():
    parsed = parse_response(
        _serialize_response(message_type="TaskFailed", status_message="boom")
    )

    assert parsed.type is PackageResponseType.ERROR
    assert parsed.error_msg == "boom"


def test_parse_response_handles_unknown_when_result_json_missing():
    parsed = parse_response(_serialize_response(message_type="CustomMessage"))

    assert parsed.type is PackageResponseType.UNKNOWN


def test_parse_response_handles_unknown_when_result_json_invalid():
    parsed = parse_response(
        _serialize_response(message_type="CustomMessage", result_json="{")
    )

    assert parsed.type is PackageResponseType.UNKNOWN


def test_parse_response_handles_heartbeat_without_results():
    parsed = parse_response(
        _serialize_response(
            message_type="CustomMessage",
            result_json=json.dumps({"extra": {"packet_number": 99}}),
        )
    )

    assert parsed.type is PackageResponseType.HEARTBEAT
    assert parsed.packet_number == 99
    assert parsed.extra is not None
    assert parsed.extra.packet_number == 99


def test_parse_response_handles_vad_start_before_text():
    parsed = parse_response(
        _serialize_response(
            message_type="CustomMessage",
            result_json=json.dumps(
                {
                    "results": [{"text": "hello", "is_interim": True, "index": 3}],
                    "extra": {"vad_start": True},
                }
            ),
        )
    )

    assert parsed.type is PackageResponseType.VAD_START
    assert parsed.vad_start is True
    assert parsed.results[0].index == 3


def test_parse_response_handles_final_result_via_vad_finished():
    parsed = parse_response(
        _serialize_response(
            message_type="CustomMessage",
            result_json=json.dumps(
                {
                    "results": [
                        {
                            "text": "done",
                            "is_interim": False,
                            "is_vad_finished": True,
                            "index": 7,
                        }
                    ],
                    "extra": {"packet_number": 5},
                }
            ),
        )
    )

    assert parsed.type is PackageResponseType.FINAL_RESULT
    assert parsed.is_final is True
    assert parsed.text == "done"
    assert parsed.results[0].index == 7


def test_parse_response_handles_final_result_via_nonstream_marker():
    parsed = parse_response(
        _serialize_response(
            message_type="CustomMessage",
            result_json=json.dumps(
                {
                    "results": [
                        {
                            "text": "nonstream",
                            "is_interim": True,
                            "extra": {"nonstream_result": True},
                        }
                    ],
                    "extra": {},
                }
            ),
        )
    )

    assert parsed.type is PackageResponseType.FINAL_RESULT
    assert parsed.text == "nonstream"


def test_parse_response_handles_interim_result_and_nested_models():
    parsed = parse_response(
        _serialize_response(
            message_type="CustomMessage",
            result_json=json.dumps(
                {
                    "results": [
                        {
                            "text": "partial",
                            "is_interim": True,
                            "index": 1,
                            "alternatives": [
                                {
                                    "text": "partial-alt",
                                    "start_time": 0.1,
                                    "end_time": 0.2,
                                    "words": [
                                        {
                                            "word": "partial",
                                            "start_time": 0.1,
                                            "end_time": 0.2,
                                        }
                                    ],
                                    "oi_decoding_info": {
                                        "oi_former_word_num": 1,
                                        "oi_latter_word_num": 2,
                                        "oi_words": ["a", "b"],
                                    },
                                }
                            ],
                        }
                    ],
                    "extra": {"audio_duration": 321},
                }
            ),
        )
    )

    assert parsed.type is PackageResponseType.INTERIM_RESULT
    assert parsed.text == "partial"
    assert parsed.results[0].alternatives[0].words[0].word == "partial"
    assert parsed.results[0].alternatives[0].oi_decoding_info is not None
    assert parsed.extra is not None
    assert parsed.extra.audio_duration == 321
