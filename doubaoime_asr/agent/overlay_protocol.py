from __future__ import annotations

import json
from typing import Any


def encode_overlay_command(command: str, **payload: Any) -> str:
    body = {"cmd": command, **payload}
    return json.dumps(body, ensure_ascii=False)


def decode_overlay_event(line: str) -> dict[str, Any]:
    payload = json.loads(line)
    if not isinstance(payload, dict):
        raise ValueError("overlay event must be a JSON object")
    return payload
