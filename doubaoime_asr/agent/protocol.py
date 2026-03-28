from __future__ import annotations

import json
from typing import Any


def encode_event(event_type: str, **payload: Any) -> str:
    body = {"type": event_type, **payload}
    return json.dumps(body, ensure_ascii=False)


def decode_event(line: str) -> dict[str, Any]:
    return json.loads(line)
