from __future__ import annotations

import asyncio
import io
import logging

from doubaoime_asr.agent import worker_main


def test_start_stdin_reader_enqueues_exit_on_eof(monkeypatch):
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()
    monkeypatch.setattr(worker_main.sys, "stdin", io.StringIO(""))

    try:
        thread = worker_main._start_stdin_reader(loop, queue, logging.getLogger("worker-main-test"))
        thread.join(timeout=1)
        assert not thread.is_alive()
        loop.run_until_complete(asyncio.sleep(0))
        assert loop.run_until_complete(queue.get()) == "EXIT"
    finally:
        loop.close()
