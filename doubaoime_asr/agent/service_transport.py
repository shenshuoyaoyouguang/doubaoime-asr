from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Any, Protocol

from .service_protocol import ServiceProtocolError, decode_service_message, encode_service_command, encode_service_event


PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
PIPE_UNLIMITED_INSTANCES = 255
ERROR_PIPE_CONNECTED = 535
ERROR_BROKEN_PIPE = 109
ERROR_NO_DATA = 232
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
PIPE_BUFFER_SIZE = 4096


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateNamedPipeW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
)
kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
kernel32.ConnectNamedPipe.argtypes = (wintypes.HANDLE, wintypes.LPVOID)
kernel32.ConnectNamedPipe.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = (
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
)
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = (
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
)
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
kernel32.FlushFileBuffers.restype = wintypes.BOOL
kernel32.DisconnectNamedPipe.argtypes = (wintypes.HANDLE,)
kernel32.DisconnectNamedPipe.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL


class ServiceTransport(Protocol):
    def start_reader(self, line_queue: asyncio.Queue[str]) -> None: ...
    def emit_event(self, event_type: str, **payload: Any) -> None: ...
    def close(self) -> None: ...


class StdioServiceTransport:
    """Minimal stdio transport for the service skeleton.

    Keeps transport concerns separate from the service runtime state machine so
    later Phase 1 work can swap in Named Pipe transport without rewriting the
    command loop again.
    """

    def __init__(self, *, logger: logging.Logger, loop: asyncio.AbstractEventLoop) -> None:
        self._logger = logger
        self._loop = loop
        self._reader_thread: threading.Thread | None = None

    def start_reader(self, line_queue: asyncio.Queue[str]) -> None:
        if self._reader_thread is not None:
            return

        def reader() -> None:
            saw_exit = False
            try:
                for line in sys.stdin:
                    raw = line.strip()
                    if not raw:
                        continue
                    self._logger.info("service_stdin_line=%s", raw)
                    self._loop.call_soon_threadsafe(line_queue.put_nowait, raw)
                    try:
                        message = decode_service_message(raw)
                    except ServiceProtocolError:
                        continue
                    if message.kind == "command" and message.name == "exit":
                        saw_exit = True
                        break
            except Exception:
                self._logger.exception("service_stdin_reader_failed")
                self._loop.call_soon_threadsafe(
                    line_queue.put_nowait,
                    encode_service_command("exit", reason="stdin_reader_failed"),
                )
                return
            if not saw_exit:
                self._logger.info("service_stdin_eof")
                self._loop.call_soon_threadsafe(
                    line_queue.put_nowait,
                    encode_service_command("exit", reason="stdin_eof"),
                )

        self._reader_thread = threading.Thread(target=reader, name="doubao-service-stdin", daemon=True)
        self._reader_thread.start()

    def emit_event(self, event_type: str, **payload: Any) -> None:
        print(encode_service_event(event_type, **payload), flush=True)

    def close(self) -> None:
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1)
        self._reader_thread = None


