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

from .config import AgentConfig, SUPPORTED_INJECTION_POLICIES
from .injection_manager import TextInjectionManager
from .input_injector import FocusChangedError, FocusTarget
from .overlay_preview import OverlayPreview
from .overlay_scheduler import OverlayRenderScheduler
from .protocol import decode_event
from .runtime_logging import setup_named_logger
from .settings_window import SettingsWindowController
from .win_keyboard_hook import GlobalHotkeyHook
from .win_hotkey import normalize_hotkey, vk_from_hotkey, vk_to_display, vk_to_hotkey


Mode = Literal["recognize", "inject"]


@dataclass(slots=True)
class WorkerSession:
    session_id: int
    process: asyncio.subprocess.Process
    stdout_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None
    wait_task: asyncio.Task[None] | None = None
    process_ready: bool = False
    active: bool = False
    target: FocusTarget | None = None
    mode: Mode = "inject"
    stop_sent: bool = False
    ready: bool = False
    streaming_started: bool = False
    pending_stop: bool = False

    def begin(self, target: FocusTarget | None, mode: Mode) -> None:
        self.active = True
        self.target = target
        self.mode = mode
        self.stop_sent = False
        self.ready = False
        self.streaming_started = False
        self.pending_stop = False

    def clear_active(self) -> None:
        self.active = False
        self.target = None
        self.mode = "inject"
        self.stop_sent = False
        self.ready = False
        self.streaming_started = False
        self.pending_stop = False


