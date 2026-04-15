"""
Coordinator Transcript Flow 模块。

提供 Transcript 累积、中间结果处理等功能。
"""
from __future__ import annotations

import hashlib

from .events import VoiceInputEvent
from .interim_dispatcher import DebouncedInterimDispatcher
from .transcript_utils import concat_transcript_text

__all__ = [
    "record_session_text",
    "resolve_segment_index",
    "next_segment_index",
    "aggregate_session_text",
    "submit_interim_snapshot",
    "flush_interim_snapshot",
    "flush_interim_dispatcher",
    "close_interim_dispatcher",
    "ensure_interim_dispatcher",
    "concat_transcript_text_module",
    "current_target_profile",
    "text_digest",
    # Transcript 兼容属性
    "segment_texts_property",
    "finalized_segment_indexes_property",
    "active_segment_index_property",
    "last_displayed_raw_final_text_property",
]


def _make_segment_texts_property():
    """创建 _segment_texts 属性。"""
    @property
    def getter(self):
        return self._transcript.segment_texts

    @getter.setter
    def setter(self, value):
        self._transcript.segment_texts = value

    return property(getter, setter)


def _make_finalized_segment_indexes_property():
    """创建 _finalized_segment_indexes 属性。"""
    @property
    def getter(self):
        return self._transcript.finalized_segment_indexes

    @getter.setter
    def setter(self, value):
        self._transcript.finalized_segment_indexes = value

    return property(getter, setter)


def _make_active_segment_index_property():
    """创建 _active_segment_index 属性。"""
    @property
    def getter(self):
        return self._transcript.active_segment_index

    @getter.setter
    def setter(self, value):
        self._transcript.active_segment_index = value

    return property(getter, setter)


def _make_last_displayed_raw_final_text_property():
    """创建 _last_displayed_raw_final_text 属性。"""
    @property
    def getter(self):
        return self._transcript.last_displayed_raw_final_text

    @getter.setter
    def setter(self, value):
        self._transcript.last_displayed_raw_final_text = value

    return property(getter, setter)


def _inject_transcript_property(cls):
    """为类注入 Transcript 相关属性。"""
    cls._segment_texts = property(
        lambda self: self._transcript.segment_texts,
        lambda self, value: setattr(self._transcript, "segment_texts", value),
    )
    cls._finalized_segment_indexes = property(
        lambda self: self._transcript.finalized_segment_indexes,
        lambda self, value: setattr(self._transcript, "finalized_segment_indexes", value),
    )
    cls._active_segment_index = property(
        lambda self: self._transcript.active_segment_index,
        lambda self, value: setattr(self._transcript, "active_segment_index", value),
    )
    cls._last_displayed_raw_final_text = property(
        lambda self: self._transcript.last_displayed_raw_final_text,
        lambda self, value: setattr(self._transcript, "last_displayed_raw_final_text", value),
    )
    return cls


# ===== 模块级函数 =====


def record_session_text(
    coordinator,
    event: VoiceInputEvent,
    text: str,
    *,
    is_final: bool,
) -> str:
    """记录分段文本。"""
    return coordinator._transcript.record_text(
        text,
        segment_index=getattr(event, "segment_index", None),
        is_final=is_final,
    )


def resolve_segment_index(coordinator, event: VoiceInputEvent, *, is_final: bool) -> int:
    """解析分段索引。"""
    return coordinator._transcript.resolve_segment_index(
        getattr(event, "segment_index", None),
        is_final=is_final,
    )


def next_segment_index(coordinator) -> int:
    """获取下一个分段索引。"""
    return coordinator._transcript.next_segment_index()


def aggregate_session_text(coordinator) -> str:
    """聚合分段文本。"""
    return coordinator._transcript.aggregate_text()


async def submit_interim_snapshot(coordinator, text: str) -> int:
    """提交中间结果快照。"""
    dispatcher = ensure_interim_dispatcher(coordinator)
    return await dispatcher.submit(text)


async def flush_interim_snapshot(coordinator, seq: int, text: str) -> None:
    """刷新中间结果快照。"""
    coordinator._last_interim_flush_seq = seq
    coordinator.logger.info(
        "interim_snapshot_flushed seq=%s len=%s digest=%s inline_enabled=%s text_profile=%s",
        seq,
        len(text),
        text_digest(text),
        coordinator.injection_service.is_inline_streaming_enabled(),
        current_target_profile(coordinator),
    )
    await coordinator.overlay_service.submit_interim(text)
    await coordinator.injection_service.apply_inline_interim(text)


async def flush_interim_dispatcher(coordinator, *, reason: str) -> None:
    """刷新中间结果调度器。"""
    if coordinator._interim_dispatcher is None:
        return
    await coordinator._interim_dispatcher.flush(reason=reason)


async def close_interim_dispatcher(coordinator) -> None:
    """关闭中间结果调度器。"""
    if coordinator._interim_dispatcher is None:
        return
    await coordinator._interim_dispatcher.close()
    coordinator._interim_dispatcher = None


def ensure_interim_dispatcher(coordinator) -> DebouncedInterimDispatcher:
    """确保中间结果调度器存在。"""
    if coordinator._interim_dispatcher is None:
        coordinator._interim_dispatcher = DebouncedInterimDispatcher(
            debounce_ms=coordinator.config.render_debounce_ms,
            logger=coordinator.logger,
            on_flush=lambda seq, text: flush_interim_snapshot(coordinator, seq, text),
        )
    return coordinator._interim_dispatcher


def current_target_profile(coordinator) -> str:
    """获取当前目标的文本输入配置。"""
    target = coordinator.injection_service.get_current_target()
    if target is None:
        return "none"
    return target.text_input_profile


def text_digest(text: str) -> str:
    """计算文本摘要（SHA1 前10位）。"""
    if not text:
        return "empty"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def concat_transcript_text_module(current: str, incoming: str) -> str:
    """拼接文本，处理重叠。"""
    return concat_transcript_text(current, incoming)