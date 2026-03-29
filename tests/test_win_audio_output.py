from __future__ import annotations

import logging
import threading
import time

import pytest

from doubaoime_asr.agent import win_audio_output
from doubaoime_asr.agent.config import CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT


def test_activate_is_atomic_across_threads(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(win_audio_output.sys, "platform", "win32")
    calls: list[bool] = []
    guard = win_audio_output.SystemOutputMuteGuard(
        logging.getLogger("win-audio-test"),
        policy=CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT,
    )
    barrier = threading.Barrier(2)

    def fake_set_default_output_muted(muted: bool) -> win_audio_output._MutedEndpointState:
        calls.append(muted)
        time.sleep(0.05)
        return win_audio_output._MutedEndpointState(endpoint_id="speaker-1", previous_muted=False)

    monkeypatch.setattr(win_audio_output, "_set_default_output_muted", fake_set_default_output_muted)
    results: list[bool] = []

    def worker() -> None:
        barrier.wait()
        results.append(guard.activate())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [True, True]
    assert calls == [True]


def test_release_is_atomic_across_threads(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(win_audio_output.sys, "platform", "win32")
    guard = win_audio_output.SystemOutputMuteGuard(
        logging.getLogger("win-audio-test"),
        policy=CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT,
    )
    guard._state = win_audio_output._MutedEndpointState(endpoint_id="speaker-1", previous_muted=False)
    barrier = threading.Barrier(2)
    restore_calls: list[str] = []

    def fake_restore_output_mute_state(state: win_audio_output._MutedEndpointState) -> None:
        restore_calls.append(state.endpoint_id)
        time.sleep(0.05)

    monkeypatch.setattr(win_audio_output, "_restore_output_mute_state", fake_restore_output_mute_state)
    results: list[bool] = []

    def worker() -> None:
        barrier.wait()
        results.append(guard.release())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
    assert restore_calls == ["speaker-1"]


def test_release_clears_stale_state_after_restore_failure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(win_audio_output.sys, "platform", "win32")
    mute_calls: list[str] = []
    guard = win_audio_output.SystemOutputMuteGuard(
        logging.getLogger("win-audio-test"),
        policy=CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT,
    )

    def fake_set_default_output_muted(muted: bool) -> win_audio_output._MutedEndpointState:
        endpoint_id = f"speaker-{len(mute_calls) + 1}"
        mute_calls.append(endpoint_id)
        return win_audio_output._MutedEndpointState(endpoint_id=endpoint_id, previous_muted=False)

    monkeypatch.setattr(win_audio_output, "_set_default_output_muted", fake_set_default_output_muted)
    monkeypatch.setattr(
        win_audio_output,
        "_restore_output_mute_state",
        lambda state: (_ for _ in ()).throw(RuntimeError(f"restore failed: {state.endpoint_id}")),
    )

    assert guard.activate() is True
    with pytest.raises(RuntimeError, match="restore failed: speaker-1"):
        guard.release()
    assert guard.activate() is True
    assert mute_calls == ["speaker-1", "speaker-2"]


def test_com_scope_tolerates_changed_thread_mode(monkeypatch: pytest.MonkeyPatch):
    class _FakeOle32:
        def __init__(self) -> None:
            self.uninitialize_calls = 0

        def CoInitializeEx(self, _reserved, _coinit):
            return win_audio_output._RPC_E_CHANGED_MODE

        def CoUninitialize(self):
            self.uninitialize_calls += 1

    fake_ole32 = _FakeOle32()
    monkeypatch.setattr(win_audio_output, "_ole32", lambda: fake_ole32)

    with win_audio_output._com_scope():
        pass

    assert fake_ole32.uninitialize_calls == 0
