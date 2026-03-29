from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
import threading
from typing import Optional

from pynput import keyboard

from doubaoime_asr import ASRConfig, ResponseType, transcribe_realtime

from .audio_source import MicrophoneAudioSource
from .composition import CompositionSession
from .config import AgentConfig
from .hotkey import hotkey_label, hotkey_matches
from .input_injector import FocusChangedError, WindowsTextInjector
from .runtime_logging import setup_agent_logger


class DebouncedInterimRenderer:
    def __init__(
        self,
        session: CompositionSession,
        *,
        debounce_ms: int,
    ) -> None:
        self._session = session
        self._debounce_s = max(0, debounce_ms) / 1000.0
        self._pending_text: str | None = None
        self._task: Optional[asyncio.Task[None]] = None
        self._error: BaseException | None = None

    def _raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    async def submit(self, text: str) -> None:
        self._raise_if_failed()
        if self._debounce_s == 0:
            self._session.render_interim(text)
            return

        self._pending_text = text
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        try:
            await asyncio.sleep(self._debounce_s)
            text = self._pending_text
            self._pending_text = None
            if text is not None:
                self._session.render_interim(text)
        except Exception as exc:  # pragma: no cover - surfaced on next await
            self._error = exc

    async def flush(self, text: str) -> None:
        self._raise_if_failed()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._pending_text = None
        self._session.finalize(text)
        self._raise_if_failed()

    async def close(self) -> None:
        if self._pending_text:
            await self.flush(self._pending_text)
            return
        self._raise_if_failed()


