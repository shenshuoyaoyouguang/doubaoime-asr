from __future__ import annotations

import asyncio
import contextlib
import ctypes
from dataclasses import dataclass
import logging
import os
import time
from typing import Protocol

from .input_injector import FocusTarget
from .tip_gateway_protocol import (
    TipGatewayProtocolError,
    decode_tip_gateway_message,
    encode_tip_gateway_command,
)


GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = (
    ctypes.c_wchar_p,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_void_p,
)
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.PeekNamedPipe.argtypes = (
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_void_p,
)
kernel32.PeekNamedPipe.restype = ctypes.c_int
kernel32.ReadFile.argtypes = (
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_void_p,
)
kernel32.ReadFile.restype = ctypes.c_int
kernel32.WriteFile.argtypes = (
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_void_p,
)
kernel32.WriteFile.restype = ctypes.c_int
kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
kernel32.CloseHandle.restype = ctypes.c_int


@dataclass(slots=True, frozen=True)
class TipGatewayResult:
    success: bool
    reason: str | None = None
    timeout: bool = False
    cleanup_performed: bool | None = None


@dataclass(slots=True, frozen=True)
class TipGatewayContextState:
    success: bool
    active_context_id: str | None = None
    edit_session_ready: bool | None = None
    reason: str | None = None
    timeout: bool = False


class TipGateway(Protocol):
    def is_available(self) -> bool: ...
    async def begin_session(self, *, session_id: str, target: FocusTarget | None) -> TipGatewayResult: ...
    async def submit_interim(self, *, session_id: str, text: str) -> TipGatewayResult: ...
    async def commit_resolved_final(self, *, session_id: str, text: str) -> TipGatewayResult: ...
    async def cancel_session(self, *, session_id: str, reason: str) -> TipGatewayResult: ...


class NullTipGateway:
    """Default gateway that keeps coordinator behavior on legacy path."""

    def is_available(self) -> bool:
        return False

    async def begin_session(self, *, session_id: str, target: FocusTarget | None) -> TipGatewayResult:
        del session_id, target
        return TipGatewayResult(success=False, reason="tip_unavailable")

    async def submit_interim(self, *, session_id: str, text: str) -> TipGatewayResult:
        del session_id, text
        return TipGatewayResult(success=False, reason="tip_unavailable")

    async def commit_resolved_final(self, *, session_id: str, text: str) -> TipGatewayResult:
        del session_id, text
        return TipGatewayResult(success=False, reason="tip_unavailable")

    async def cancel_session(self, *, session_id: str, reason: str) -> TipGatewayResult:
        del session_id, reason
        return TipGatewayResult(success=False, reason="tip_unavailable", cleanup_performed=False)


