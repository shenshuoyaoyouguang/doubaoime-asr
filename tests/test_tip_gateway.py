import asyncio
import ctypes
import logging
import os
import threading
import time

import pytest

from doubaoime_asr.agent.input_injector import FocusTarget
from doubaoime_asr.agent.tip_gateway import INVALID_HANDLE_VALUE, NamedPipeTipGateway, NullTipGateway, build_tip_gateway_from_env
from doubaoime_asr.agent.tip_gateway_protocol import (
    TIP_GATEWAY_PROTOCOL_VERSION,
    decode_tip_gateway_message,
    encode_tip_gateway_command,
    encode_tip_gateway_event,
)


PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateNamedPipeW.argtypes = (
    ctypes.c_wchar_p,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_void_p,
)
kernel32.CreateNamedPipeW.restype = ctypes.c_void_p
kernel32.ConnectNamedPipe.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
kernel32.ConnectNamedPipe.restype = ctypes.c_int
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
kernel32.FlushFileBuffers.argtypes = (ctypes.c_void_p,)
kernel32.FlushFileBuffers.restype = ctypes.c_int
kernel32.DisconnectNamedPipe.argtypes = (ctypes.c_void_p,)
kernel32.DisconnectNamedPipe.restype = ctypes.c_int
kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
kernel32.CloseHandle.restype = ctypes.c_int


def _pipe_read_line(handle: int, *, timeout_s: float = 1.0) -> str:
    deadline = time.perf_counter() + timeout_s
    buffer = b""
    while time.perf_counter() < deadline:
        chunk = ctypes.create_string_buffer(512)
        bytes_read = ctypes.c_uint32(0)
        ok = kernel32.ReadFile(handle, chunk, 512, ctypes.byref(bytes_read), None)
        assert ok
        if bytes_read.value:
            buffer += chunk.raw[: bytes_read.value]
            if b"\n" in buffer:
                raw, _rest = buffer.split(b"\n", 1)
                return raw.decode("utf-8")
        time.sleep(0.01)
    raise RuntimeError("timed out waiting for pipe line")


def _pipe_write_line(handle: int, raw_line: str) -> None:
    payload = (raw_line + "\n").encode("utf-8")
    bytes_written = ctypes.c_uint32(0)
    ok = kernel32.WriteFile(
        handle,
        ctypes.c_char_p(payload),
        len(payload),
        ctypes.byref(bytes_written),
        None,
    )
    assert ok
    assert bytes_written.value == len(payload)
    assert kernel32.FlushFileBuffers(handle)


def test_tip_gateway_protocol_round_trip() -> None:
    raw = encode_tip_gateway_command("begin_session", session_id="abc", context_id="hwnd:1")
    decoded = decode_tip_gateway_message(raw)
    assert decoded.version == TIP_GATEWAY_PROTOCOL_VERSION
    assert decoded.name == "begin_session"
    assert decoded.session_id == "abc"
    assert decoded.payload == {"context_id": "hwnd:1"}


@pytest.mark.asyncio
async def test_named_pipe_tip_gateway_begin_session_round_trip() -> None:
    pipe_name = rf"\\.\pipe\doubao-tip-gateway-{int(time.time() * 1000)}"
    observed: list[object] = []

    def server() -> None:
        for expected_name in ("query_active_context", "begin_session"):
            handle = kernel32.CreateNamedPipeW(
                pipe_name,
                PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                1,
                4096,
                4096,
                0,
                None,
            )
            assert handle != INVALID_HANDLE_VALUE
            try:
                assert kernel32.ConnectNamedPipe(handle, None) or ctypes.get_last_error() == 535
                request = decode_tip_gateway_message(_pipe_read_line(int(handle)))
                observed.append(request)
                assert request.name == expected_name
                if request.name == "query_active_context":
                    _pipe_write_line(
                        int(handle),
                        encode_tip_gateway_event(
                            "ack",
                            session_id=request.session_id,
                            ok=True,
                            active_context_id="hwnd:99",
                            edit_session_ready=True,
                        ),
                    )
                else:
                    _pipe_write_line(
                        int(handle),
                        encode_tip_gateway_event("ack", session_id=request.session_id, ok=True),
                    )
            finally:
                kernel32.DisconnectNamedPipe(handle)
                kernel32.CloseHandle(handle)

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    gateway = NamedPipeTipGateway(
        pipe_name=pipe_name,
        timeout_ms=250,
        logger=logging.getLogger("tip-gateway-test"),
    )
    target = FocusTarget(hwnd=42, focus_hwnd=99, process_id=7, process_name="notepad.exe")
    result = await gateway.begin_session(session_id="session-1", target=target)
    thread.join(timeout=1)

    assert result.success is True
    query_request, begin_request = observed
    assert query_request.name == "query_active_context"
    assert begin_request.name == "begin_session"
    assert begin_request.session_id == "session-1"
    assert begin_request.payload["context_id"] == "hwnd:99"
    assert begin_request.payload["target_hwnd"] == 42
    assert begin_request.payload["process_name"] == "notepad.exe"