class NamedPipeServiceTransport:
    """Windows named-pipe transport for the long-lived service process."""

    def __init__(self, *, logger: logging.Logger, loop: asyncio.AbstractEventLoop, pipe_name: str) -> None:
        self._logger = logger
        self._loop = loop
        self._pipe_name = pipe_name
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()
        self._pipe_handle: int | None = None
        self._pending_writes: list[bytes] = []
        self._opened = False

    @property
    def pipe_name(self) -> str:
        return self._pipe_name

    def start_reader(self, line_queue: asyncio.Queue[str]) -> None:
        if self._reader_thread is not None:
            return

        self._logger.info("named_pipe_transport_start_requested pipe=%s", self._pipe_name)

        def reader() -> None:
            handle = kernel32.CreateNamedPipeW(
                self._pipe_name,
                PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                1,
                PIPE_BUFFER_SIZE,
                PIPE_BUFFER_SIZE,
                0,
                None,
            )
            if handle == INVALID_HANDLE_VALUE:
                self._logger.error("named_pipe_create_failed pipe=%s error=%s", self._pipe_name, ctypes.get_last_error())
                return

            connected = kernel32.ConnectNamedPipe(handle, None)
            if not connected:
                error_code = ctypes.get_last_error()
                if error_code != ERROR_PIPE_CONNECTED:
                    self._logger.error(
                        "named_pipe_connect_failed pipe=%s error=%s",
                        self._pipe_name,
                        error_code,
                    )
                    kernel32.CloseHandle(handle)
                    return

            with self._write_lock:
                self._pipe_handle = handle
                self._opened = True
                pending = list(self._pending_writes)
                self._pending_writes.clear()

            for payload in pending:
                self._write_bytes(payload)

            buffer = b""
            while not self._stop_event.is_set():
                chunk = self._read_chunk(handle)
                if chunk is None:
                    break
                if not chunk:
                    continue
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    self._loop.call_soon_threadsafe(line_queue.put_nowait, line)

            with self._write_lock:
                self._opened = False
                self._pipe_handle = None
            kernel32.DisconnectNamedPipe(handle)
            kernel32.CloseHandle(handle)

        self._reader_thread = threading.Thread(target=reader, name="doubao-service-pipe", daemon=True)
        self._reader_thread.start()

    def emit_event(self, event_type: str, **payload: Any) -> None:
        encoded = (encode_service_event(event_type, **payload) + "\n").encode("utf-8")
        with self._write_lock:
            if not self._opened or self._pipe_handle is None:
                self._pending_writes.append(encoded)
                self._logger.info("named_pipe_transport_emit_buffered pipe=%s event=%s", self._pipe_name, event_type)
                return
        self._write_bytes(encoded)

    def close(self) -> None:
        self._stop_event.set()
        with self._write_lock:
            handle = self._pipe_handle
            self._pipe_handle = None
            self._opened = False
        if handle not in (None, INVALID_HANDLE_VALUE):
            kernel32.DisconnectNamedPipe(handle)
            kernel32.CloseHandle(handle)
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1)
        self._reader_thread = None

    def _read_chunk(self, handle: int) -> bytes | None:
        buffer = ctypes.create_string_buffer(PIPE_BUFFER_SIZE)
        bytes_read = wintypes.DWORD(0)
        ok = kernel32.ReadFile(handle, buffer, PIPE_BUFFER_SIZE, ctypes.byref(bytes_read), None)
        if ok:
            return buffer.raw[: bytes_read.value]

        error_code = ctypes.get_last_error()
        if error_code in {ERROR_BROKEN_PIPE, ERROR_NO_DATA} or self._stop_event.is_set():
            return None
        self._logger.error("named_pipe_read_failed pipe=%s error=%s", self._pipe_name, error_code)
        return None

    def _write_bytes(self, payload: bytes) -> None:
        with self._write_lock:
            handle = self._pipe_handle
        if handle in (None, INVALID_HANDLE_VALUE):
            return
        bytes_written = wintypes.DWORD(0)
        ok = kernel32.WriteFile(
            handle,
            ctypes.c_char_p(payload),
            len(payload),
            ctypes.byref(bytes_written),
            None,
        )
        if not ok:
            self._logger.error("named_pipe_write_failed pipe=%s error=%s", self._pipe_name, ctypes.get_last_error())
            return
        kernel32.FlushFileBuffers(handle)


def build_service_transport(
    *,
    logger: logging.Logger,
    loop: asyncio.AbstractEventLoop,
    transport_kind: str,
    pipe_name: str,
) -> ServiceTransport:
    if transport_kind == "stdio":
        return StdioServiceTransport(logger=logger, loop=loop)
    if transport_kind in {"named_pipe", "named_pipe_placeholder"}:
        return NamedPipeServiceTransport(logger=logger, loop=loop, pipe_name=pipe_name)
    raise ValueError(f"unsupported service transport kind: {transport_kind}")