class NamedPipeTipGateway:
    """TIP gateway backed by a native control named pipe."""

    def __init__(
        self,
        *,
        pipe_name: str,
        timeout_ms: int = 250,
        logger: logging.Logger | None = None,
    ) -> None:
        self._pipe_name = pipe_name
        self._timeout_s = max(timeout_ms, 1) / 1000.0
        self._logger = logger or logging.getLogger(__name__)

    @property
    def pipe_name(self) -> str:
        return self._pipe_name

    def is_available(self) -> bool:
        return bool(self._pipe_name)

    async def begin_session(self, *, session_id: str, target: FocusTarget | None) -> TipGatewayResult:
        if target is None:
            return TipGatewayResult(success=False, reason="tip_context_missing")
        payload = _focus_target_payload(target)
        context_state = await self._query_active_context()
        if context_state.timeout:
            return TipGatewayResult(success=False, reason="tip_timeout", timeout=True)
        if context_state.success:
            if not context_state.active_context_id:
                return TipGatewayResult(success=False, reason="tip_context_not_active")
            if context_state.active_context_id != payload["context_id"]:
                return TipGatewayResult(success=False, reason="tip_context_not_active")
            if context_state.edit_session_ready is not True:
                return TipGatewayResult(success=False, reason="tip_edit_session_unavailable")
        if not context_state.success:
            return TipGatewayResult(success=False, reason=context_state.reason or "tip_query_failed")
        return await self._dispatch(
            "begin_session",
            session_id=session_id,
            payload=payload,
            default_reason="tip_connect_failed",
        )

    async def submit_interim(self, *, session_id: str, text: str) -> TipGatewayResult:
        return await self._dispatch(
            "interim",
            session_id=session_id,
            payload={"text": text},
            default_reason="tip_interim_failed",
        )

    async def commit_resolved_final(self, *, session_id: str, text: str) -> TipGatewayResult:
        return await self._dispatch(
            "commit_resolved_final",
            session_id=session_id,
            payload={"text": text},
            default_reason="tip_failure",
        )

    async def cancel_session(self, *, session_id: str, reason: str) -> TipGatewayResult:
        return await self._dispatch(
            "cancel_session",
            session_id=session_id,
            payload={"reason": reason},
            default_reason="composition_cleanup_failed",
        )

    async def _dispatch(
        self,
        command: str,
        *,
        session_id: str,
        payload: dict[str, object],
        default_reason: str,
    ) -> TipGatewayResult:
        if not self.is_available():
            return TipGatewayResult(success=False, reason="tip_unavailable")
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._round_trip,
                    command,
                    session_id,
                    payload,
                    default_reason,
                ),
                timeout=self._timeout_s + 0.05,
            )
        except asyncio.TimeoutError:
            self._logger.warning("tip_gateway_timeout command=%s session_id=%s", command, session_id)
            return TipGatewayResult(success=False, reason="tip_timeout", timeout=True)

    async def _query_active_context(self) -> TipGatewayContextState:
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self._round_trip_message,
                    "query_active_context",
                    "rendezvous",
                    {},
                ),
                timeout=self._timeout_s + 0.05,
            )
        except asyncio.TimeoutError:
            self._logger.warning("tip_gateway_timeout command=query_active_context session_id=rendezvous")
            return TipGatewayContextState(success=False, reason="tip_timeout", timeout=True)

        if response is None:
            return TipGatewayContextState(success=False, reason="tip_connect_failed")

        payload = response.payload or {}
        active_context_id = payload.get("active_context_id")
        if active_context_id is not None and not isinstance(active_context_id, str):
            active_context_id = None
        edit_session_ready = payload.get("edit_session_ready")
        if edit_session_ready is not None and not isinstance(edit_session_ready, bool):
            edit_session_ready = None
        if response.name == "ack":
            return TipGatewayContextState(
                success=bool(payload.get("ok", True)),
                active_context_id=active_context_id,
                edit_session_ready=edit_session_ready,
                reason=_coerce_reason(payload.get("reason")),
            )
        return TipGatewayContextState(
            success=False,
            active_context_id=active_context_id,
            edit_session_ready=edit_session_ready,
            reason=_coerce_reason(payload.get("reason")) or "tip_query_failed",
        )

    def _round_trip(
        self,
        command: str,
        session_id: str,
        payload: dict[str, object],
        default_reason: str,
    ) -> TipGatewayResult:
        response = self._round_trip_message(command, session_id, payload)
        if response is None:
            self._logger.warning("tip_gateway_connect_failed pipe=%s command=%s", self._pipe_name, command)
            return TipGatewayResult(success=False, reason=default_reason)
        try:
            payload_body = response.payload or {}
            cleanup_performed = payload_body.get("cleanup_performed")
            if cleanup_performed is not None and not isinstance(cleanup_performed, bool):
                cleanup_performed = None

            if response.session_id != session_id:
                self._logger.warning(
                    "tip_gateway_session_mismatch command=%s expected=%s actual=%s",
                    command,
                    session_id,
                    response.session_id,
                )
                return TipGatewayResult(success=False, reason="tip_session_mismatch")

            if response.name == "ack":
                return TipGatewayResult(
                    success=bool(payload_body.get("ok", True)),
                    reason=_coerce_reason(payload_body.get("reason")),
                    cleanup_performed=cleanup_performed,
                )

            return TipGatewayResult(
                success=False,
                reason=_coerce_reason(payload_body.get("reason")) or default_reason,
                cleanup_performed=cleanup_performed,
            )
        except TipGatewayProtocolError as exc:
            self._logger.warning("tip_gateway_protocol_error command=%s detail=%s", command, exc)
            return TipGatewayResult(success=False, reason="tip_protocol_error")

    def _round_trip_message(
        self,
        command: str,
        session_id: str,
        payload: dict[str, object],
    ):
        handle = _open_pipe_client(self._pipe_name, timeout_s=self._timeout_s)
        if handle == INVALID_HANDLE_VALUE:
            return None
        try:
            raw_command = encode_tip_gateway_command(command, session_id=session_id, **payload)
            if not _write_line(handle, raw_command):
                return None
            raw_response = _read_line(handle, timeout_s=self._timeout_s)
            if raw_response is None:
                return None
            return decode_tip_gateway_message(raw_response)
        except TipGatewayProtocolError as exc:
            self._logger.warning("tip_gateway_protocol_error command=%s detail=%s", command, exc)
        finally:
            kernel32.CloseHandle(handle)


