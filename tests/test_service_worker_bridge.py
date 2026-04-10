from doubaoime_asr.agent.service_worker_bridge import translate_worker_event_to_service_events


def test_translate_worker_interim_to_service_interim() -> None:
    events = translate_worker_event_to_service_events(
        {"type": "interim", "text": "你好", "segment_index": 2},
        session_id="s-1",
    )
    assert events[0]["type"] == "interim"
    assert events[0]["session_id"] == "s-1"
    assert events[0]["text"] == "你好"
    assert events[0]["segment_index"] == 2


def test_translate_worker_final_to_service_final_raw() -> None:
    events = translate_worker_event_to_service_events(
        {"type": "final", "text": "最终文本", "segment_index": 3},
        session_id="s-1",
    )
    assert events[0]["type"] == "final_raw"
    assert events[0]["text"] == "最终文本"


def test_translate_worker_finished_to_status() -> None:
    events = translate_worker_event_to_service_events(
        {"type": "finished"},
        session_id="s-1",
    )
    assert events[0]["type"] == "status"
    assert events[0]["code"] == "worker_finished"


def test_translate_worker_exit_to_error() -> None:
    events = translate_worker_event_to_service_events(
        {"type": "worker_exit", "code": 7},
        session_id="s-1",
    )
    assert events[0]["type"] == "error"
    assert events[0]["code"] == "worker_exit"
    assert events[0]["exit_code"] == 7