@pytest.mark.asyncio
async def test_named_pipe_tip_gateway_maps_error_reason() -> None:
    pipe_name = rf"\\.\pipe\doubao-tip-gateway-{int(time.time() * 1000)}-error"

    def server() -> None:
        handle = kernel32.CreateNamedPipeW(
            pipe_name,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1,
            4096,
            4096,
            0,
            None,
        )
        assert handle != INVALID_HANDLE_VALUE
        try:
            assert kernel32.ConnectNamedPipe(handle, None) or ctypes.get_last_error() == 535
            request = decode_tip_gateway_message(_pipe_read_line(int(handle)))
            _pipe_write_line(
                int(handle),
                encode_tip_gateway_event(
                    "error",
                    session_id=request.session_id,
                    reason="tip_context_missing",
                    cleanup_performed=False,
                ),
            )
        finally:
            kernel32.DisconnectNamedPipe(handle)
            kernel32.CloseHandle(handle)

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    gateway = NamedPipeTipGateway(pipe_name=pipe_name, timeout_ms=250)
    result = await gateway.submit_interim(session_id="session-2", text="hello")
    thread.join(timeout=1)

    assert result.success is False
    assert result.reason == "tip_context_missing"
    assert result.cleanup_performed is False


@pytest.mark.asyncio
async def test_named_pipe_tip_gateway_rejects_missing_begin_target_without_pipe() -> None:
    gateway = NamedPipeTipGateway(pipe_name=r"\\.\pipe\unused-tip-gateway", timeout_ms=50)
    result = await gateway.begin_session(session_id="missing-target", target=None)
    assert result.success is False
    assert result.reason == "tip_context_missing"
    assert result.timeout is False


@pytest.mark.asyncio
async def test_named_pipe_tip_gateway_supports_sequential_commands() -> None:
    pipe_name = rf"\\.\pipe\doubao-tip-gateway-{int(time.time() * 1000)}-seq"
    observed_names: list[str] = []

    def server() -> None:
        for _ in range(4):
            handle = kernel32.CreateNamedPipeW(
                pipe_name,
                PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                1,
                4096,
                4096,
                0,
                None,
            )
            assert handle != INVALID_HANDLE_VALUE
            try:
                assert kernel32.ConnectNamedPipe(handle, None) or ctypes.get_last_error() == 535
                request = decode_tip_gateway_message(_pipe_read_line(int(handle)))
                observed_names.append(request.name)
                _pipe_write_line(
                    int(handle),
                    encode_tip_gateway_event(
                        "ack",
                        session_id=request.session_id,
                        ok=True,
                        **(
                            {"active_context_id": "hwnd:22", "edit_session_ready": True}
                            if request.name == "query_active_context"
                            else {}
                        ),
                    ),
                )
            finally:
                kernel32.DisconnectNamedPipe(handle)
                kernel32.CloseHandle(handle)

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    gateway = NamedPipeTipGateway(pipe_name=pipe_name, timeout_ms=250)
    begin = await gateway.begin_session(session_id="seq-1", target=FocusTarget(hwnd=11, focus_hwnd=22))
    interim = await gateway.submit_interim(session_id="seq-1", text="hello")
    final = await gateway.commit_resolved_final(session_id="seq-1", text="world")
    thread.join(timeout=1)

    assert begin.success is True
    assert interim.success is True
    assert final.success is True
    assert observed_names == ["query_active_context", "begin_session", "interim", "commit_resolved_final"]


@pytest.mark.asyncio
async def test_named_pipe_tip_gateway_rejects_non_active_context() -> None:
    pipe_name = rf"\\.\pipe\doubao-tip-gateway-{int(time.time() * 1000)}-inactive"

    def server() -> None:
        handle = kernel32.CreateNamedPipeW(
            pipe_name,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1,
            4096,
            4096,
            0,
            None,
        )
        assert handle != INVALID_HANDLE_VALUE
        try:
            assert kernel32.ConnectNamedPipe(handle, None) or ctypes.get_last_error() == 535
            request = decode_tip_gateway_message(_pipe_read_line(int(handle)))
            assert request.name == "query_active_context"
            _pipe_write_line(
                int(handle),
                encode_tip_gateway_event("ack", session_id=request.session_id, ok=True, active_context_id="hwnd:999"),
            )
        finally:
            kernel32.DisconnectNamedPipe(handle)
            kernel32.CloseHandle(handle)

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    gateway = NamedPipeTipGateway(pipe_name=pipe_name, timeout_ms=250)
    result = await gateway.begin_session(session_id="inactive-1", target=FocusTarget(hwnd=11, focus_hwnd=22))
    thread.join(timeout=1)

    assert result.success is False
    assert result.reason == "tip_context_not_active"


