from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import TextIO

from .config import AgentConfig
from .overlay_protocol import decode_overlay_event, encode_overlay_command


def overlay_executable_candidates(
    *,
    env: dict[str, str] | None = None,
    executable: str | None = None,
    frozen: bool | None = None,
    meipass: str | None = None,
    module_file: str | Path | None = None,
) -> list[Path]:
    env = env or os.environ
    candidates: list[Path] = []

    override = env.get("DOUBAO_OVERLAY_EXE")
    if override:
        candidates.append(Path(override))

    executable_path = Path(executable or sys.executable).resolve()
    frozen_mode = bool(getattr(sys, "frozen", False) if frozen is None else frozen)

    if frozen_mode:
        if meipass:
            candidates.append(Path(meipass) / "overlay_ui.exe")
        candidates.append(executable_path.parent / "overlay_ui.exe")
        candidates.append(executable_path.parent / "_internal" / "overlay_ui.exe")

    source_root = Path(module_file or __file__).resolve().parents[2]
    candidates.extend(
        [
            source_root / "build" / "overlay_ui" / "Release" / "overlay_ui.exe",
            source_root / "build" / "overlay_ui" / "RelWithDebInfo" / "overlay_ui.exe",
            source_root / "build" / "overlay_ui" / "MinSizeRel" / "overlay_ui.exe",
            source_root / "build" / "overlay_ui" / "Debug" / "overlay_ui.exe",
            source_root / "build" / "overlay_ui" / "overlay_ui.exe",
            source_root / "overlay_ui" / "build" / "Release" / "overlay_ui.exe",
            source_root / "overlay_ui" / "build" / "overlay_ui.exe",
        ]
    )
    return candidates


def find_overlay_executable(
    *,
    env: dict[str, str] | None = None,
    executable: str | None = None,
    frozen: bool | None = None,
    meipass: str | None = None,
    module_file: str | Path | None = None,
) -> Path | None:
    for candidate in overlay_executable_candidates(
        env=env,
        executable=executable,
        frozen=frozen,
        meipass=meipass,
        module_file=module_file,
    ):
        if candidate.is_file():
            return candidate
    return None


class OverlayPreviewCpp:
    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        executable: Path | None = None,
        startup_timeout_s: float = 2.5,
    ) -> None:
        self._logger = logger or logging.getLogger("doubaoime_asr.agent.overlay")
        self._executable = executable
        self._startup_timeout_s = startup_timeout_s

        self._process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._startup_complete = threading.Event()
        self._ready = threading.Event()
        self._startup_error: str | None = None

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return

        overlay_executable = self._executable or find_overlay_executable(
            meipass=getattr(sys, "_MEIPASS", None),
        )
        if overlay_executable is None:
            raise FileNotFoundError("overlay_ui.exe not found")

        self._startup_complete.clear()
        self._ready.clear()
        self._startup_error = None

        command = [
            str(overlay_executable),
            "--log-path",
            str(AgentConfig.default_overlay_log_path()),
        ]
        self._logger.info("overlay_cpp_spawn cmd=%s", command)

        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(self._process.stdout,),
            name="overlay-cpp-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(self._process.stderr,),
            name="overlay-cpp-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

        if not self._startup_complete.wait(timeout=self._startup_timeout_s):
            self.stop()
            raise RuntimeError("overlay_ui startup timed out")
        if not self._ready.is_set():
            self.stop()
            raise RuntimeError(self._startup_error or "overlay_ui failed before ready")

    def show(
        self,
        text: str,
        *,
        seq: int = 0,
        kind: str = "interim",
        stable_prefix_utf16_len: int = 0,
    ) -> None:
        self._send_command(
            "show",
            text=text,
            seq=str(seq),
            kind=kind,
            stable_prefix_utf16_len=str(max(0, int(stable_prefix_utf16_len))),
        )

    def hide(self, reason: str = "") -> None:
        self._send_command("hide", reason=reason)

    def configure(self, config: AgentConfig) -> None:
        self._send_command("configure", **config.overlay_style_payload())

    def stop(self) -> None:
        process = self._process
        if process is None:
            return

        try:
            self._send_command("stop")
        except Exception:
            self._logger.exception("overlay_cpp_stop_command_failed")

        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._logger.warning("overlay_cpp_stop_timeout pid=%s", process.pid)
            process.kill()
            process.wait(timeout=2)
        finally:
            self._process = None
            self._ready.clear()
            self._startup_complete.set()
            if self._stdout_thread is not None:
                self._stdout_thread.join(timeout=1)
                self._stdout_thread = None
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=1)
                self._stderr_thread = None

    def _send_command(self, command: str, **payload: object) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("overlay_ui process is not running")
        if process.poll() is not None:
            raise RuntimeError(f"overlay_ui exited with code {process.returncode}")

        line = encode_overlay_command(command, **payload)
        with self._write_lock:
            process.stdin.write(f"{line}\n")
            process.stdin.flush()

    def _read_stdout(self, stream: TextIO | None) -> None:
        if stream is None:
            self._startup_error = "overlay_ui stdout missing"
            self._startup_complete.set()
            return

        for line in stream:
            raw = line.strip()
            if not raw:
                continue
            try:
                event = decode_overlay_event(raw)
            except Exception:
                self._logger.error("overlay_cpp_stdout_invalid=%s", raw)
                continue

            event_name = str(event.get("event", ""))
            self._logger.info("overlay_cpp_event=%s payload=%s", event_name, event)
            if event_name == "ready":
                self._ready.set()
                self._startup_complete.set()
            elif event_name == "error":
                self._startup_error = str(event.get("message", "overlay_ui error"))
                self._startup_complete.set()
            elif event_name == "exiting":
                self._startup_complete.set()

        if not self._startup_complete.is_set():
            code = self._process.poll() if self._process is not None else None
            self._startup_error = f"overlay_ui exited before ready: {code}"
            self._startup_complete.set()

    def _read_stderr(self, stream: TextIO | None) -> None:
        if stream is None:
            return
        for line in stream:
            text = line.rstrip()
            if text:
                self._logger.error("overlay_cpp_stderr=%s", text)
