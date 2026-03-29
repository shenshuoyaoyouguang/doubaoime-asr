import asyncio
import logging

import pytest

from doubaoime_asr.agent.overlay_scheduler import OverlayRenderScheduler


class _Preview:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def show(self, text: str, *, seq: int = 0, kind: str = "interim") -> None:
        self.calls.append(("show", (text, seq, kind)))

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

    assert preview.calls[0] == ("show", ("abc", 1, "interim"))


@pytest.mark.asyncio
async def test_overlay_scheduler_hide_clears_pending():
    preview = _Preview()
    scheduler = OverlayRenderScheduler(preview, logger=logging.getLogger("overlay-scheduler"), fps=30)

    await scheduler.submit_interim("hello")
    await scheduler.hide("finished")

    assert preview.calls[-1] == ("hide", ("finished",))
