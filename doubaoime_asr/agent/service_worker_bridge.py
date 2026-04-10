from __future__ import annotations

from typing import Any


def translate_worker_event_to_service_events(
    event_data: dict[str, Any],
    *,
    session_id: str,
) -> list[dict[str, Any]]:
    event_type = event_data.get("type")
    if not isinstance(event_type, str):
        return [{"type": "error", "session_id": session_id, "message": "worker event missing type"}]

    if event_type == "interim":
        return [
            {
                "type": "interim",
                "session_id": session_id,
                "text": event_data.get("text", ""),
                "segment_index": event_data.get("segment_index"),
            }
        ]

    if event_type == "final":
        return [
            {
                "type": "final_raw",
                "session_id": session_id,
                "text": event_data.get("text", ""),
                "segment_index": event_data.get("segment_index"),
            }
        ]

    if event_type == "status":
        return [
            {
                "type": "status",
                "session_id": session_id,
                "message": event_data.get("message", ""),
                "source": "worker",
            }
        ]

    if event_type == "error":
        return [
            {
                "type": "error",
                "session_id": session_id,
                "code": event_data.get("code", "worker_error"),
                "message": event_data.get("message", "worker error"),
                "source": "worker",
            }
        ]

    if event_type == "finished":
        return [
            {
                "type": "status",
                "session_id": session_id,
                "code": "worker_finished",
                "message": "worker session finished",
                "source": "worker",
            }
        ]

    if event_type == "worker_exit":
        return [
            {
                "type": "error",
                "session_id": session_id,
                "code": "worker_exit",
                "exit_code": event_data.get("code"),
                "message": "worker process exited",
                "source": "worker",
            }
        ]

    return [
        {
            "type": "status",
            "session_id": session_id,
            "code": f"worker_{event_type}",
            "message": "worker event passthrough",
            "source": "worker",
        }
    ]