@pytest.mark.asyncio
async def test_named_pipe_tip_gateway_rejects_missing_rendezvous_context() -> None:
    pipe_name = rf"\\.\pipe\doubao-tip-gateway-{int(time.time() * 1000)}-missing-active"

    def server() -> None:
        handle = kernel32.CreateNamedPipeW(
            pipe_name,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1,
            4096,
            4096,
            0,
            None,
        )
        assert handle != INVALID_HANDLE_VALUE
        try:
            assert kernel32.ConnectNamedPipe(handle, None) or ctypes.get_last_error() == 535
            request = decode_tip_gateway_message(_pipe_read_line(int(handle)))
            assert request.name == "query_active_context"
            _pipe_write_line(
                int(handle),
                encode_tip_gateway_event("ack", session_id=request.session_id, ok=True),
            )
        finally:
            kernel32.DisconnectNamedPipe(handle)
            kernel32.CloseHandle(handle)

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    gateway = NamedPipeTipGateway(pipe_name=pipe_name, timeout_ms=250)
    result = await gateway.begin_session(session_id="inactive-2", target=FocusTarget(hwnd=11, focus_hwnd=22))
    thread.join(timeout=1)

    assert result.success is False
    assert result.reason == "tip_context_not_active"


@pytest.mark.asyncio
async def test_named_pipe_tip_gateway_rejects_edit_session_unavailable() -> None:
    pipe_name = rf"\\.\pipe\doubao-tip-gateway-{int(time.time() * 1000)}-edit-unavailable"

    def server() -> None:
        handle = kernel32.CreateNamedPipeW(
            pipe_name,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1,
            4096,
            4096,
            0,
            None,
        )
        assert handle != INVALID_HANDLE_VALUE
        try:
            assert kernel32.ConnectNamedPipe(handle, None) or ctypes.get_last_error() == 535
            request = decode_tip_gateway_message(_pipe_read_line(int(handle)))
            assert request.name == "query_active_context"
            _pipe_write_line(
                int(handle),
                encode_tip_gateway_event(
                    "ack",
                    session_id=request.session_id,
                    ok=True,
                    active_context_id="hwnd:22",
                    edit_session_ready=False,
                ),
            )
        finally:
            kernel32.DisconnectNamedPipe(handle)
            kernel32.CloseHandle(handle)

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    gateway = NamedPipeTipGateway(pipe_name=pipe_name, timeout_ms=250)
    result = await gateway.begin_session(session_id="inactive-3", target=FocusTarget(hwnd=11, focus_hwnd=22))
    thread.join(timeout=1)

    assert result.success is False
    assert result.reason == "tip_edit_session_unavailable"


@pytest.mark.asyncio
async def test_named_pipe_tip_gateway_rejects_query_unsupported() -> None:
    pipe_name = rf"\\.\pipe\doubao-tip-gateway-{int(time.time() * 1000)}-unsupported"

    def server() -> None:
        handle = kernel32.CreateNamedPipeW(
            pipe_name,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1,
            4096,
            4096,
            0,
            None,
        )
        assert handle != INVALID_HANDLE_VALUE
        try:
            assert kernel32.ConnectNamedPipe(handle, None) or ctypes.get_last_error() == 535
            request = decode_tip_gateway_message(_pipe_read_line(int(handle)))
            assert request.name == "query_active_context"
            _pipe_write_line(
                int(handle),
                encode_tip_gateway_event("error", session_id=request.session_id, reason="tip_command_unsupported"),
            )
        finally:
            kernel32.DisconnectNamedPipe(handle)
            kernel32.CloseHandle(handle)

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    gateway = NamedPipeTipGateway(pipe_name=pipe_name, timeout_ms=250)
    result = await gateway.begin_session(session_id="inactive-4", target=FocusTarget(hwnd=11, focus_hwnd=22))
    thread.join(timeout=1)

    assert result.success is False
    assert result.reason == "tip_command_unsupported"


def test_build_tip_gateway_from_env_respects_pipe_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOUBAO_TIP_GATEWAY_PIPE_NAME", raising=False)
    assert isinstance(build_tip_gateway_from_env(), NullTipGateway)

    monkeypatch.setenv("DOUBAO_TIP_GATEWAY_PIPE_NAME", r"\\.\pipe\doubao-tip-control")
    gateway = build_tip_gateway_from_env()
    assert isinstance(gateway, NamedPipeTipGateway)
    assert gateway.pipe_name == r"\\.\pipe\doubao-tip-control"

    monkeypatch.delenv("DOUBAO_TIP_GATEWAY_PIPE_NAME", raising=False)