def build_tip_gateway_from_env(*, logger: logging.Logger | None = None) -> TipGateway:
    pipe_name = os.environ.get("DOUBAO_TIP_GATEWAY_PIPE_NAME", "").strip()
    if not pipe_name:
        return NullTipGateway()
    timeout_raw = os.environ.get("DOUBAO_TIP_GATEWAY_TIMEOUT_MS", "").strip()
    timeout_ms = 250
    if timeout_raw:
        with contextlib.suppress(ValueError):
            timeout_ms = int(timeout_raw)
    return NamedPipeTipGateway(pipe_name=pipe_name, timeout_ms=timeout_ms, logger=logger)


def _coerce_reason(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _focus_target_payload(target: FocusTarget | None) -> dict[str, object]:
    if target is None:
        raise ValueError("target is required for tip gateway begin_session")
    context_hwnd = target.focus_hwnd or target.hwnd
    payload: dict[str, object] = {
        "context_id": f"hwnd:{context_hwnd}",
        "target_hwnd": target.hwnd,
        "text_input_profile": target.text_input_profile,
    }
    if target.focus_hwnd is not None:
        payload["focus_hwnd"] = target.focus_hwnd
    if target.process_id is not None:
        payload["process_id"] = target.process_id
    if target.process_name is not None:
        payload["process_name"] = target.process_name
    return payload


def _open_pipe_client(pipe_name: str, *, timeout_s: float) -> int:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        handle = kernel32.CreateFileW(
            pipe_name,
            GENERIC_READ | GENERIC_WRITE,
            0,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle != INVALID_HANDLE_VALUE:
            return int(handle)
        time.sleep(0.01)
    return INVALID_HANDLE_VALUE


def _write_line(handle: int, raw_line: str) -> bool:
    payload = (raw_line + "\n").encode("utf-8")
    bytes_written = ctypes.c_uint32(0)
    ok = kernel32.WriteFile(
        handle,
        ctypes.c_char_p(payload),
        len(payload),
        ctypes.byref(bytes_written),
        None,
    )
    return bool(ok) and bytes_written.value == len(payload)


def _read_line(handle: int, *, timeout_s: float) -> str | None:
    deadline = time.perf_counter() + timeout_s
    buffer = b""
    while time.perf_counter() < deadline:
        total_available = ctypes.c_uint32(0)
        ok = kernel32.PeekNamedPipe(
            handle,
            None,
            0,
            None,
            ctypes.byref(total_available),
            None,
        )
        if not ok:
            return None
        if total_available.value == 0:
            time.sleep(0.01)
            continue
        chunk = ctypes.create_string_buffer(max(total_available.value, 256))
        bytes_read = ctypes.c_uint32(0)
        ok = kernel32.ReadFile(handle, chunk, len(chunk), ctypes.byref(bytes_read), None)
        if not ok:
            return None
        if bytes_read.value:
            buffer += chunk.raw[: bytes_read.value]
            if b"\n" in buffer:
                raw, _rest = buffer.split(b"\n", 1)
                return raw.decode("utf-8")
    return None
