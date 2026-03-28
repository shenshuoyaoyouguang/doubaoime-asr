from __future__ import annotations

from dataclasses import replace
import logging
import threading
from typing import Callable, Mapping

from .config import (
    AgentConfig,
    INJECTION_POLICY_DIRECT_ONLY,
    INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
    SUPPORTED_INJECTION_POLICIES,
    SUPPORTED_MODES,
)
from .win_hotkey import normalize_hotkey, vk_from_hotkey, vk_to_display, vk_to_hotkey
from .win_keyboard_hook import SingleKeyRecorder


MODE_OPTIONS: list[tuple[str, str]] = [
    ("inject", "自动上屏"),
    ("recognize", "仅识别"),
]

INJECTION_POLICY_OPTIONS: list[tuple[str, str]] = [
    (INJECTION_POLICY_DIRECT_THEN_CLIPBOARD, "智能兼容（默认，失败时用剪贴板）"),
    (INJECTION_POLICY_DIRECT_ONLY, "仅直接输入（绝不动剪贴板）"),
]


class SettingsValidationError(ValueError):
    """设置值非法。"""


def list_microphone_choices(current_value: int | str | None) -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = [("__default__", "系统默认")]
    try:
        import sounddevice as sd

        for index, device in enumerate(sd.query_devices()):
            if int(device.get("max_input_channels", 0)) <= 0:
                continue
            name = str(device.get("name", f"输入设备 {index}"))
            choices.append((f"index:{index}", f"[{index}] {name}"))
    except Exception:
        pass

    if isinstance(current_value, int):
        token = f"index:{current_value}"
        if all(value != token for value, _ in choices):
            choices.append((token, f"[{current_value}] 输入设备 {current_value}"))
    elif isinstance(current_value, str) and current_value:
        token = f"name:{current_value}"
        if all(value != token for value, _ in choices):
            choices.append((token, current_value))

    return choices


def build_config_from_settings_values(
    base_config: AgentConfig,
    values: Mapping[str, str],
) -> AgentConfig:
    hotkey_vk_raw = values.get("hotkey_vk", "").strip()
    if hotkey_vk_raw:
        try:
            hotkey_vk = int(hotkey_vk_raw)
        except ValueError as exc:
            raise SettingsValidationError("热键值无效") from exc
        hotkey_display = values.get("hotkey_display", "").strip() or vk_to_display(hotkey_vk)
        hotkey = vk_to_hotkey(hotkey_vk) or normalize_hotkey(hotkey_display)
    else:
        hotkey = normalize_hotkey(values.get("hotkey", ""))
        if not hotkey:
            raise SettingsValidationError("热键不能为空")
        try:
            hotkey_vk = vk_from_hotkey(hotkey)
        except ValueError as exc:
            raise SettingsValidationError(str(exc)) from exc
        hotkey_display = vk_to_display(hotkey_vk)

    mode = values.get("mode", "")
    if mode not in SUPPORTED_MODES:
        raise SettingsValidationError("模式无效")

    injection_policy = values.get("injection_policy", "")
    if injection_policy not in SUPPORTED_INJECTION_POLICIES:
        raise SettingsValidationError("注入策略无效")

    return replace(
        base_config,
        hotkey=hotkey,
        hotkey_vk=hotkey_vk,
        hotkey_display=hotkey_display,
        mode=mode,
        microphone_device=_parse_microphone_value(values.get("microphone_device", "__default__")),
        injection_policy=injection_policy,
        render_debounce_ms=_parse_int(values.get("render_debounce_ms", ""), "流式防抖", 0, 1000),
        overlay_render_fps=_parse_int(values.get("overlay_render_fps", ""), "显示帧率", 1, 120),
        overlay_font_size=_parse_int(values.get("overlay_font_size", ""), "字体大小", 10, 36),
        overlay_max_width=_parse_int(values.get("overlay_max_width", ""), "最大宽度", 320, 1200),
        overlay_opacity_percent=_parse_int(values.get("overlay_opacity_percent", ""), "透明度", 35, 100),
        overlay_bottom_offset=_parse_int(values.get("overlay_bottom_offset", ""), "底部偏移", 20, 500),
        overlay_animation_ms=_parse_int(values.get("overlay_animation_ms", ""), "动画时长", 0, 600),
    )


def _parse_int(raw: str, label: str, minimum: int, maximum: int) -> int:
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise SettingsValidationError(f"{label}必须是整数") from exc
    if value < minimum or value > maximum:
        raise SettingsValidationError(f"{label}必须在 {minimum} 到 {maximum} 之间")
    return value


def _parse_microphone_value(raw: str) -> int | str | None:
    value = raw.strip()
    if not value or value == "__default__":
        return None
    if value.startswith("index:"):
        return int(value.split(":", 1)[1])
    if value.startswith("name:"):
        name = value.split(":", 1)[1]
        return name or None
    return int(value) if value.isdigit() else value