class StableVoiceInputApp:
    def __init__(
        self,
        config: AgentConfig,
        *,
        mode: Mode | None = None,
        enable_tray: bool = True,
        console: bool = False,
    ) -> None:
        self.config = config
        self.mode = mode or config.mode
        self.config.mode = self.mode
        self.enable_tray = enable_tray
        self.console = console

        self.logger = setup_named_logger(
            "doubaoime_asr.agent.controller",
            config.default_controller_log_path(),
        )
        self.injection_manager = TextInjectionManager(self.logger, policy=self.config.injection_policy)
        self.preview = OverlayPreview(self.logger, self.config)
        self.overlay_scheduler = OverlayRenderScheduler(
            self.preview,
            logger=self.logger,
            fps=self.config.overlay_render_fps,
        )

        self._status = "空闲"
        self._status_lock = threading.Lock()
        self._event_queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        self._listener: GlobalHotkeyHook | None = None
        self._session: WorkerSession | None = None
        self._stopping = False
        self._tray_icon = None
        self._tray_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._settings_controller: SettingsWindowController | None = None
        self._pending_listener_rebind = False
        self._pending_worker_restart = False
        self._next_worker_session_id = 0

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
            print(f"热键: {self.config.effective_hotkey_display()}")
            print("使用方式：按住热键说话，松开结束。")
            print("按 Ctrl+C 退出。")
            print()

        self.preview.start()
        self.preview.configure(self.config)
        self.overlay_scheduler.configure(self.config)
        self.set_status("空闲")

        loop = asyncio.get_running_loop()
        self._loop = loop
        self._listener = self._build_listener(loop, self.config.effective_hotkey_vk())
        self._listener.start()
        self._settings_controller = SettingsWindowController(
            logger=self.logger,
            get_current_config=lambda: self.config,
            on_save=lambda config: self._emit_threadsafe(loop, "apply_config", config),
        )
        if self.enable_tray:
            self._start_tray(loop)

        try:
            await self._ensure_worker()
        except Exception:
            self.logger.exception("worker_prewarm_failed")

        try:
            while not self._stopping:
                kind, payload = await self._event_queue.get()
                try:
                    if kind == "press":
                        await self._handle_press()
                    elif kind == "release":
                        await self._handle_release()
                    elif kind == "worker_event":
                        if isinstance(payload, tuple) and len(payload) == 2:
                            session_id, event = payload
                            await self._handle_worker_event(int(session_id), event)
                    elif kind == "worker_exit":
                        if isinstance(payload, tuple) and len(payload) == 2:
                            session_id, code = payload
                            await self._handle_worker_exit(int(session_id), int(code))
                    elif kind == "apply_config":
                        if isinstance(payload, AgentConfig):
                            await self._apply_config(payload)
                    elif kind == "stop":
                        break
                except Exception:
                    self.logger.exception("controller_event_failed kind=%s payload=%s", kind, payload)
                    self.set_status("控制器异常，请查看 controller.log")
                    await self._terminate_worker()
        except KeyboardInterrupt:
            self.stop()
        finally:
            await self._terminate_worker()
            if self._listener is not None:
                self._listener.stop()
                self._listener = None
            if self._tray_icon is not None:
                with contextlib.suppress(Exception):
                    self._tray_icon.stop()
            if self._tray_thread is not None:
                self._tray_thread.join(timeout=2)
                self._tray_thread = None
            if self._settings_controller is not None:
                self._settings_controller.close()
                self._settings_controller = None
            self.preview.stop()
        return 0

    async def _handle_press(self) -> None:
        self.logger.info("hotkey_down")
        session = await self._ensure_worker()
        if session.active:
            return

        target: FocusTarget | None = None
        if self.mode == "inject":
            target = self.injection_manager.capture_target()
            if target is None:
                self.set_status("未检测到可写入焦点")
                return
            self.logger.info("captured_target hwnd=%s focus_hwnd=%s", target.hwnd, target.focus_hwnd)

        session.begin(target, self.mode)
        try:
            await self._send_worker_command("START")
        except Exception:
            session.clear_active()
            self.logger.exception("worker_start_command_failed")
            self.set_status("启动识别失败，请查看 controller.log")
            await self._restart_worker()
            return
        await self.overlay_scheduler.hide("session_start")
        self.set_status("启动识别中…")

    async def _handle_release(self) -> None:
        self.logger.info("hotkey_up")
        if self._session is None or not self._session.active or self._session.stop_sent:
            return
        if not self._session.ready:
            self._session.pending_stop = True
            self.logger.info("worker_stop_deferred reason=not_ready")
            self.set_status("等待录音就绪…")
            return
        await self._send_stop("worker_stop_sent", "等待最终结果…")

    async def _send_worker_command(self, command: str) -> None:
        if self._session is None or self._session.process.stdin is None:
            raise RuntimeError("worker process is not running")
        self._session.process.stdin.write(f"{command}\n".encode("utf-8"))
        await self._session.process.stdin.drain()

    async def _send_stop(self, log_tag: str, status: str) -> None:
        if self._session is None:
            return
        await self._send_worker_command("STOP")
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

    async def _ensure_worker(self) -> WorkerSession:
        if self._session is not None and self._session.process.returncode is None:
            if self._session.process_ready:
                return self._session
        if self._session is not None and self._session.process.returncode is not None:
            await self._dispose_worker()

        process = await self._spawn_worker()
        self._next_worker_session_id += 1
        session = WorkerSession(
            session_id=self._next_worker_session_id,
            process=process,
        )
        session.stdout_task = asyncio.create_task(self._read_worker_stdout(process.stdout, session))
        session.stderr_task = asyncio.create_task(self._read_worker_stderr(process.stderr))
        session.wait_task = asyncio.create_task(self._wait_worker(process, session.session_id))
        self._session = session

        deadline = asyncio.get_running_loop().time() + 2.5
        while asyncio.get_running_loop().time() < deadline:
            if session.process_ready:
                return session
            if session.process.returncode is not None:
                break
            await asyncio.sleep(0.02)

        await self._terminate_session_process(session)
        await self._dispose_worker()
        raise RuntimeError("worker process did not become ready")

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

    async def _read_worker_stdout(self, stream: asyncio.StreamReader | None, session: WorkerSession) -> None:
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
            if event.get("type") == "worker_ready":
                session.process_ready = True
            self._emit("worker_event", (session.session_id, event))

    async def _read_worker_stderr(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            self.logger.error("worker_stderr=%s", line.decode("utf-8", errors="replace").rstrip())

    async def _wait_worker(self, process: asyncio.subprocess.Process, session_id: int) -> None:
        code = await process.wait()
        self._emit("worker_exit", (session_id, code))

    async def _handle_worker_event(self, session_id: int, event: object) -> None:
        if not isinstance(event, dict):
            return
        event_type = event.get("type")
        self.logger.info("worker_event=%s payload=%s", event_type, event)

        if self._session is None or self._session.session_id != session_id:
            self.logger.info(
                "worker_event_ignored session_id=%s current_session_id=%s type=%s",
                session_id,
                self._session.session_id if self._session is not None else None,
                event_type,
            )
            return
        session = self._session

        if event_type == "worker_ready":
            session.process_ready = True
            return

        if event_type == "ready":
            if session.active:
                session.ready = True
                self.set_status("录音中，等待说话")
                await self._send_stop_if_needed()
        elif event_type == "streaming_started":
            if session.active:
                session.streaming_started = True
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
            if text and session.active:
                if self.console:
                    print(f"\r[识别中] {text}", end="", flush=True)
                await self.overlay_scheduler.submit_interim(text)
                self.set_status(f"识别中: {text[-24:]}")
        elif event_type == "final":
            text = str(event.get("text", ""))
            if self.console:
                print(f"\r[最终] {text}          ", flush=True)
            await self.overlay_scheduler.hide("final")
            await self._inject_final(text)
            self.set_status(f"最终结果: {text[-24:]}")
        elif event_type == "error":
            await self.overlay_scheduler.hide("error")
            message = str(event.get("message", "语音识别失败"))
            self.set_status(f"识别失败: {message}")
            await self._clear_active_session()
        elif event_type == "finished":
            await self.overlay_scheduler.hide("finished")
            if not self._status.startswith("识别失败"):
                self.set_status("空闲")
            await self._clear_active_session()

    async def _inject_final(self, text: str) -> None:
        if self._session is None or self._session.mode != "inject":
            return
        if self._session.target is None:
            return
        try:
            result = await self.injection_manager.inject_text(self._session.target, text)
            self.logger.info(
                "inject_success method=%s target_profile=%s clipboard_touched=%s clipboard_restored=%s",
                result.method,
                result.target_profile,
                result.clipboard_touched,
                result.restored_clipboard,
            )
        except FocusChangedError:
            self.logger.warning("inject_focus_changed")
            self.set_status("焦点已变化，仅保留识别")
        except Exception:
            self.logger.exception("inject_final_failed")
            self.set_status("注入失败，仅保留识别")

    async def _handle_worker_exit(self, session_id: int, code: int) -> None:
        if self._session is None or self._session.session_id != session_id:
            self.logger.info(
                "worker_exit_ignored session_id=%s current_session_id=%s code=%s",
                session_id,
                self._session.session_id if self._session is not None else None,
                code,
            )
            return
        self.logger.info("worker_exit code=%s", code)
        if not self._stopping and code != 0 and not self._status.startswith("识别失败"):
            self.set_status(f"识别进程异常退出: {code}")
        await self._dispose_worker()
        if not self._stopping:
            self._apply_pending_listener_rebind("listener_rebind_failed_after_worker_exit")
        if not self._stopping and self._pending_worker_restart:
            self._pending_worker_restart = False
            with contextlib.suppress(Exception):
                await self._ensure_worker()

    async def _clear_active_session(self) -> None:
        if self._session is None:
            return
        self._session.clear_active()
        self._apply_pending_listener_rebind("listener_rebind_failed_after_session")
        if self._pending_worker_restart:
            self._pending_worker_restart = False
            await self._restart_worker()

    def _apply_pending_listener_rebind(self, log_tag: str) -> None:
        if not self._pending_listener_rebind:
            return
        self._pending_listener_rebind = False
        try:
            self._rebind_listener(self.config.effective_hotkey_vk())
        except Exception:
            self.logger.exception(log_tag)

    async def _dispose_worker(self) -> None:
        if self._session is None:
            return
        session = self._session
        for task in (session.stdout_task, session.stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        wait_task = session.wait_task
        if wait_task is not None and not wait_task.done():
            if session.process.returncode is None:
                wait_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await wait_task
            else:
                with contextlib.suppress(Exception):
                    await wait_task
        self._session = None

    async def _terminate_session_process(self, session: WorkerSession) -> None:
        process = session.process
        if self._session is session and process.stdin is not None and process.returncode is None:
            with contextlib.suppress(Exception):
                await self._send_worker_command("EXIT")
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except (asyncio.TimeoutError, ProcessLookupError):
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
                await asyncio.wait_for(process.wait(), timeout=2)

    async def _terminate_worker(self) -> None:
        if self._session is None:
            return
        await self.overlay_scheduler.hide("terminate_worker")
        await self._terminate_session_process(self._session)
        await self._dispose_worker()

    async def _restart_worker(self) -> None:
        await self._terminate_worker()
        if not self._stopping:
            await self._ensure_worker()

    def stop(self) -> None:
        self._stopping = True
        with contextlib.suppress(Exception):
            self._event_queue.put_nowait(("stop", None))

    def _build_listener(self, loop: asyncio.AbstractEventLoop, hotkey_vk: int) -> GlobalHotkeyHook:
        return GlobalHotkeyHook(
            hotkey_vk,
            on_press=lambda: self._emit_threadsafe(loop, "press"),
            on_release=lambda: self._emit_threadsafe(loop, "release"),
        )

    def _rebind_listener(self, hotkey_vk: int) -> None:
        if self._loop is None:
            return
        listener = self._build_listener(self._loop, hotkey_vk)
        listener.start()
        old_listener = self._listener
        self._listener = listener
        if old_listener is not None:
            old_listener.stop()

    async def _apply_config(self, new_config: AgentConfig) -> None:
        old_config = self.config
        old_mode = self.mode
        old_pending_listener_rebind = self._pending_listener_rebind
        old_pending_worker_restart = self._pending_worker_restart
        hotkey_changed = old_config.effective_hotkey_vk() != new_config.effective_hotkey_vk()
        worker_changed = (
            old_config.credential_path != new_config.credential_path
            or old_config.microphone_device != new_config.microphone_device
        )
        session_active = self._session is not None and self._session.active
        listener_rebound = False
        worker_restarted = False

        try:
            self.config = new_config
            self.mode = new_config.mode
            self.injection_manager.set_policy(new_config.injection_policy)
            self.preview.configure(new_config)
            self.overlay_scheduler.configure(new_config)

            if hotkey_changed:
                if session_active:
                    self._pending_listener_rebind = True
                else:
                    self._rebind_listener(new_config.effective_hotkey_vk())
                    listener_rebound = True

            if worker_changed:
                if session_active:
                    self._pending_worker_restart = True
                else:
                    await self._restart_worker()
                    worker_restarted = True

            self.config.save()
        except Exception:
            self.logger.exception("apply_config_failed")
            self.config = old_config
            self.mode = old_mode
            self._pending_listener_rebind = old_pending_listener_rebind
            self._pending_worker_restart = old_pending_worker_restart
            self.injection_manager.set_policy(old_config.injection_policy)
            self.preview.configure(old_config)
            self.overlay_scheduler.configure(old_config)
            if listener_rebound:
                try:
                    self._rebind_listener(old_config.effective_hotkey_vk())
                except Exception:
                    self.logger.exception("apply_config_rollback_listener_failed")
            if worker_restarted:
                try:
                    await self._restart_worker()
                except Exception:
                    self.logger.exception("apply_config_rollback_worker_failed")
            try:
                self.config.save()
            except Exception:
                self.logger.exception("apply_config_rollback_save_failed")
                self.set_status("设置保存失败，请检查日志并手动确认配置")
                return
            self.set_status("设置保存失败，已恢复旧配置")
            return

        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.update_menu()

        if not session_active:
            if hotkey_changed:
                self.set_status(f"热键已更新为 {new_config.effective_hotkey_display()}")
            elif worker_changed:
                self.set_status("设置已保存并重启识别服务")
            else:
                self.set_status("设置已保存")
        else:
            self.logger.info("settings_saved_during_active_session")

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

        def open_settings(icon=None, item=None):
            if self._settings_controller is not None:
                self._settings_controller.show(self.config)

        def stop_app(icon=None, item=None):
            loop.call_soon_threadsafe(self.stop)

        icon = pystray.Icon(
            "doubao-voice-agent",
            build_icon(),
            "Doubao Voice Input",
            menu=pystray.Menu(
                pystray.MenuItem(lambda item: f"状态: {self._status}", None, enabled=False),
                pystray.MenuItem(lambda item: f"模式: {self.mode}", None, enabled=False),
                pystray.MenuItem(lambda item: f"热键: {self.config.effective_hotkey_display()}", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("设置", open_settings),
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
        default=argparse.SUPPRESS,
        help="recognize 仅识别；inject 识别后尝试写入当前焦点输入框",
    )
    parser.add_argument("--hotkey", help="覆盖默认热键，例如 f8 / f9 / space")
    parser.add_argument("--mic-device", help="覆盖麦克风设备名称或索引")
    parser.add_argument("--credential-path", help="覆盖凭据文件路径")
    parser.add_argument(
        "--injection-policy",
        choices=SUPPORTED_INJECTION_POLICIES,
        default=argparse.SUPPRESS,
        help="direct_only 仅直接输入；direct_then_clipboard 失败时允许剪贴板回退",
    )
    parser.add_argument("--render-debounce-ms", type=int, help="流式渲染防抖毫秒数")
    parser.add_argument("--console", action="store_true", help="显示控制台输出，便于调试")
    parser.add_argument("--no-tray", action="store_true", help="禁用系统托盘，仅作为前台常驻工具运行")
    return parser


def build_config_from_args(args: argparse.Namespace | None = None) -> AgentConfig:
    if args is None:
        parser = build_arg_parser()
        args = parser.parse_args()

    config = AgentConfig.load()
    if getattr(args, "mode", None):
        config.mode = args.mode
    if getattr(args, "hotkey", None):
        hotkey = str(args.hotkey)
        hotkey_vk = vk_from_hotkey(hotkey)
        config.hotkey = normalize_cli_hotkey(hotkey_vk)
        config.hotkey_vk = hotkey_vk
        config.hotkey_display = vk_to_display(hotkey_vk)
    if getattr(args, "mic_device", None):
        config.microphone_device = (
            int(args.mic_device)
            if str(args.mic_device).isdigit()
            else args.mic_device
        )
    if getattr(args, "credential_path", None):
        config.credential_path = args.credential_path
    if getattr(args, "injection_policy", None):
        config.injection_policy = args.injection_policy
    if getattr(args, "render_debounce_ms", None) is not None:
        config.render_debounce_ms = args.render_debounce_ms
    return config


def normalize_cli_hotkey(hotkey_vk: int) -> str:
    return vk_to_hotkey(hotkey_vk) or normalize_hotkey(vk_to_display(hotkey_vk))
