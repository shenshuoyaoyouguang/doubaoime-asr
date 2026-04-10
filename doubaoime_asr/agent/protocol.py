from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


class ProtocolDecodeError(ValueError):
    """Raised when a JSON line protocol message cannot be decoded."""


def encode_json_message(message: Mapping[str, Any]) -> str:
    return json.dumps(dict(message), ensure_ascii=False)


def decode_json_message(line: str) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:  # pragma: no cover - exercised via callers
        raise ProtocolDecodeError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise ProtocolDecodeError("protocol message must decode to a JSON object")
    return payload


def encode_event(event_type: str, **payload: Any) -> str:
    body = {"type": event_type, **payload}
    return encode_json_message(body)


def decode_event(line: str) -> dict[str, Any]:
    return decode_json_message(line)