class SettingsWindowController:
    WM_APP_SHOW = 0x8000 + 101
    WM_APP_CLOSE = 0x8000 + 102
    WM_APP_HOTKEY_CAPTURED = 0x8000 + 103

    def __init__(
        self,
        *,
        logger: logging.Logger,
        get_current_config: Callable[[], AgentConfig],
        on_save: Callable[[AgentConfig], None],
    ) -> None:
        self._logger = logger
        self._get_current_config = get_current_config
        self._on_save = on_save
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._config_snapshot = replace(get_current_config())
        self._hwnd: int | None = None
        self._class_name = f"DoubaoVoiceInputSettings_{id(self)}"
        self._controls: dict[str, int] = {}
        self._combo_values: dict[str, list[str]] = {}
        self._font_handle = None
        self._recorder: SingleKeyRecorder | None = None
        self._recording = False
        self._recorded_hotkey_vk = self._config_snapshot.effective_hotkey_vk()
        self._recorded_hotkey_display = self._config_snapshot.effective_hotkey_display()
        self._pending_hotkey_capture: tuple[int, str] | None = None

    def show(self, config: AgentConfig | None = None) -> None:
        with self._lock:
            if config is not None:
                self._config_snapshot = replace(config)
            else:
                self._config_snapshot = replace(self._get_current_config())
            self._recorded_hotkey_vk = self._config_snapshot.effective_hotkey_vk()
            self._recorded_hotkey_display = self._config_snapshot.effective_hotkey_display()
            if self._thread is None or not self._thread.is_alive():
                self._ready.clear()
                self._thread = threading.Thread(target=self._thread_main, name="doubao-settings", daemon=True)
                self._thread.start()
        self._ready.wait(timeout=3)
        if self._hwnd:
            import win32gui

            win32gui.PostMessage(self._hwnd, self.WM_APP_SHOW, 0, 0)

    def close(self) -> None:
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None
        if self._hwnd:
            import win32gui

            win32gui.PostMessage(self._hwnd, self.WM_APP_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _thread_main(self) -> None:
        import ctypes
        import win32api
        import win32con
        import win32gui

        wndclass = win32gui.WNDCLASS()
        wndclass.hInstance = win32api.GetModuleHandle(None)
        wndclass.lpszClassName = self._class_name
        wndclass.style = win32con.CS_HREDRAW | win32con.CS_VREDRAW
        wndclass.lpfnWndProc = self._wndproc
        wndclass.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        wndclass.hbrBackground = win32con.COLOR_WINDOW + 1
        try:
            win32gui.RegisterClass(wndclass)
        except win32gui.error:
            pass

        self._hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_TOOLWINDOW,
            self._class_name,
            "豆包语音输入 - 设置",
            win32con.WS_OVERLAPPED | win32con.WS_CAPTION | win32con.WS_SYSMENU | win32con.WS_MINIMIZEBOX,
            win32con.CW_USEDEFAULT,
            win32con.CW_USEDEFAULT,
            540,
            480,
            0,
            0,
            wndclass.hInstance,
            None,
        )
        self._font_handle = ctypes.windll.gdi32.GetStockObject(17)
        self._create_controls(self._hwnd)
        self._refresh_controls()
        win32gui.ShowWindow(self._hwnd, win32con.SW_SHOWNORMAL)
        win32gui.UpdateWindow(self._hwnd)
        self._ready.set()
        win32gui.PumpMessages()

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int):
        import win32api
        import win32con
        import win32gui

        if msg == win32con.WM_COMMAND:
            control_id = win32api.LOWORD(wparam)
            notify_code = win32api.HIWORD(wparam)
            if control_id == 1001 and notify_code == win32con.BN_CLICKED:
                self._save_from_controls(hwnd)
                return 0
            if control_id == 1002 and notify_code == win32con.BN_CLICKED:
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                return 0
            if control_id == 1003 and notify_code == win32con.BN_CLICKED:
                self._start_hotkey_recording(hwnd)
                return 0
        elif msg == self.WM_APP_SHOW:
            self._refresh_controls()
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
            win32gui.SetForegroundWindow(hwnd)
            return 0
        elif msg == self.WM_APP_HOTKEY_CAPTURED:
            payload = self._pending_hotkey_capture
            self._pending_hotkey_capture = None
            if payload is not None:
                self._recording = False
                self._recorded_hotkey_vk, self._recorded_hotkey_display = payload
                if self._recorder is not None:
                    self._recorder.stop()
                    self._recorder = None
                self._update_hotkey_label()
            return 0
        elif msg == self.WM_APP_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        elif msg == win32con.WM_CLOSE:
            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            return 0
        elif msg == win32con.WM_DESTROY:
            if self._recorder is not None:
                self._recorder.stop()
                self._recorder = None
            self._hwnd = None
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _create_controls(self, hwnd: int) -> None:
        import ctypes
        import win32api
        import win32con
        import win32gui

        label_x = 18
        field_x = 180
        row_y = 18
        row_step = 32
        label_width = 148
        field_width = 310
        combo_height = 220
        edit_height = 24

        hotkey_label = win32gui.CreateWindowEx(
            0,
            "STATIC",
            "热键",
            win32con.WS_CHILD | win32con.WS_VISIBLE,
            label_x,
            row_y + 4,
            label_width,
            edit_height,
            hwnd,
            0,
            0,
            None,
        )
        self._set_control_font(hotkey_label)
        hotkey_display = win32gui.CreateWindowEx(
            win32con.WS_EX_CLIENTEDGE,
            "EDIT",
            "",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.ES_READONLY,
            field_x,
            row_y,
            212,
            edit_height,
            hwnd,
            1098,
            win32api.GetModuleHandle(None),
            None,
        )
        self._set_control_font(hotkey_display)
        record_button = win32gui.CreateWindowEx(
            0,
            "BUTTON",
            "录制",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP,
            field_x + 224,
            row_y,
            86,
            edit_height,
            hwnd,
            1003,
            win32api.GetModuleHandle(None),
            None,
        )
        self._set_control_font(record_button)
        self._controls["hotkey_display"] = hotkey_display

        rows = [
            ("mode", "模式", "combo"),
            ("microphone_device", "麦克风", "combo"),
            ("render_debounce_ms", "流式防抖(ms)", "edit"),
            ("injection_policy", "注入策略", "combo"),
            ("overlay_render_fps", "显示帧率(FPS)", "edit"),
            ("overlay_font_size", "字体大小", "edit"),
            ("overlay_max_width", "最大宽度(px)", "edit"),
            ("overlay_opacity_percent", "透明度(%)", "edit"),
            ("overlay_bottom_offset", "底部偏移(px)", "edit"),
            ("overlay_animation_ms", "动画时长(ms)", "edit"),
        ]

        for index, (name, label, control_type) in enumerate(rows):
            y = row_y + (index + 1) * row_step
            label_handle = win32gui.CreateWindowEx(
                0,
                "STATIC",
                label,
                win32con.WS_CHILD | win32con.WS_VISIBLE,
                label_x,
                y + 4,
                label_width,
                edit_height,
                hwnd,
                0,
                0,
                None,
            )
            self._set_control_font(label_handle)
            if control_type == "combo":
                style = win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP | win32con.CBS_DROPDOWNLIST | win32con.WS_VSCROLL
                handle = win32gui.CreateWindowEx(
                    win32con.WS_EX_CLIENTEDGE,
                    "COMBOBOX",
                    "",
                    style,
                    field_x,
                    y,
                    field_width,
                    combo_height,
                    hwnd,
                    1100 + index,
                    win32api.GetModuleHandle(None),
                    None,
                )
            else:
                handle = win32gui.CreateWindowEx(
                    win32con.WS_EX_CLIENTEDGE,
                    "EDIT",
                    "",
                    win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP | win32con.ES_AUTOHSCROLL,
                    field_x,
                    y,
                    field_width,
                    edit_height,
                    hwnd,
                    1100 + index,
                    win32api.GetModuleHandle(None),
                    None,
                )
            self._set_control_font(handle)
            self._controls[name] = handle

        hint_handle = win32gui.CreateWindowEx(
            0,
            "STATIC",
            "热键支持录制任意单键；终端类窗口建议保留智能兼容注入。",
            win32con.WS_CHILD | win32con.WS_VISIBLE,
            label_x,
            row_y + (len(rows) + 1) * row_step + 4,
            480,
            36,
            hwnd,
            0,
            0,
            None,
        )
        self._set_control_font(hint_handle)

        save_button = win32gui.CreateWindowEx(
            0,
            "BUTTON",
            "保存",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP | win32con.BS_DEFPUSHBUTTON,
            300,
            424,
            90,
            28,
            hwnd,
            1001,
            win32api.GetModuleHandle(None),
            None,
        )
        cancel_button = win32gui.CreateWindowEx(
            0,
            "BUTTON",
            "取消",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP,
            400,
            424,
            90,
            28,
            hwnd,
            1002,
            win32api.GetModuleHandle(None),
            None,
        )
        self._set_control_font(save_button)
        self._set_control_font(cancel_button)

    def _refresh_controls(self) -> None:
        import win32con
        import win32gui

        with self._lock:
            config = replace(self._config_snapshot)

        self._recorded_hotkey_vk = config.effective_hotkey_vk()
        self._recorded_hotkey_display = config.effective_hotkey_display()
        self._update_hotkey_label()
        self._set_combo_items("mode", MODE_OPTIONS, config.mode)
        self._set_combo_items("microphone_device", list_microphone_choices(config.microphone_device), _microphone_choice_value(config.microphone_device))
        self._set_combo_items("injection_policy", INJECTION_POLICY_OPTIONS, config.injection_policy)

        win32gui.SetWindowText(self._controls["render_debounce_ms"], str(config.render_debounce_ms))
        win32gui.SetWindowText(self._controls["overlay_render_fps"], str(config.overlay_render_fps))
        win32gui.SetWindowText(self._controls["overlay_font_size"], str(config.overlay_font_size))
        win32gui.SetWindowText(self._controls["overlay_max_width"], str(config.overlay_max_width))
        win32gui.SetWindowText(self._controls["overlay_opacity_percent"], str(config.overlay_opacity_percent))
        win32gui.SetWindowText(self._controls["overlay_bottom_offset"], str(config.overlay_bottom_offset))
        win32gui.SetWindowText(self._controls["overlay_animation_ms"], str(config.overlay_animation_ms))

    def _set_combo_items(self, name: str, items: list[tuple[str, str]], selected_value: str) -> None:
        import win32con
        import win32gui

        combo_handle = self._controls[name]
        self._combo_values[name] = [value for value, _ in items]
        win32gui.SendMessage(combo_handle, win32con.CB_RESETCONTENT, 0, 0)
        selected_index = 0
        for index, (value, label) in enumerate(items):
            win32gui.SendMessage(combo_handle, win32con.CB_ADDSTRING, 0, label)
            if value == selected_value:
                selected_index = index
        win32gui.SendMessage(combo_handle, win32con.CB_SETCURSEL, selected_index, 0)

    def _save_from_controls(self, hwnd: int) -> None:
        import win32con
        import win32gui

        with self._lock:
            base_config = replace(self._config_snapshot)
        try:
            next_config = build_config_from_settings_values(base_config, self._collect_control_values())
        except SettingsValidationError as exc:
            win32gui.MessageBox(hwnd, str(exc), "设置无效", win32con.MB_OK | win32con.MB_ICONERROR)
            return

        self._logger.info("settings_window_saved config=%s", next_config)
        self._on_save(next_config)
        with self._lock:
            self._config_snapshot = replace(next_config)
        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)

    def _collect_control_values(self) -> dict[str, str]:
        import win32con
        import win32gui

        values = {
            "hotkey_vk": str(self._recorded_hotkey_vk),
            "hotkey_display": self._recorded_hotkey_display,
            "render_debounce_ms": win32gui.GetWindowText(self._controls["render_debounce_ms"]),
            "overlay_render_fps": win32gui.GetWindowText(self._controls["overlay_render_fps"]),
            "overlay_font_size": win32gui.GetWindowText(self._controls["overlay_font_size"]),
            "overlay_max_width": win32gui.GetWindowText(self._controls["overlay_max_width"]),
            "overlay_opacity_percent": win32gui.GetWindowText(self._controls["overlay_opacity_percent"]),
            "overlay_bottom_offset": win32gui.GetWindowText(self._controls["overlay_bottom_offset"]),
            "overlay_animation_ms": win32gui.GetWindowText(self._controls["overlay_animation_ms"]),
        }

        for name in ("mode", "microphone_device", "injection_policy"):
            combo_handle = self._controls[name]
            index = int(win32gui.SendMessage(combo_handle, win32con.CB_GETCURSEL, 0, 0))
            values[name] = self._combo_values[name][index] if index >= 0 else ""
        return values

    def _set_control_font(self, handle: int) -> None:
        import ctypes

        if self._font_handle:
            ctypes.windll.user32.SendMessageW(handle, 0x0030, self._font_handle, 1)

    def _start_hotkey_recording(self, hwnd: int) -> None:
        import win32gui

        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None

        self._recording = True
        win32gui.SetWindowText(self._controls["hotkey_display"], "请按任意单键…")

        def on_key(vk: int, display: str) -> None:
            self._pending_hotkey_capture = (vk, display)
            win32gui.PostMessage(hwnd, self.WM_APP_HOTKEY_CAPTURED, vk, 0)

        self._recorder = SingleKeyRecorder(on_key=on_key)
        self._recorder.start()

    def _update_hotkey_label(self) -> None:
        import win32gui

        label = self._recorded_hotkey_display or vk_to_display(self._recorded_hotkey_vk)
        win32gui.SetWindowText(self._controls["hotkey_display"], label)


def _microphone_choice_value(value: int | str | None) -> str:
    if value is None:
        return "__default__"
    if isinstance(value, int):
        return f"index:{value}"
    return f"name:{value}"
