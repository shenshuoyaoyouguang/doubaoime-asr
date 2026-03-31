"""语音输入事件类型测试。"""
from __future__ import annotations

import pytest

from doubaoime_asr.agent.events import (
    AudioLevelEvent,
    ConfigChangeEvent,
    ErrorEvent,
    FinalResultEvent,
    FinishedEvent,
    HotkeyPressEvent,
    HotkeyReleaseEvent,
    InterimResultEvent,
    ReadyEvent,
    RestartAsAdminEvent,
    StreamingStartedEvent,
    StopEvent,
    VoiceInputEvent,
    WorkerEventWrapper,
    WorkerExitEvent,
    WorkerReadyEvent,
    WorkerStatusEvent,
    parse_worker_event,
)


class TestBaseEvent:
    """基类 VoiceInputEvent 测试。"""

    def test_base_event_has_event_type(self) -> None:
        """基类必须有 event_type 字段。"""
        event = VoiceInputEvent(event_type="test")
        assert event.event_type == "test"

    def test_to_dict_basic(self) -> None:
        """基类序列化应包含 type 字段。"""
        event = VoiceInputEvent(event_type="test")
        result = event.to_dict()
        assert result == {"type": "test"}

    def test_from_dict_unknown_type_returns_base(self) -> None:
        """未知类型应返回基类实例。"""
        event = VoiceInputEvent.from_dict({"type": "unknown_event"})
        assert isinstance(event, VoiceInputEvent)
        assert event.event_type == "unknown_event"


class TestHotkeyEvents:
    """热键事件测试。"""

    def test_hotkey_press_creation(self) -> None:
        """热键按下事件创建。"""
        event = HotkeyPressEvent()
        assert event.event_type == "press"

    def test_hotkey_release_creation(self) -> None:
        """热键释放事件创建。"""
        event = HotkeyReleaseEvent()
        assert event.event_type == "release"

    def test_hotkey_press_serialization(self) -> None:
        """热键按下事件序列化。"""
        event = HotkeyPressEvent()
        result = event.to_dict()
        assert result == {"type": "press"}

    def test_hotkey_press_deserialization(self) -> None:
        """热键按下事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "press"})
        assert isinstance(event, HotkeyPressEvent)

    def test_hotkey_release_deserialization(self) -> None:
        """热键释放事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "release"})
        assert isinstance(event, HotkeyReleaseEvent)


class TestControllerEvents:
    """Controller 内部事件测试。"""

    def test_config_change_event(self) -> None:
        """配置变更事件。"""
        event = ConfigChangeEvent()
        assert event.event_type == "apply_config"
        assert event.config is None

    def test_config_change_with_config(self) -> None:
        """带配置对象的配置变更事件。"""
        mock_config = {"hotkey": "f9"}
        event = ConfigChangeEvent(config=mock_config)
        assert event.config == mock_config

    def test_config_change_serialization(self) -> None:
        """配置变更事件序列化（不包含 config）。"""
        event = ConfigChangeEvent(config={"hotkey": "f9"})
        result = event.to_dict()
        assert result == {"type": "apply_config"}

    def test_restart_as_admin_event(self) -> None:
        """管理员重启事件。"""
        event = RestartAsAdminEvent()
        assert event.event_type == "restart_as_admin"

    def test_restart_as_admin_deserialization(self) -> None:
        """管理员重启事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "restart_as_admin"})
        assert isinstance(event, RestartAsAdminEvent)

    def test_stop_event(self) -> None:
        """停止事件。"""
        event = StopEvent()
        assert event.event_type == "stop"

    def test_stop_event_deserialization(self) -> None:
        """停止事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "stop"})
        assert isinstance(event, StopEvent)