class VoiceInputAgent:
    def __init__(
        self,
        config: AgentConfig,
        *,
        enable_tray: bool = True,
    ) -> None:
        self.config = config
        self.enable_tray = enable_tray
        self._injector = WindowsTextInjector()
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop,
            name="doubao-voice-agent-loop",
            daemon=True,
        )
        self._listener: keyboard.Listener | None = None
        self._icon = None
        self._stop_event = threading.Event()
        self._status_lock = threading.Lock()
        self._status = "空闲"
        self._session_active = False
        self._hotkey_held = False
        self._session_task: asyncio.Task[None] | None = None
        self._microphone: MicrophoneAudioSource | None = None
        self._logger = setup_agent_logger(config.default_log_path())

    @property
    def status(self) -> str:
        with self._status_lock:
            return self._status

    def set_status(self, value: str) -> None:
        with self._status_lock:
            self._status = value
        print(value, flush=True)
        self._logger.info("status=%s", value)
        if self._icon is not None:
            with contextlib.suppress(Exception):
                self._icon.update_menu()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro) -> asyncio.Future:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)

        def _record_failure(done_future) -> None:
            try:
                done_future.result()
            except Exception:
                self._logger.exception("background coroutine failed")

        future.add_done_callback(_record_failure)
        return future

    def _on_press(self, key) -> None:
        if not hotkey_matches(key, self.config.hotkey):
            return
        if self._hotkey_held:
            return
        self._hotkey_held = True
        self._submit(self._start_session())

    def _on_release(self, key) -> None:
        if not hotkey_matches(key, self.config.hotkey):
            return
        self._hotkey_held = False
        self._submit(self._stop_session())

    async def _start_session(self) -> None:
        if self._session_active:
            return
        try:
            target = self._injector.capture_target()
            if target is None:
                self.set_status("未检测到可写入焦点")
                return

            self._logger.info("captured target hwnd=%s", target.hwnd)
            self._session_active = True
            self.set_status("准备录音…")

            asr_config = ASRConfig(credential_path=self.config.credential_path)
            self._microphone = MicrophoneAudioSource(
                sample_rate=asr_config.sample_rate,
                channels=asr_config.channels,
                frame_duration_ms=asr_config.frame_duration_ms,
                device=self.config.microphone_device,
                on_status=self.set_status,
            )
            self._microphone.start()
            session = CompositionSession(self._injector, target)
            renderer = DebouncedInterimRenderer(
                session,
                debounce_ms=self.config.render_debounce_ms,
            )
            self._session_task = asyncio.create_task(
                self._run_session(asr_config, renderer, session)
            )
        except Exception:
            self._session_active = False
            self._microphone = None
            self._logger.exception("failed to start session")
            self.set_status("启动识别失败，请查看日志")

    async def _stop_session(self) -> None:
        if self._microphone is not None:
            self._microphone.stop()
            self.set_status("等待最终结果…")

    async def _run_session(
        self,
        asr_config: ASRConfig,
        renderer: DebouncedInterimRenderer,
        session: CompositionSession,
    ) -> None:
        final_text = ""
        final_applied = False
        try:
            self.set_status("识别中…")
            assert self._microphone is not None
            self._logger.info("session started")

            async for response in transcribe_realtime(
                self._microphone.chunks(),
                config=asr_config,
            ):
                self._logger.info("response=%s text=%s", response.type.name, response.text)
                if response.type == ResponseType.INTERIM_RESULT and response.text:
                    await renderer.submit(response.text)
                    self.set_status(f"识别中: {response.text[-24:]}")
                elif response.type == ResponseType.FINAL_RESULT:
                    final_text = response.text
                    await renderer.flush(response.text)
                    final_applied = True
                    self.set_status(f"最终结果: {response.text[-24:]}")
                elif response.type == ResponseType.ERROR:
                    raise RuntimeError(response.error_msg or "语音识别失败")

            self._logger.info(
                "audio metrics chunks=%s bytes=%s first_chunk=%s",
                self._microphone.chunk_count,
                self._microphone.bytes_captured,
                self._microphone.first_chunk_received,
            )

            if not self._microphone.first_chunk_received:
                self.set_status("未采集到音频，请按住热键后再说话")
                return

            if final_text and not final_applied:
                await renderer.flush(final_text)
            else:
                await renderer.close()
                if session.rendered_text:
                    self.set_status(f"保留最后中间结果: {session.rendered_text[-24:]}")
        except FocusChangedError:
            self._logger.warning("focus changed during composition")
            self.set_status("焦点已变化，已停止流式上屏")
        except Exception as exc:
            if (
                isinstance(exc, RuntimeError)
                and str(exc) == "InternalError"
                and self._microphone is not None
                and not self._microphone.first_chunk_received
            ):
                self._logger.warning(
                    "server returned InternalError before any audio chunk was captured"
                )
                self.set_status("未采集到音频，请按住热键后再说话")
                return
            self._logger.exception("recognition session failed")
            self.set_status(f"识别失败: {exc}")
        finally:
            self._microphone = None
            self._session_task = None
            self._session_active = False
            self._logger.info("session finished")
            if self.status.startswith(("识别中", "等待最终结果", "准备录音")):
                self.set_status("空闲")

    def _build_icon(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (64, 64), (20, 20, 20, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=(38, 110, 255, 255))
        draw.rectangle((26, 18, 38, 42), fill=(255, 255, 255, 255))
        draw.ellipse((22, 12, 42, 28), fill=(255, 255, 255, 255))
        draw.rectangle((22, 44, 42, 48), fill=(255, 255, 255, 255))
        return image

    def _open_config_dir(self) -> None:
        path = self.config.default_dir()
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)  # type: ignore[attr-defined]

    def _stop_from_tray(self, icon=None, item=None) -> None:
        self.stop()

    def _run_tray(self) -> None:
        import pystray

        self._icon = pystray.Icon(
            "doubao-voice-agent",
            self._build_icon(),
            "Doubao Voice Input",
            menu=pystray.Menu(
                pystray.MenuItem(
                    lambda item: f"状态: {self.status}",
                    None,
                    enabled=False,
                ),
                pystray.MenuItem(
                    lambda item: f"热键: {hotkey_label(self.config.hotkey)}",
                    None,
                    enabled=False,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("打开配置目录", lambda icon, item: self._open_config_dir()),
                pystray.MenuItem("退出", self._stop_from_tray),
            ),
        )
        self._icon.run()

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._microphone is not None:
            self._microphone.stop()
        if self._icon is not None:
            with contextlib.suppress(Exception):
                self._icon.stop()
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def run(self) -> int:
        if sys.platform != "win32":
            print("当前桌面代理仅支持 Windows。", file=sys.stderr)
            return 1

        self.config.save()
        self._loop_thread.start()
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()
        self.set_status("空闲")

        try:
            if self.enable_tray:
                self._run_tray()
            else:
                self._stop_event.wait()
        except KeyboardInterrupt:
            self.stop()
        finally:
            self.stop()
            if self._loop_thread.is_alive():
                self._loop_thread.join(timeout=2)
        return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Doubao Windows 流式语音输入代理")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="不显示系统托盘，仅在前台保持热键监听",
    )
    parser.add_argument("--hotkey", help="覆盖默认热键，例如 right_ctrl / f9 / space")
    parser.add_argument("--mic-device", help="覆盖麦克风设备名称或索引")
    parser.add_argument("--credential-path", help="覆盖凭据文件路径")
    parser.add_argument("--render-debounce-ms", type=int, help="流式渲染防抖毫秒数")
    return parser


def build_config_from_args(args: argparse.Namespace | None = None) -> AgentConfig:
    if args is None:
        parser = build_arg_parser()
        args = parser.parse_args()
    config = AgentConfig.load()
    if args.hotkey:
        config.hotkey = args.hotkey
    if args.mic_device:
        config.microphone_device = (
            int(args.mic_device)
            if str(args.mic_device).isdigit()
            else args.mic_device
        )
    if args.credential_path:
        config.credential_path = args.credential_path
    if args.render_debounce_ms is not None:
        config.render_debounce_ms = args.render_debounce_ms
    return config
