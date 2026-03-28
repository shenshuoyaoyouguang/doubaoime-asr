from __future__ import annotations

import queue
import threading


class OverlayPreview:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._queue: "queue.Queue[tuple[str, str | None]]" = queue.Queue()
        self._started = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="doubao-preview", daemon=True)
        self._thread.start()
        self._started.wait(timeout=2)

    def show(self, text: str) -> None:
        self._queue.put(("show", text))

    def hide(self) -> None:
        self._queue.put(("hide", None))

    def stop(self) -> None:
        self._queue.put(("stop", None))
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

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

        def position_window() -> None:
            window.update_idletasks()
            screen_w = window.winfo_screenwidth()
            screen_h = window.winfo_screenheight()
            width = window.winfo_reqwidth()
            height = window.winfo_reqheight()
            x = max(20, (screen_w - width) // 2)
            y = max(20, screen_h - height - 120)
            window.geometry(f"+{x}+{y}")

        def pump() -> None:
            try:
                while True:
                    action, payload = self._queue.get_nowait()
                    if action == "show" and payload:
                        label.configure(text=payload)
                        position_window()
                        window.deiconify()
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
