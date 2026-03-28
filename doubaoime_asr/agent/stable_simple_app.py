from __future__ import annotations

import argparse
import asyncio
import contextlib
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import threading
from typing import Literal

from .config import AgentConfig
from .hotkey import hotkey_label
from .injection_manager import TextInjectionManager
from .input_injector import FocusChangedError, FocusTarget
from .overlay_preview import OverlayPreview
from .protocol import decode_event
from .runtime_logging import setup_named_logger
from .win_keyboard_hook import GlobalHotkeyHook


Mode = Literal["recognize", "inject"]


@dataclass(slots=True)
class WorkerSession:
    process: asyncio.subprocess.Process
    stdout_task: asyncio.Task[None]
    stderr_task: asyncio.Task[None]
    wait_task: asyncio.Task[None]
    target: FocusTarget | None = None
    stop_sent: bool = False
    ready: bool = False
    streaming_started: bool = False
    pending_stop: bool = False


class StableVoiceInputApp:
    def __init__(
        self,
        config: AgentConfig,
        *,
        mode: Mode = "inject",
        enable_tray: bool = True,
        console: bool = False,
    ) -> None:
        self.config = config
        self.mode = mode
        self.enable_tray = enable_tray
        self.console = console

        self.logger = setup_named_logger(
            "doubaoime_asr.agent.controller",
            config.default_controller_log_path(),
        )
        self.injection_manager = TextInjectionManager(self.logger)
        self.preview = OverlayPreview()

        self._status = "空闲"
        self._status_lock = threading.Lock()
        self._event_queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        self._listener: GlobalHotkeyHook | None = None
        self._session: WorkerSession | None = None
        self._stopping = False
        self._tray_icon = None
        self._tray_thread: threading.Thread | None = None

    def set_status(self, value: str) -> None:
        with self._status_lock:
            if self._status == value:
                return
            self._status = value
        if self.console:
            print(value, flush=True)
        self.logger.info("status=%s", value)
        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.update_menu()

    def _emit(self, kind: str, payload: object = None) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon(self._event_queue.put_nowait, (kind, payload))
        except RuntimeError:
            pass

    def _emit_threadsafe(self, loop: asyncio.AbstractEventLoop, kind: str, payload: object = None) -> None:
        loop.call_soon_threadsafe(self._event_queue.put_nowait, (kind, payload))

    async def run(self) -> int:
        if sys.platform != "win32":
            print("当前稳定版仅支持 Windows。", file=sys.stderr)
            return 1

        self.config.save()
        if self.console:
            print("=" * 50)
            print("豆包语音输入 - 全局版")
            print("=" * 50)
            print(f"模式: {self.mode}")
            print(f"热键: {hotkey_label(self.config.hotkey)}")
            print("使用方式：按住热键说话，松开结束。")
            print("按 Ctrl+C 退出。")
            print()

        self.preview.start()
        self.set_status("空闲")

        loop = asyncio.get_running_loop()
        self._listener = GlobalHotkeyHook(
            self.config.hotkey,
            on_press=lambda: self._emit_threadsafe(loop, "press"),
            on_release=lambda: self._emit_threadsafe(loop, "release"),
        )
        self._listener.start()
        if self.enable_tray:
            self._start_tray(loop)

        try:
            while not self._stopping:
                kind, payload = await self._event_queue.get()
                try:
                    if kind == "press":
                        await self._handle_press()
                    elif kind == "release":
                        await self._handle_release()
                    elif kind == "worker_event":
                        await self._handle_worker_event(payload)
                    elif kind == "worker_exit":
                        await self._handle_worker_exit(int(payload))
                    elif kind == "stop":
                        break
                except Exception:
                    self.logger.exception("controller_event_failed kind=%s payload=%s", kind, payload)
                    self.set_status("控制器异常，请查看 controller.log")
                    await self._terminate_session()
        except KeyboardInterrupt:
            self.stop()
        finally:
            await self._terminate_session()
            if self._listener is not None:
                self._listener.stop()
                self._listener = None
            if self._tray_icon is not None:
                with contextlib.suppress(Exception):
                    self._tray_icon.stop()
            if self._tray_thread is not None:
                self._tray_thread.join(timeout=2)
                self._tray_thread = None
            self.preview.stop()
        return 0

    async def _handle_press(self) -> None:
        self.logger.info("hotkey_down")
        if self._session is not None:
            return

        target: FocusTarget | None = None
        if self.mode == "inject":
            target = self.injection_manager.capture_target()
            if target is None:
                self.set_status("未检测到可写入焦点")
                return
            self.logger.info("captured_target hwnd=%s focus_hwnd=%s", target.hwnd, target.focus_hwnd)

        process = await self._spawn_worker()
        self._session = WorkerSession(
            process=process,
            stdout_task=asyncio.create_task(self._read_worker_stdout(process.stdout)),
            stderr_task=asyncio.create_task(self._read_worker_stderr(process.stderr)),
            wait_task=asyncio.create_task(self._wait_worker(process)),
            target=target,
        )
        self.preview.hide()
        self.set_status("启动识别中…")

    async def _handle_release(self) -> None:
        self.logger.info("hotkey_up")
        if self._session is None or self._session.stop_sent:
            return
        if not self._session.ready:
            self._session.pending_stop = True
            self.logger.info("worker_stop_deferred reason=not_ready")
            self.set_status("等待录音就绪…")
            return
        await self._send_stop("worker_stop_sent", "等待最终结果…")

    async def _send_stop(self, log_tag: str, status: str) -> None:
        if self._session is None or self._session.process.stdin is None:
            return
        self._session.process.stdin.write(b"STOP\n")
        await self._session.process.stdin.drain()
        self._session.stop_sent = True
        self._session.pending_stop = False
        self.logger.info(log_tag)
        self.set_status(status)

    async def _send_stop_if_needed(self) -> None:
        if self._session is None:
            return
        if self._session.stop_sent or not self._session.pending_stop:
            return
        await self._send_stop("worker_stop_sent_after_ready", "等待最终结果…")

    async def _spawn_worker(self) -> asyncio.subprocess.Process:
        command = self._build_worker_command()
        self.logger.info("worker_spawn cmd=%s", command)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.cwd()),
            env=self._build_worker_env(),
        )
        self.logger.info("worker_spawned pid=%s", process.pid)
        return process

    def _build_worker_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _build_worker_command(self) -> list[str]:
        args = [
            "--worker",
            "--credential-path",
            self.config.credential_path or AgentConfig.default().credential_path or "",
        ]
        if self.config.microphone_device is not None:
            args.extend(["--mic-device", str(self.config.microphone_device)])

        if getattr(sys, "frozen", False):
            return [sys.executable, *args]
        return [sys.executable, "-m", "doubaoime_asr.agent.stable_main", *args]

    async def _read_worker_stdout(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                event = decode_event(raw)
            except json.JSONDecodeError:
                self.logger.error("worker_stdout_invalid=%s", raw)
                continue
            self._emit("worker_event", event)

    async def _read_worker_stderr(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            self.logger.error("worker_stderr=%s", line.decode("utf-8", errors="replace").rstrip())

    async def _wait_worker(self, process: asyncio.subprocess.Process) -> None:
        code = await process.wait()
        self._emit("worker_exit", code)

    async def _handle_worker_event(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        event_type = event.get("type")
        self.logger.info("worker_event=%s payload=%s", event_type, event)

        if event_type == "ready":
            if self._session is not None:
                self._session.ready = True
            self.set_status("录音中，等待说话…")
            await self._send_stop_if_needed()
        elif event_type == "streaming_started":
            if self._session is not None:
                self._session.streaming_started = True
            self.logger.info(
                "worker_streaming_started chunks=%s bytes=%s",
                event.get("chunks"),
                event.get("bytes"),
            )
            await self._send_stop_if_needed()
        elif event_type == "status":
            message = str(event.get("message", ""))
            if message:
                self.set_status(message)
        elif event_type == "interim":
            text = str(event.get("text", ""))
            if text:
                if self.console:
                    print(f"\r[识别中] {text}", end="", flush=True)
                self.preview.show(text)
                self.set_status(f"识别中: {text[-24:]}")
        elif event_type == "final":
            text = str(event.get("text", ""))
            if self.console:
                print(f"\r[最终] {text}          ", flush=True)
            self.preview.hide()
            await self._inject_final(text)
            self.set_status(f"最终结果: {text[-24:]}")
        elif event_type == "error":
            self.preview.hide()
            message = str(event.get("message", "语音识别失败"))
            self.set_status(f"识别失败: {message}")
        elif event_type == "finished":
            self.preview.hide()
            if not self._status.startswith("识别失败"):
                self.set_status("空闲")

    async def _inject_final(self, text: str) -> None:
        if self.mode != "inject":
            return
        if self._session is None or self._session.target is None:
            return
        try:
            result = await self.injection_manager.inject_text(self._session.target, text)
            self.logger.info(
                "inject_success method=%s clipboard_restored=%s",
                result.method,
                result.restored_clipboard,
            )
        except FocusChangedError:
            self.logger.warning("inject_focus_changed")
            self.set_status("焦点已变化，仅保留识别")
        except Exception:
            self.logger.exception("inject_final_failed")
            self.set_status("注入失败，仅保留识别")

    async def _handle_worker_exit(self, code: int) -> None:
        self.logger.info("worker_exit code=%s", code)
        if code != 0 and not self._status.startswith("识别失败"):
            self.set_status(f"识别进程异常退出: {code}")
        await self._cleanup_session()

    async def _cleanup_session(self) -> None:
        if self._session is None:
            return
        for task in (self._session.stdout_task, self._session.stderr_task, self._session.wait_task):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._session = None
        if not self._status.startswith("识别失败"):
            self.set_status("空闲")

    async def _terminate_session(self) -> None:
        if self._session is None:
            return
        if self._session.process.stdin is not None and not self._session.stop_sent:
            with contextlib.suppress(Exception):
                self._session.process.stdin.write(b"STOP\n")
                await self._session.process.stdin.drain()
        with contextlib.suppress(ProcessLookupError):
            self._session.process.kill()
        await self._cleanup_session()

    def stop(self) -> None:
        self._stopping = True
        with contextlib.suppress(Exception):
            self._event_queue.put_nowait(("stop", None))

    def _start_tray(self, loop: asyncio.AbstractEventLoop) -> None:
        import pystray
        from PIL import Image, ImageDraw

        def build_icon():
            image = Image.new("RGBA", (64, 64), (20, 20, 20, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=(38, 110, 255, 255))
            draw.rectangle((26, 18, 38, 42), fill=(255, 255, 255, 255))
            draw.ellipse((22, 12, 42, 28), fill=(255, 255, 255, 255))
            draw.rectangle((22, 44, 42, 48), fill=(255, 255, 255, 255))
            return image

        def open_log_dir(icon=None, item=None):
            path = self.config.default_log_dir()
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)  # type: ignore[attr-defined]

        def stop_app(icon=None, item=None):
            loop.call_soon_threadsafe(self.stop)

        icon = pystray.Icon(
            "doubao-voice-agent",
            build_icon(),
            "Doubao Voice Input",
            menu=pystray.Menu(
                pystray.MenuItem(lambda item: f"状态: {self._status}", None, enabled=False),
                pystray.MenuItem(lambda item: f"模式: {self.mode}", None, enabled=False),
                pystray.MenuItem(lambda item: f"热键: {hotkey_label(self.config.hotkey)}", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("打开日志目录", open_log_dir),
                pystray.MenuItem("退出", stop_app),
            ),
        )
        self._tray_icon = icon
        self._tray_thread = threading.Thread(target=icon.run, name="doubao-tray", daemon=True)
        self._tray_thread.start()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Doubao 语音输入全局版")
    parser.add_argument(
        "--mode",
        choices=("recognize", "inject"),
        default="inject",
        help="recognize 仅识别；inject 识别后尝试写入当前焦点输入框",
    )
    parser.add_argument("--hotkey", help="覆盖默认热键，例如 f8 / f9 / space")
    parser.add_argument("--mic-device", help="覆盖麦克风设备名称或索引")
    parser.add_argument("--credential-path", help="覆盖凭据文件路径")
    parser.add_argument("--render-debounce-ms", type=int, help="流式渲染防抖毫秒数")
    parser.add_argument("--console", action="store_true", help="显示控制台输出，便于调试")
    parser.add_argument("--no-tray", action="store_true", help="禁用系统托盘，仅作为前台常驻工具运行")
    return parser


def build_config_from_args(args: argparse.Namespace | None = None) -> AgentConfig:
    if args is None:
        parser = build_arg_parser()
        args = parser.parse_args()

    config = AgentConfig.load()
    if getattr(args, "hotkey", None):
        config.hotkey = args.hotkey
    if getattr(args, "mic_device", None):
        config.microphone_device = (
            int(args.mic_device)
            if str(args.mic_device).isdigit()
            else args.mic_device
        )
    if getattr(args, "credential_path", None):
        config.credential_path = args.credential_path
    if getattr(args, "render_debounce_ms", None) is not None:
        config.render_debounce_ms = args.render_debounce_ms
    return config
