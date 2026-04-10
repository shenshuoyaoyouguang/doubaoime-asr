from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolDecodeError, decode_json_message, encode_json_message


SERVICE_PROTOCOL_VERSION = 1
SERVICE_COMMANDS = frozenset({"start", "stop", "cancel", "ping", "exit"})
SERVICE_EVENTS = frozenset(
    {
        "service_ready",
        "status",
        "pong",
        "error",
        "service_exiting",
        "interim",
        "final_raw",
        "final_resolved",
        "fallback_required",
    }
)


@dataclass(frozen=True, slots=True)
class ServiceMessage:
    version: int
    kind: str
    name: str
    session_id: str | None = None
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        body = {
            "version": self.version,
            "kind": self.kind,
            "name": self.name,
            "payload": dict(self.payload or {}),
        }
        if self.session_id is not None:
            body["session_id"] = self.session_id
        return body


class ServiceProtocolError(ValueError):
    """Raised when a service protocol frame is invalid."""



def _validate_name(kind: str, name: str) -> None:
    allowed = SERVICE_COMMANDS if kind == "command" else SERVICE_EVENTS if kind == "event" else None
    if allowed is None:
        raise ServiceProtocolError(f"unsupported service message kind: {kind!r}")
    if name not in allowed:
        raise ServiceProtocolError(f"unsupported {kind} name: {name!r}")



def encode_service_message(
    *,
    kind: str,
    name: str,
    session_id: str | None = None,
    version: int = SERVICE_PROTOCOL_VERSION,
    **payload: Any,
) -> str:
    _validate_name(kind, name)
    return encode_json_message(
        ServiceMessage(
            version=version,
            kind=kind,
            name=name,
            session_id=session_id,
            payload=payload,
        ).to_dict()
    )



def encode_service_command(
    command: str,
    *,
    session_id: str | None = None,
    version: int = SERVICE_PROTOCOL_VERSION,
    **payload: Any,
) -> str:
    return encode_service_message(
        kind="command",
        name=command,
        session_id=session_id,
        version=version,
        **payload,
    )



def encode_service_event(
    event_type: str,
    *,
    session_id: str | None = None,
    version: int = SERVICE_PROTOCOL_VERSION,
    **payload: Any,
) -> str:
    return encode_service_message(
        kind="event",
        name=event_type,
        session_id=session_id,
        version=version,
        **payload,
    )



def decode_service_message(line: str) -> ServiceMessage:
    try:
        body = decode_json_message(line)
    except ProtocolDecodeError as exc:
        raise ServiceProtocolError(str(exc)) from exc

    version = body.get("version")
    if not isinstance(version, int) or version <= 0:
        raise ServiceProtocolError("service message version must be a positive integer")

    kind = body.get("kind")
    name = body.get("name")
    if not isinstance(kind, str) or not kind:
        raise ServiceProtocolError("service message kind must be a non-empty string")
    if not isinstance(name, str) or not name:
        raise ServiceProtocolError("service message name must be a non-empty string")
    _validate_name(kind, name)

    session_id = body.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        raise ServiceProtocolError("service message session_id must be a string when present")

    payload = body.get("payload", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ServiceProtocolError("service message payload must be an object")

    return ServiceMessage(
        version=version,
        kind=kind,
        name=name,
        session_id=session_id,
        payload=payload,
    )