class TestWorkerReadyEvents:
    """Worker 就绪事件测试。"""

    def test_worker_ready_event(self) -> None:
        """Worker 进程就绪事件。"""
        event = WorkerReadyEvent()
        assert event.event_type == "worker_ready"

    def test_worker_ready_deserialization(self) -> None:
        """Worker 进程就绪事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "worker_ready"})
        assert isinstance(event, WorkerReadyEvent)

    def test_ready_event(self) -> None:
        """录音就绪事件。"""
        event = ReadyEvent()
        assert event.event_type == "ready"

    def test_ready_deserialization(self) -> None:
        """录音就绪事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "ready"})
        assert isinstance(event, ReadyEvent)


class TestWorkerStatusEvent:
    """Worker 状态消息事件测试。"""

    def test_status_event_empty_message(self) -> None:
        """空消息状态事件。"""
        event = WorkerStatusEvent()
        assert event.event_type == "status"
        assert event.message == ""

    def test_status_event_with_message(self) -> None:
        """带消息的状态事件。"""
        event = WorkerStatusEvent(message="正在录音")
        assert event.message == "正在录音"

    def test_status_serialization(self) -> None:
        """状态事件序列化。"""
        event = WorkerStatusEvent(message="正在录音")
        result = event.to_dict()
        assert result == {"type": "status", "message": "正在录音"}

    def test_status_deserialization(self) -> None:
        """状态事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "status", "message": "正在录音"})
        assert isinstance(event, WorkerStatusEvent)
        assert event.message == "正在录音"

    def test_status_missing_message(self) -> None:
        """缺失消息字段的状态事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "status"})
        assert isinstance(event, WorkerStatusEvent)
        assert event.message == ""


class TestAudioLevelEvent:
    """音频级别事件测试。"""

    def test_audio_level_default(self) -> None:
        """默认音频级别。"""
        event = AudioLevelEvent()
        assert event.event_type == "audio_level"
        assert event.level == 0.0

    def test_audio_level_with_value(self) -> None:
        """带值的音频级别事件。"""
        event = AudioLevelEvent(level=0.75)
        assert event.level == 0.75

    def test_audio_level_serialization(self) -> None:
        """音频级别事件序列化。"""
        event = AudioLevelEvent(level=0.5)
        result = event.to_dict()
        assert result == {"type": "audio_level", "level": 0.5}

    def test_audio_level_deserialization(self) -> None:
        """音频级别事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "audio_level", "level": 0.3})
        assert isinstance(event, AudioLevelEvent)
        assert event.level == 0.3

    def test_audio_level_invalid_value(self) -> None:
        """无效音频级别值反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "audio_level", "level": "invalid"})
        assert isinstance(event, AudioLevelEvent)
        assert event.level == 0.0

    def test_audio_level_missing_value(self) -> None:
        """缺失音频级别值反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "audio_level"})
        assert isinstance(event, AudioLevelEvent)
        assert event.level == 0.0


class TestInterimResultEvent:
    """中间识别结果事件测试。"""

    def test_interim_default(self) -> None:
        """默认中间结果事件。"""
        event = InterimResultEvent()
        assert event.event_type == "interim"
        assert event.text == ""
        assert event.segment_index is None

    def test_interim_with_text(self) -> None:
        """带文本的中间结果事件。"""
        event = InterimResultEvent(text="你好世界")
        assert event.text == "你好世界"

    def test_interim_with_segment_index(self) -> None:
        """带段索引的中间结果事件。"""
        event = InterimResultEvent(text="你好", segment_index=2)
        assert event.segment_index == 2

    def test_interim_serialization(self) -> None:
        """中间结果事件序列化。"""
        event = InterimResultEvent(text="你好世界", segment_index=1)
        result = event.to_dict()
        assert result == {"type": "interim", "text": "你好世界", "segment_index": 1}

    def test_interim_serialization_no_index(self) -> None:
        """无段索引的中间结果事件序列化。"""
        event = InterimResultEvent(text="你好")
        result = event.to_dict()
        assert result == {"type": "interim", "text": "你好"}

    def test_interim_deserialization(self) -> None:
        """中间结果事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "interim", "text": "测试文本", "segment_index": 3})
        assert isinstance(event, InterimResultEvent)
        assert event.text == "测试文本"
        assert event.segment_index == 3

    def test_interim_invalid_segment_index(self) -> None:
        """无效段索引反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "interim", "text": "测试", "segment_index": "invalid"})
        assert isinstance(event, InterimResultEvent)
        assert event.segment_index is None


class TestFinalResultEvent:
    """最终识别结果事件测试。"""

    def test_final_default(self) -> None:
        """默认最终结果事件。"""
        event = FinalResultEvent()
        assert event.event_type == "final"
        assert event.text == ""
        assert event.segment_index is None

    def test_final_with_text(self) -> None:
        """带文本的最终结果事件。"""
        event = FinalResultEvent(text="最终结果文本")
        assert event.text == "最终结果文本"

    def test_final_with_segment_index(self) -> None:
        """带段索引的最终结果事件。"""
        event = FinalResultEvent(text="最终", segment_index=5)
        assert event.segment_index == 5

    def test_final_serialization(self) -> None:
        """最终结果事件序列化。"""
        event = FinalResultEvent(text="最终结果", segment_index=2)
        result = event.to_dict()
        assert result == {"type": "final", "text": "最终结果", "segment_index": 2}

    def test_final_deserialization(self) -> None:
        """最终结果事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "final", "text": "最终文本", "segment_index": 4})
        assert isinstance(event, FinalResultEvent)
        assert event.text == "最终文本"
        assert event.segment_index == 4


