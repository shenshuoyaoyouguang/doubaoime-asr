from __future__ import annotations

from doubaoime_asr.agent.transcript_utils import (
    TranscriptAccumulator,
    concat_transcript_text,
    suffix_prefix_overlap,
)


def test_transcript_accumulator_record_text_tracks_active_and_final_segments() -> None:
    transcript = TranscriptAccumulator()

    interim_text = transcript.record_text("你好", segment_index=None, is_final=False)
    final_text = transcript.record_text("你好世界", segment_index=0, is_final=True)

    assert interim_text == "你好"
    assert final_text == "你好世界"
    assert transcript.active_segment_index is None
    assert transcript.finalized_segment_indexes == {0}


def test_transcript_accumulator_aggregate_text_merges_in_order() -> None:
    transcript = TranscriptAccumulator(segment_texts={0: "你好", 1: "世界"})

    assert transcript.aggregate_text() == "你好世界"


def test_concat_transcript_text_adds_space_for_ascii_words() -> None:
    assert concat_transcript_text("hello", "world") == "hello world"


def test_suffix_prefix_overlap_returns_longest_overlap() -> None:
    assert suffix_prefix_overlap("你好世", "世界") == 1
