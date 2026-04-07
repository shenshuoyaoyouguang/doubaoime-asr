from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class TranscriptAccumulator:
    """管理识别分段文本及聚合逻辑。"""

    segment_texts: dict[int, str] = field(default_factory=dict)
    finalized_segment_indexes: set[int] = field(default_factory=set)
    active_segment_index: int | None = None
    last_displayed_raw_final_text: str = ""

    def reset(self) -> None:
        self.segment_texts.clear()
        self.finalized_segment_indexes.clear()
        self.active_segment_index = None
        self.last_displayed_raw_final_text = ""

    def record_text(
        self,
        text: str,
        *,
        segment_index: int | None,
        is_final: bool,
    ) -> str:
        index = self.resolve_segment_index(segment_index, is_final=is_final)
        self.segment_texts[index] = text
        return self.aggregate_text()

    def resolve_segment_index(self, segment_index: int | None, *, is_final: bool) -> int:
        if segment_index is None:
            segment_index = (
                self.active_segment_index
                if self.active_segment_index is not None
                else self.next_segment_index()
            )
        if is_final:
            self.finalized_segment_indexes.add(segment_index)
            if self.active_segment_index == segment_index:
                self.active_segment_index = None
        else:
            self.active_segment_index = segment_index
        return segment_index

    def next_segment_index(self) -> int:
        if not self.segment_texts:
            return 0
        return max(self.segment_texts) + 1

    def aggregate_text(self) -> str:
        text = ""
        for _, segment in sorted(self.segment_texts.items()):
            if not segment:
                continue
            text = concat_transcript_text(text, segment)
        return text


def concat_transcript_text(current: str, incoming: str) -> str:
    """拼接文本，处理重叠与英文单词边界。"""
    if not current:
        return incoming
    if not incoming:
        return current
    if incoming.startswith(current):
        return incoming
    if current.endswith(incoming):
        return current
    overlap = suffix_prefix_overlap(current, incoming)
    if overlap > 0:
        return current + incoming[overlap:]
    if (
        current[-1].isascii()
        and current[-1].isalnum()
        and incoming[0].isascii()
        and incoming[0].isalnum()
    ):
        return f"{current} {incoming}"
    return current + incoming


def suffix_prefix_overlap(left: str, right: str) -> int:
    """计算 left 后缀与 right 前缀的最大重叠长度。"""
    max_overlap = min(len(left), len(right))
    for size in range(max_overlap, 0, -1):
        if left[-size:] == right[:size]:
            return size
    return 0