class TestErrorEvent:
    """错误事件测试。"""

    def test_error_default(self) -> None:
        """默认错误事件。"""
        event = ErrorEvent()
        assert event.event_type == "error"
        assert event.message == ""

    def test_error_with_message(self) -> None:
        """带消息的错误事件。"""
        event = ErrorEvent(message="麦克风初始化失败")
        assert event.message == "麦克风初始化失败"

    def test_error_serialization(self) -> None:
        """错误事件序列化。"""
        event = ErrorEvent(message="识别失败")
        result = event.to_dict()
        assert result == {"type": "error", "message": "识别失败"}

    def test_error_deserialization(self) -> None:
        """错误事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "error", "message": "网络错误"})
        assert isinstance(event, ErrorEvent)
        assert event.message == "网络错误"


class TestFinishedEvent:
    """识别完成事件测试。"""

    def test_finished_event(self) -> None:
        """识别完成事件。"""
        event = FinishedEvent()
        assert event.event_type == "finished"

    def test_finished_serialization(self) -> None:
        """识别完成事件序列化。"""
        event = FinishedEvent()
        result = event.to_dict()
        assert result == {"type": "finished"}

    def test_finished_deserialization(self) -> None:
        """识别完成事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "finished"})
        assert isinstance(event, FinishedEvent)


class TestWorkerExitEvent:
    """Worker 进程退出事件测试。"""

    def test_worker_exit_default(self) -> None:
        """默认 Worker 退出事件。"""
        event = WorkerExitEvent()
        assert event.event_type == "worker_exit"
        assert event.session_id == 0
        assert event.exit_code == 0

    def test_worker_exit_with_values(self) -> None:
        """带值的 Worker 退出事件。"""
        event = WorkerExitEvent(session_id=3, exit_code=1)
        assert event.session_id == 3
        assert event.exit_code == 1

    def test_worker_exit_serialization(self) -> None:
        """Worker 退出事件序列化。"""
        event = WorkerExitEvent(session_id=2, exit_code=0)
        result = event.to_dict()
        assert result == {"type": "worker_exit", "session_id": 2, "code": 0}

    def test_worker_exit_deserialization(self) -> None:
        """Worker 退出事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "worker_exit", "session_id": 5, "code": 2})
        assert isinstance(event, WorkerExitEvent)
        assert event.session_id == 5
        assert event.exit_code == 2

    def test_worker_exit_invalid_values(self) -> None:
        """无效值反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "worker_exit", "session_id": "invalid", "code": "invalid"})
        assert isinstance(event, WorkerExitEvent)
        assert event.session_id == 0
        assert event.exit_code == 0


