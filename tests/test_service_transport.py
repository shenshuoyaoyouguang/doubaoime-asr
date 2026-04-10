import asyncio
import ctypes
import logging
import time

import pytest

from doubaoime_asr.agent.service_protocol import decode_service_message, encode_service_command
from doubaoime_asr.agent.service_transport import (
    INVALID_HANDLE_VALUE,
    NamedPipeServiceTransport,
    StdioServiceTransport,
    build_service_transport,
)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3

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


def _open_pipe_client(pipe_name: str, *, timeout_s: float = 1.0) -> int:
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
        time.sleep(0.02)
    raise RuntimeError(f"failed to connect to pipe {pipe_name}")


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


def test_stdio_service_transport_emit_event_prints_json(capsys) -> None:
    loop = asyncio.new_event_loop()
    try:
        transport = StdioServiceTransport(logger=logging.getLogger("service-transport-test"), loop=loop)
        transport.emit_event("status", code="ok", message="hello")
        out = capsys.readouterr().out
        assert '"name": "status"' in out
        assert '"code": "ok"' in out
    finally:
        loop.close()


def test_named_pipe_service_transport_exposes_pipe_name() -> None:
    loop = asyncio.new_event_loop()
    try:
        transport = NamedPipeServiceTransport(
            logger=logging.getLogger("named-pipe-transport-test"),
            loop=loop,
            pipe_name=r"\\.\pipe\doubao-tip-service",
        )
        assert transport.pipe_name == r"\\.\pipe\doubao-tip-service"
        queue: asyncio.Queue[str] = asyncio.Queue()
        transport.start_reader(queue)
        transport.close()
    finally:
        loop.close()


def test_build_service_transport_selects_named_pipe_placeholder() -> None:
    loop = asyncio.new_event_loop()
    try:
        transport = build_service_transport(
            logger=logging.getLogger("service-transport-test"),
            loop=loop,
            transport_kind="named_pipe",
            pipe_name=r"\\.\pipe\doubao-tip-service",
        )
        assert isinstance(transport, NamedPipeServiceTransport)
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_named_pipe_service_transport_buffers_and_reads_round_trip() -> None:
    pipe_name = rf"\\.\pipe\doubao-tip-service-{int(time.time() * 1000)}"
    transport = NamedPipeServiceTransport(
        logger=logging.getLogger("named-pipe-transport-test"),
        loop=asyncio.get_running_loop(),
        pipe_name=pipe_name,
    )
    queue: asyncio.Queue[str] = asyncio.Queue()
    transport.start_reader(queue)
    transport.emit_event("status", code="ready", message="buffered-before-connect")
    client = _open_pipe_client(pipe_name)
    try:
        first_line = _pipe_read_line(client)
        assert decode_service_message(first_line).name == "status"
        _pipe_write_line(client, encode_service_command("ping", session_id="s-1"))
        inbound = await asyncio.wait_for(queue.get(), timeout=1)
        decoded = decode_service_message(inbound)
        assert decoded.name == "ping"
        assert decoded.session_id == "s-1"
    finally:
        kernel32.CloseHandle(client)
        transport.close()


def test_build_service_transport_rejects_unknown_kind() -> None:
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(ValueError, match="unsupported service transport kind"):
            build_service_transport(
                logger=logging.getLogger("service-transport-test"),
                loop=loop,
                transport_kind="unknown",
                pipe_name="ignored",
            )
    finally:
        loop.close()
