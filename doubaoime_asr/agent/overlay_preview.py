from __future__ import annotations

import logging
import queue
import threading

from .config import AgentConfig
from .overlay_preview_cpp import OverlayPreviewCpp


class TkOverlayPreview:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._started = threading.Event()
        self._config = AgentConfig()
        self._last_seq = -1

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="doubao-preview", daemon=True)
        self._thread.start()
        self._started.wait(timeout=2)

    def show(self, text: str, *, seq: int = 0, kind: str = "interim") -> None:
        self._queue.put(("show", (text, seq, kind)))

    def hide(self, reason: str = "") -> None:
        self._queue.put(("hide", reason))

    def stop(self) -> None:
        self._queue.put(("stop", None))
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def configure(self, config: AgentConfig) -> None:
        self._config = config
        self._queue.put(("configure", None))

    def _run(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        window = tk.Toplevel(root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg="#111111")
        window.withdraw()

        label = tk.Label(
            window,
            text="",
            fg="white",
            bg="#111111",
            padx=16,
            pady=10,
            font=("Microsoft YaHei UI", 12),
            justify="left",
            wraplength=560,
        )
        label.pack()

        def apply_config() -> None:
            window.attributes("-alpha", max(0.35, min(1.0, self._config.overlay_opacity_percent / 100.0)))
            label.configure(
                font=("Microsoft YaHei UI", self._config.overlay_font_size),
                wraplength=self._config.overlay_max_width,
            )

        def position_window() -> None:
            window.update_idletasks()
            screen_w = window.winfo_screenwidth()
            screen_h = window.winfo_screenheight()
            width = window.winfo_reqwidth()
            height = window.winfo_reqheight()
            x = max(20, (screen_w - width) // 2)
            y = max(20, screen_h - height - self._config.overlay_bottom_offset)
            window.geometry(f"+{x}+{y}")

        def pump() -> None:
            try:
                while True:
                    action, payload = self._queue.get_nowait()
                    if action == "show" and isinstance(payload, tuple):
                        text, seq, _kind = payload
                        if seq < self._last_seq:
                            continue
                        self._last_seq = seq
                        apply_config()
                        label.configure(text=text)
                        position_window()
                        window.deiconify()
                    elif action == "configure":
                        apply_config()
                        if window.state() != "withdrawn":
                            position_window()
                    elif action == "hide":
                        window.withdraw()
                    elif action == "stop":
                        root.quit()
                        return
            except queue.Empty:
                pass
            root.after(50, pump)

        self._started.set()
        pump()
        root.mainloop()
        window.destroy()
        root.destroy()


class OverlayPreview:
    def __init__(self, logger: logging.Logger | None = None, config: AgentConfig | None = None) -> None:
        self._logger = logger or logging.getLogger("doubaoime_asr.agent.overlay")
        self._config = config or AgentConfig()
        self._backend: OverlayPreviewCpp | TkOverlayPreview | None = None
        self._using_legacy = False

    def start(self) -> None:
        self._ensure_backend_started()

    def show(self, text: str, *, seq: int = 0, kind: str = "interim") -> None:
        self._invoke("show", text, seq=seq, kind=kind)

    def hide(self, reason: str = "") -> None:
        self._invoke("hide", reason=reason)

    def configure(self, config: AgentConfig) -> None:
        self._config = config
        if self._backend is not None:
            self._invoke("configure", config)

    def stop(self) -> None:
        if self._backend is None:
            return
        try:
            self._backend.stop()
        finally:
            self._backend = None

    def _ensure_backend_started(self) -> None:
        if self._backend is not None:
            return
        try:
            backend = OverlayPreviewCpp(logger=self._logger)
            backend.start()
            backend.configure(self._config)
            self._backend = backend
            self._using_legacy = False
            self._logger.info("overlay_backend=native")
        except Exception:
            self._logger.exception("overlay_native_start_failed")
            self._activate_legacy_backend(log_message="overlay_fallback_start_failed")

    def _invoke(self, method: str, *args: object, **kwargs: object) -> None:
        self._ensure_backend_started()
        if self._backend is None:
            return
        try:
            getattr(self._backend, method)(*args, **kwargs)
        except Exception:
            self._logger.exception("overlay_backend_call_failed method=%s", method)
            if self._using_legacy:
                return
            try:
                self._backend.stop()
            except Exception:
                self._logger.exception("overlay_native_stop_failed")
            if not self._activate_legacy_backend(
                log_message="overlay_fallback_failed method=%s",
                method=method,
            ):
                return
            assert self._backend is not None
            try:
                getattr(self._backend, method)(*args, **kwargs)
            except Exception:
                self._logger.exception("overlay_fallback_failed method=%s", method)
                try:
                    self._backend.stop()
                except Exception:
                    self._logger.exception("overlay_fallback_stop_failed")
                self._backend = None
                self._using_legacy = False

    def _activate_legacy_backend(self, *, log_message: str, method: str | None = None) -> bool:
        try:
            backend = TkOverlayPreview()
            backend.start()
            backend.configure(self._config)
        except Exception:
            self._backend = None
            self._using_legacy = False
            if method is None:
                self._logger.exception(log_message)
            else:
                self._logger.exception(log_message, method)
            return False
        self._backend = backend
        self._using_legacy = True
        self._logger.info("overlay_backend=tk_fallback")
        return True