class TestWorkerEventWrapper:
    """Worker 事件包装器测试。"""

    def test_worker_event_wrapper_default(self) -> None:
        """默认 Worker 事件包装器。"""
        event = WorkerEventWrapper()
        assert event.event_type == "worker_event"
        assert event.session_id == 0
        assert event.inner_event == {}

    def test_worker_event_wrapper_with_values(self) -> None:
        """带值的 Worker 事件包装器。"""
        inner = {"type": "interim", "text": "测试"}
        event = WorkerEventWrapper(session_id=1, inner_event=inner)
        assert event.session_id == 1
        assert event.inner_event == inner

    def test_worker_event_wrapper_serialization(self) -> None:
        """Worker 事件包装器序列化。"""
        inner = {"type": "status", "message": "录音中"}
        event = WorkerEventWrapper(session_id=2, inner_event=inner)
        result = event.to_dict()
        assert result == {"type": "worker_event", "session_id": 2, "event": inner}

    def test_worker_event_wrapper_deserialization(self) -> None:
        """Worker 事件包装器反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "worker_event", "session_id": 3, "event": {"type": "ready"}})
        assert isinstance(event, WorkerEventWrapper)
        assert event.session_id == 3
        assert event.inner_event == {"type": "ready"}

    def test_worker_event_wrapper_invalid_event(self) -> None:
        """无效 inner_event 反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "worker_event", "session_id": 1, "event": "not_a_dict"})
        assert isinstance(event, WorkerEventWrapper)
        assert event.inner_event == {}


