from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .protocol import ProtocolDecodeError, decode_json_message, encode_json_message


TIP_GATEWAY_PROTOCOL_VERSION = 1
TIP_GATEWAY_COMMANDS = frozenset(
    {
        "register_active_context",
        "clear_active_context",
        "query_active_context",
        "begin_session",
        "interim",
        "commit_resolved_final",
        "cancel_session",
    }
)
TIP_GATEWAY_EVENTS = frozenset({"ack", "error"})


@dataclass(frozen=True, slots=True)
class TipGatewayMessage:
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


class TipGatewayProtocolError(ValueError):
    """Raised when a native TIP gateway frame is invalid."""


def _validate_name(kind: str, name: str) -> None:
    allowed = TIP_GATEWAY_COMMANDS if kind == "command" else TIP_GATEWAY_EVENTS if kind == "event" else None
    if allowed is None:
        raise TipGatewayProtocolError(f"unsupported tip gateway message kind: {kind!r}")
    if name not in allowed:
        raise TipGatewayProtocolError(f"unsupported {kind} name: {name!r}")


def encode_tip_gateway_message(
    *,
    kind: str,
    name: str,
    session_id: str | None = None,
    version: int = TIP_GATEWAY_PROTOCOL_VERSION,
    **payload: Any,
) -> str:
    _validate_name(kind, name)
    return encode_json_message(
        TipGatewayMessage(
            version=version,
            kind=kind,
            name=name,
            session_id=session_id,
            payload=payload,
        ).to_dict()
    )


def encode_tip_gateway_command(
    command: str,
    *,
    session_id: str | None = None,
    version: int = TIP_GATEWAY_PROTOCOL_VERSION,
    **payload: Any,
) -> str:
    return encode_tip_gateway_message(
        kind="command",
        name=command,
        session_id=session_id,
        version=version,
        **payload,
    )


def encode_tip_gateway_event(
    event_type: str,
    *,
    session_id: str | None = None,
    version: int = TIP_GATEWAY_PROTOCOL_VERSION,
    **payload: Any,
) -> str:
    return encode_tip_gateway_message(
        kind="event",
        name=event_type,
        session_id=session_id,
        version=version,
        **payload,
    )


def decode_tip_gateway_message(line: str) -> TipGatewayMessage:
    try:
        body = decode_json_message(line)
    except ProtocolDecodeError as exc:
        raise TipGatewayProtocolError(str(exc)) from exc

    version = body.get("version")
    if not isinstance(version, int) or version <= 0:
        raise TipGatewayProtocolError("tip gateway message version must be a positive integer")

    kind = body.get("kind")
    name = body.get("name")
    if not isinstance(kind, str) or not kind:
        raise TipGatewayProtocolError("tip gateway message kind must be a non-empty string")
    if not isinstance(name, str) or not name:
        raise TipGatewayProtocolError("tip gateway message name must be a non-empty string")
    _validate_name(kind, name)

    session_id = body.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        raise TipGatewayProtocolError("tip gateway message session_id must be a string when present")

    payload = body.get("payload", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise TipGatewayProtocolError("tip gateway message payload must be an object")

    return TipGatewayMessage(
        version=version,
        kind=kind,
        name=name,
        session_id=session_id,
        payload=payload,
    )
