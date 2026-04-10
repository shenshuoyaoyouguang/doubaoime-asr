"""
语音输入事件类型定义。

替换原有 (kind, payload) tuple 格式，提供类型安全的事件处理。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union


@dataclass(slots=True)
class VoiceInputEvent:
    """所有语音输入事件的基类。"""

    event_type: str

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，用于 JSON 输出。"""
        result = {"type": self.event_type}
        # slots=True 的 dataclass 没有 __dict__，使用 __slots__ 遍历
        for slot in self.__slots__:
            if slot == "event_type":
                continue
            value = getattr(self, slot, None)
            if value is not None:
                result[slot] = value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VoiceInputEvent:
        """从字典反序列化事件。"""
        event_type = data.get("type", "")
        factory_cls = _EVENT_FACTORY.get(event_type)
        if factory_cls is None:
            # 未知类型返回基类实例
            return cls(event_type=event_type)
        return factory_cls._from_dict_impl(data)


# ===== Controller 内部事件 =====


@dataclass(slots=True)
class HotkeyPressEvent(VoiceInputEvent):
    """热键按下事件。"""

    event_type: str = field(default="press", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> HotkeyPressEvent:
        return HotkeyPressEvent()


@dataclass(slots=True)
class HotkeyReleaseEvent(VoiceInputEvent):
    """热键释放事件。"""

    event_type: str = field(default="release", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> HotkeyReleaseEvent:
        return HotkeyReleaseEvent()


@dataclass(slots=True)
class ConfigChangeEvent(VoiceInputEvent):
    """配置变更事件。"""

    config: Any = None
    preview_id: int = 0
    preview_only: bool = False
    event_type: str = field(default="apply_config", init=False)

    def to_dict(self) -> dict[str, Any]:
        # 配置对象不直接序列化，由 Controller 特殊处理
        result = {"type": self.event_type}
        if self.preview_only:
            result["preview_only"] = True
            result["preview_id"] = self.preview_id
        return result

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> ConfigChangeEvent:
        raw_preview_id = data.get("preview_id", 0)
        try:
            preview_id = int(raw_preview_id)
        except (TypeError, ValueError):
            preview_id = 0
        return ConfigChangeEvent(
            config=None,
            preview_id=preview_id,
            preview_only=bool(data.get("preview_only", False)),
        )


@dataclass(slots=True)
class RestartAsAdminEvent(VoiceInputEvent):
    """请求以管理员身份重启事件。"""

    event_type: str = field(default="restart_as_admin", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> RestartAsAdminEvent:
        return RestartAsAdminEvent()


@dataclass(slots=True)
class StopEvent(VoiceInputEvent):
    """停止事件。"""

    event_type: str = field(default="stop", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> StopEvent:
        return StopEvent()


# ===== Worker 事件 =====


@dataclass(slots=True)
class WorkerReadyEvent(VoiceInputEvent):
    """Worker 进程就绪事件。"""

    event_type: str = field(default="worker_ready", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> WorkerReadyEvent:
        return WorkerReadyEvent()


@dataclass(slots=True)
class WorkerStatusEvent(VoiceInputEvent):
    """Worker 状态消息事件。"""

    message: str = ""
    event_type: str = field(default="status", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> WorkerStatusEvent:
        return WorkerStatusEvent(message=str(data.get("message", "")))


@dataclass(slots=True)
class AudioLevelEvent(VoiceInputEvent):
    """音频级别事件。"""

    level: float = 0.0
    event_type: str = field(default="audio_level", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> AudioLevelEvent:
        try:
            level = float(data.get("level", 0.0))
        except (TypeError, ValueError):
            level = 0.0
        return AudioLevelEvent(level=level)


@dataclass(slots=True)
class InterimResultEvent(VoiceInputEvent):
    """中间识别结果事件。"""

    text: str = ""
    segment_index: int | None = None
    event_type: str = field(default="interim", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> InterimResultEvent:
        raw_index = data.get("segment_index")
        segment_index = None
        if raw_index is not None:
            try:
                segment_index = int(raw_index)
            except (TypeError, ValueError):
                pass
        return InterimResultEvent(
            text=str(data.get("text", "")),
            segment_index=segment_index,
        )


@dataclass(slots=True)
class FinalResultEvent(VoiceInputEvent):
    """最终识别结果事件。"""

    text: str = ""
    segment_index: int | None = None
    event_type: str = field(default="final", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> FinalResultEvent:
        raw_index = data.get("segment_index")
        segment_index = None
        if raw_index is not None:
            try:
                segment_index = int(raw_index)
            except (TypeError, ValueError):
                pass
        return FinalResultEvent(
            text=str(data.get("text", "")),
            segment_index=segment_index,
        )


@dataclass(slots=True)
class ErrorEvent(VoiceInputEvent):
    """错误事件。"""

    message: str = ""
    event_type: str = field(default="error", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> ErrorEvent:
        return ErrorEvent(message=str(data.get("message", "")))


@dataclass(slots=True)
class FinishedEvent(VoiceInputEvent):
    """识别完成事件。"""

    event_type: str = field(default="finished", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> FinishedEvent:
        return FinishedEvent()


@dataclass(slots=True)
class ServiceResolvedFinalEvent(VoiceInputEvent):
    """Service 解析出的最终提交文本。"""

    text: str = ""
    raw_text: str = ""
    applied_mode: str = ""
    fallback_reason: str | None = None
    committed_source: str = ""
    event_type: str = field(default="final_resolved", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> ServiceResolvedFinalEvent:
        fallback_reason = data.get("fallback_reason")
        return ServiceResolvedFinalEvent(
            text=str(data.get("text", "")),
            raw_text=str(data.get("raw_text", "")),
            applied_mode=str(data.get("applied_mode", "")),
            fallback_reason=str(fallback_reason) if fallback_reason is not None else None,
            committed_source=str(data.get("committed_source", "")),
        )


@dataclass(slots=True)
class FallbackRequiredEvent(VoiceInputEvent):
    """Service/TIP 要求切换 fallback。"""

    reason: str = ""
    source: str = ""
    event_type: str = field(default="fallback_required", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> FallbackRequiredEvent:
        return FallbackRequiredEvent(
            reason=str(data.get("reason", "")),
            source=str(data.get("source", "")),
        )


@dataclass(slots=True)
class WorkerExitEvent(VoiceInputEvent):
    """Worker 进程退出事件（Controller 内部使用）。"""

    session_id: int = 0
    exit_code: int = 0
    event_type: str = field(default="worker_exit", init=False)

    def to_dict(self) -> dict[str, Any]:
        # worker_exit 由 Controller 内部构造，不来自 Worker
        return {"type": self.event_type, "session_id": self.session_id, "code": self.exit_code}

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> WorkerExitEvent:
        try:
            session_id = int(data.get("session_id", 0))
        except (TypeError, ValueError):
            session_id = 0
        try:
            exit_code = int(data.get("code", 0))
        except (TypeError, ValueError):
            exit_code = 0
        return WorkerExitEvent(session_id=session_id, exit_code=exit_code)


@dataclass(slots=True)
class WorkerEventWrapper(VoiceInputEvent):
    """Worker 事件包装器（Controller 内部使用，包装从 Worker 接收的事件）。"""

    session_id: int = 0
    inner_event: dict[str, Any] = field(default_factory=dict)
    event_type: str = field(default="worker_event", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.event_type, "session_id": self.session_id, "event": self.inner_event}

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> WorkerEventWrapper:
        try:
            session_id = int(data.get("session_id", 0))
        except (TypeError, ValueError):
            session_id = 0
        event = data.get("event", {})
        if not isinstance(event, dict):
            event = {}
        return WorkerEventWrapper(session_id=session_id, inner_event=event)


@dataclass(slots=True)
class ReadyEvent(VoiceInputEvent):
    """录音就绪事件（Worker 发送）。"""

    event_type: str = field(default="ready", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> ReadyEvent:
        return ReadyEvent()


@dataclass(slots=True)
class StreamingStartedEvent(VoiceInputEvent):
    """流式传输开始事件。"""

    chunks: int = 0
    bytes: int = 0
    event_type: str = field(default="streaming_started", init=False)

    @classmethod
    def _from_dict_impl(cls, data: dict[str, Any]) -> StreamingStartedEvent:
        try:
            chunks = int(data.get("chunks", 0))
        except (TypeError, ValueError):
            chunks = 0
        try:
            bytes_val = int(data.get("bytes", 0))
        except (TypeError, ValueError):
            bytes_val = 0
        return StreamingStartedEvent(chunks=chunks, bytes=bytes_val)


# ===== 事件工厂映射 =====

_EVENT_FACTORY: dict[str, type[VoiceInputEvent]] = {
    "press": HotkeyPressEvent,
    "release": HotkeyReleaseEvent,
    "apply_config": ConfigChangeEvent,
    "restart_as_admin": RestartAsAdminEvent,
    "stop": StopEvent,
    "worker_ready": WorkerReadyEvent,
    "ready": ReadyEvent,
    "streaming_started": StreamingStartedEvent,
    "status": WorkerStatusEvent,
    "audio_level": AudioLevelEvent,
    "interim": InterimResultEvent,
    "final": FinalResultEvent,
    "error": ErrorEvent,
    "finished": FinishedEvent,
    "final_resolved": ServiceResolvedFinalEvent,
    "fallback_required": FallbackRequiredEvent,
    "worker_exit": WorkerExitEvent,
    "worker_event": WorkerEventWrapper,
}


def parse_worker_event(data: dict[str, Any]) -> VoiceInputEvent:
    """解析 Worker 发送的事件字典为事件对象。"""
    return VoiceInputEvent.from_dict(data)


# 导出所有事件类型
__all__ = [
    "VoiceInputEvent",
    "HotkeyPressEvent",
    "HotkeyReleaseEvent",
    "ConfigChangeEvent",
    "RestartAsAdminEvent",
    "StopEvent",
    "WorkerReadyEvent",
    "WorkerStatusEvent",
    "AudioLevelEvent",
    "InterimResultEvent",
    "FinalResultEvent",
    "ErrorEvent",
    "FinishedEvent",
    "ServiceResolvedFinalEvent",
    "FallbackRequiredEvent",
    "WorkerExitEvent",
    "WorkerEventWrapper",
    "ReadyEvent",
    "StreamingStartedEvent",
    "parse_worker_event",
]