class TestStreamingStartedEvent:
    """流式传输开始事件测试。"""

    def test_streaming_started_default(self) -> None:
        """默认流式传输开始事件。"""
        event = StreamingStartedEvent()
        assert event.event_type == "streaming_started"
        assert event.chunks == 0
        assert event.bytes == 0

    def test_streaming_started_with_values(self) -> None:
        """带值的流式传输开始事件。"""
        event = StreamingStartedEvent(chunks=10, bytes=3200)
        assert event.chunks == 10
        assert event.bytes == 3200

    def test_streaming_started_serialization(self) -> None:
        """流式传输开始事件序列化。"""
        event = StreamingStartedEvent(chunks=5, bytes=1600)
        result = event.to_dict()
        assert result == {"type": "streaming_started", "chunks": 5, "bytes": 1600}

    def test_streaming_started_deserialization(self) -> None:
        """流式传输开始事件反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "streaming_started", "chunks": 8, "bytes": 2560})
        assert isinstance(event, StreamingStartedEvent)
        assert event.chunks == 8
        assert event.bytes == 2560

    def test_streaming_started_invalid_values(self) -> None:
        """无效值反序列化。"""
        event = VoiceInputEvent.from_dict({"type": "streaming_started", "chunks": "invalid", "bytes": "invalid"})
        assert isinstance(event, StreamingStartedEvent)
        assert event.chunks == 0
        assert event.bytes == 0


class TestParseWorkerEvent:
    """parse_worker_event 函数测试。"""

    def test_parse_interim_event(self) -> None:
        """解析中间结果事件。"""
        event = parse_worker_event({"type": "interim", "text": "测试文本"})
        assert isinstance(event, InterimResultEvent)
        assert event.text == "测试文本"

    def test_parse_final_event(self) -> None:
        """解析最终结果事件。"""
        event = parse_worker_event({"type": "final", "text": "最终文本", "segment_index": 1})
        assert isinstance(event, FinalResultEvent)
        assert event.text == "最终文本"
        assert event.segment_index == 1

    def test_parse_audio_level_event(self) -> None:
        """解析音频级别事件。"""
        event = parse_worker_event({"type": "audio_level", "level": 0.5})
        assert isinstance(event, AudioLevelEvent)
        assert event.level == 0.5

    def test_parse_error_event(self) -> None:
        """解析错误事件。"""
        event = parse_worker_event({"type": "error", "message": "识别失败"})
        assert isinstance(event, ErrorEvent)
        assert event.message == "识别失败"

    def test_parse_finished_event(self) -> None:
        """解析完成事件。"""
        event = parse_worker_event({"type": "finished"})
        assert isinstance(event, FinishedEvent)

    def test_parse_unknown_event(self) -> None:
        """解析未知事件类型。"""
        event = parse_worker_event({"type": "unknown_type"})
        assert isinstance(event, VoiceInputEvent)
        assert event.event_type == "unknown_type"


class TestEventRoundtrip:
    """事件序列化/反序列化往返测试。"""

    def test_roundtrip_interim(self) -> None:
        """中间结果事件往返。"""
        original = InterimResultEvent(text="往返测试", segment_index=2)
        serialized = original.to_dict()
        restored = VoiceInputEvent.from_dict(serialized)
        assert isinstance(restored, InterimResultEvent)
        assert restored.text == original.text
        assert restored.segment_index == original.segment_index

    def test_roundtrip_audio_level(self) -> None:
        """音频级别事件往返。"""
        original = AudioLevelEvent(level=0.85)
        serialized = original.to_dict()
        restored = VoiceInputEvent.from_dict(serialized)
        assert isinstance(restored, AudioLevelEvent)
        assert restored.level == original.level

    def test_roundtrip_worker_status(self) -> None:
        """Worker 状态事件往返。"""
        original = WorkerStatusEvent(message="正在录音中")
        serialized = original.to_dict()
        restored = VoiceInputEvent.from_dict(serialized)
        assert isinstance(restored, WorkerStatusEvent)
        assert restored.message == original.message

    def test_roundtrip_error(self) -> None:
        """错误事件往返。"""
        original = ErrorEvent(message="网络超时")
        serialized = original.to_dict()
        restored = VoiceInputEvent.from_dict(serialized)
        assert isinstance(restored, ErrorEvent)
        assert restored.message == original.message

    def test_roundtrip_streaming_started(self) -> None:
        """流式传输开始事件往返。"""
        original = StreamingStartedEvent(chunks=15, bytes=4800)
        serialized = original.to_dict()
        restored = VoiceInputEvent.from_dict(serialized)
        assert isinstance(restored, StreamingStartedEvent)
        assert restored.chunks == original.chunks
        assert restored.bytes == original.bytes


class TestAllEventsHaveSlots:
    """验证所有事件类使用 slots=True。"""

    def test_base_event_has_slots(self) -> None:
        """基类有 __slots__。"""
        assert hasattr(VoiceInputEvent, "__slots__")

    def test_hotkey_press_has_slots(self) -> None:
        """HotkeyPressEvent 有 __slots__。"""
        assert hasattr(HotkeyPressEvent, "__slots__")

    def test_all_events_have_slots(self) -> None:
        """所有事件类都有 __slots__。"""
        event_classes = [
            HotkeyPressEvent,
            HotkeyReleaseEvent,
            ConfigChangeEvent,
            RestartAsAdminEvent,
            StopEvent,
            WorkerReadyEvent,
            WorkerStatusEvent,
            AudioLevelEvent,
            InterimResultEvent,
            FinalResultEvent,
            ErrorEvent,
            FinishedEvent,
            WorkerExitEvent,
            WorkerEventWrapper,
            ReadyEvent,
            StreamingStartedEvent,
        ]
        for cls in event_classes:
            assert hasattr(cls, "__slots__"), f"{cls.__name__} should have __slots__"