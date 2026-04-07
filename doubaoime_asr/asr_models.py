from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional


class ResponseType(Enum):
    """ASR 响应类型。"""

    TASK_STARTED = auto()
    SESSION_STARTED = auto()
    SESSION_FINISHED = auto()
    VAD_START = auto()
    INTERIM_RESULT = auto()
    FINAL_RESULT = auto()
    HEARTBEAT = auto()
    ERROR = auto()
    UNKNOWN = auto()


@dataclass
class ASRWord:
    """单词级别的识别结果。"""

    word: str
    start_time: float
    end_time: float


@dataclass
class OIDecodingInfo:
    """OI 解码信息。"""

    oi_former_word_num: int = 0
    oi_latter_word_num: int = 0
    oi_words: Optional[List] = None


@dataclass
class ASRAlternative:
    """识别候选结果。"""

    text: str
    start_time: float
    end_time: float
    words: List[ASRWord] = field(default_factory=list)
    semantic_related_to_prev: Optional[bool] = None
    oi_decoding_info: Optional[OIDecodingInfo] = None


@dataclass
class ASRResult:
    """单条识别结果。"""

    text: str
    start_time: float
    end_time: float
    confidence: float = 0.0
    alternatives: List[ASRAlternative] = field(default_factory=list)
    is_interim: bool = True
    is_vad_finished: bool = False
    index: int = 0


@dataclass
class ASRExtra:
    """响应附加信息。"""

    audio_duration: Optional[int] = None
    model_avg_rtf: Optional[float] = None
    model_send_first_response: Optional[int] = None
    speech_adaptation_version: Optional[str] = None
    model_total_process_time: Optional[int] = None
    packet_number: Optional[int] = None
    vad_start: Optional[bool] = None
    req_payload: Optional[dict] = None


@dataclass
class ASRResponse:
    """ASR 响应。"""

    type: ResponseType
    text: str = ""
    is_final: bool = False
    vad_start: bool = False
    vad_finished: bool = False
    packet_number: int = -1
    error_msg: str = ""
    raw_json: Optional[dict] = None
    results: List[ASRResult] = field(default_factory=list)
    extra: Optional[ASRExtra] = None


class ASRError(Exception):
    """ASR 错误。"""

    def __init__(self, message: str, response: Optional[ASRResponse] = None) -> None:
        super().__init__(message)
        self.response = response


class ASRTransportError(ASRError):
    """可重试的传输层错误。"""


@dataclass
class ASRProbeResult:
    ok: bool
    stage: str
    message: str = ""
    latency_ms: int = 0
