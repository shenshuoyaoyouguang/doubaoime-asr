import asyncio
import logging

import pytest

from doubaoime_asr.agent.overlay_scheduler import OverlayRenderScheduler


class _Preview:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def show(
        self,
        text: str,
        *,
        seq: int = 0,
        kind: str = "interim",
        stable_prefix_utf16_len: int = 0,
        show_microphone: bool = False,
        level: float = 0.0,
    ) -> None:
        self.calls.append(("show", (text, seq, kind, stable_prefix_utf16_len, show_microphone, level)))

    def hide(self, reason: str = "") -> None:
        self.calls.append(("hide", (reason,)))

    def configure(self, config) -> None:
        self.calls.append(("configure", (config,)))


@pytest.mark.asyncio
async def test_overlay_scheduler_keeps_latest_interim():
    preview = _Preview()
    scheduler = OverlayRenderScheduler(preview, logger=logging.getLogger("overlay-scheduler"), fps=10)

    await scheduler.submit_interim("a")
    await scheduler.submit_interim("ab")
    await scheduler.submit_interim("abc")
    await asyncio.sleep(0.15)

    assert preview.calls[0] == ("show", ("abc", 1, "interim", 0, False, 0.0))


@pytest.mark.asyncio
async def test_overlay_scheduler_flushes_final_even_when_text_matches_latest_interim():
    preview = _Preview()
    scheduler = OverlayRenderScheduler(preview, logger=logging.getLogger("overlay-scheduler"), fps=30)

    await scheduler.submit_interim("hello")
    await asyncio.sleep(0.05)
    await scheduler.submit_final("hello", kind="final_raw")
    await asyncio.sleep(0.05)

    assert preview.calls[-1] == ("show", ("hello", 2, "final_raw", 5, False, 0.0))


@pytest.mark.asyncio
async def test_overlay_scheduler_hide_clears_pending():
    preview = _Preview()
    scheduler = OverlayRenderScheduler(preview, logger=logging.getLogger("overlay-scheduler"), fps=30)

    await scheduler.submit_interim("hello")
    await scheduler.hide("finished")

    assert preview.calls[-1] == ("hide", ("finished",))


@pytest.mark.asyncio
async def test_overlay_scheduler_flushes_microphone_placeholder():
    preview = _Preview()
    scheduler = OverlayRenderScheduler(preview, logger=logging.getLogger("overlay-scheduler"), fps=30)

    await scheduler.show_microphone()
    await asyncio.sleep(0.05)

    assert preview.calls[-1] == ("show", ("正在聆听…", 1, "listening", 5, True, 0.0))


@pytest.mark.asyncio
async def test_overlay_scheduler_updates_microphone_level_on_latest_frame():
    preview = _Preview()
    scheduler = OverlayRenderScheduler(preview, logger=logging.getLogger("overlay-scheduler"), fps=60)

    await scheduler.show_microphone()
    await asyncio.sleep(0.03)
    await scheduler.update_microphone_level(0.42)
    await asyncio.sleep(0.03)

    assert preview.calls[-1] == ("show", ("正在聆听…", 2, "listening", 5, True, 0.42))


@pytest.mark.asyncio
async def test_overlay_scheduler_keeps_microphone_visible_when_interim_arrives():
    preview = _Preview()
    scheduler = OverlayRenderScheduler(preview, logger=logging.getLogger("overlay-scheduler"), fps=60)

    await scheduler.show_microphone()
    await asyncio.sleep(0.03)
    await scheduler.update_microphone_level(0.31)
    await asyncio.sleep(0.03)
    await scheduler.submit_interim("你好")
    await asyncio.sleep(0.03)

    assert preview.calls[-1] == ("show", ("你好", 3, "interim", 0, True, 0.31))


@pytest.mark.asyncio
async def test_overlay_scheduler_preserves_pending_microphone_state_for_first_interim():
    preview = _Preview()
    scheduler = OverlayRenderScheduler(preview, logger=logging.getLogger("overlay-scheduler"), fps=60)

    await scheduler.show_microphone()
    await scheduler.update_microphone_level(0.27)
    await scheduler.submit_interim("首条文本")
    await asyncio.sleep(0.03)

    assert preview.calls[-1] == ("show", ("首条文本", 1, "interim", 0, True, 0.27))


@pytest.mark.asyncio
async def test_overlay_scheduler_removes_microphone_from_final_frames_after_stop():
    preview = _Preview()
    scheduler = OverlayRenderScheduler(preview, logger=logging.getLogger("overlay-scheduler"), fps=60)

    await scheduler.show_microphone()
    await asyncio.sleep(0.03)
    await scheduler.update_microphone_level(0.33)
    await asyncio.sleep(0.03)
    await scheduler.submit_interim("过渡文本")
    await asyncio.sleep(0.03)
    await scheduler.stop_microphone()
    await asyncio.sleep(0.03)
    await scheduler.submit_final("最终文本", kind="final_raw")
    await asyncio.sleep(0.03)

    assert preview.calls[-1] == ("show", ("最终文本", 5, "final_raw", 4, False, 0.0))


@pytest.mark.asyncio
async def test_overlay_scheduler_hides_listening_placeholder_when_stopped_before_transcript():
    preview = _Preview()
    scheduler = OverlayRenderScheduler(preview, logger=logging.getLogger("overlay-scheduler"), fps=60)

    await scheduler.show_microphone()
    await asyncio.sleep(0.03)
    await scheduler.stop_microphone()

    assert preview.calls[-1] == ("hide", ("stop_microphone_placeholder",))
