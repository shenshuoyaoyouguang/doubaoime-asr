from __future__ import annotations

from dataclasses import replace
import logging
import threading
from typing import Callable

from .config import AgentConfig
from .settings_mapping import (
    build_config_from_settings_values,
    list_microphone_choices,
    settings_values_from_config,
)
from .settings_validation import (
    SettingsValidationError,
    inline_error_message,
    preview_banner_message,
    restore_banner_message,
    validation_banner_message,
)
from .settings_schema import (
    CAPTURE_OUTPUT_OPTIONS,
    COMBO_FIELD_NAMES,
    EDIT_FIELD_NAMES,
    FIELD_HELP_TEXT,
    FIELD_LABELS,
    FIELD_TO_PAGE,
    FINAL_COMMIT_SOURCE_OPTIONS,
    INJECTION_POLICY_OPTIONS,
    MODE_OPTIONS,
    PAGE_DESCRIPTIONS,
    PAGE_FIELDS,
    PAGE_LABELS,
    PAGE_ORDER,
    POLISH_MODE_OPTIONS,
    STREAMING_TEXT_MODE_OPTIONS,
    WARMUP_OPTIONS,
    page_footer_hint,
    page_heading,
    should_show_ollama_fields,
    visible_fields_for_page,
)
from .win_keyboard_hook import SingleKeyRecorder, VK_RCONTROL


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
        on_preview: Callable[[AgentConfig], None] | None = None,
    ) -> None:
        self._logger = logger
        self._get_current_config = get_current_config
        self._on_save = on_save
        self._on_preview = on_preview
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._config_snapshot = replace(get_current_config())
        self._hwnd: int | None = None
        self._class_name = f"DoubaoVoiceInputSettings_{id(self)}"
        self._controls: dict[str, int] = {}
        self._control_ids: dict[str, int] = {}
        self._labels: dict[str, int] = {}
        self._help_labels: dict[str, int] = {}
        self._error_labels: dict[str, int] = {}
        self._field_errors: dict[str, str] = {}
        self._combo_values: dict[str, list[str]] = {}
        self._page_buttons: dict[str, int] = {}
        self._font_handle = None
        self._recorder: SingleKeyRecorder | None = None
        self._recording = False
        self._recorded_hotkey_vk = self._config_snapshot.effective_hotkey_vk()
        self._recorded_hotkey_display = self._config_snapshot.effective_hotkey_display()
        self._pending_hotkey_capture: tuple[int, str] | None = None
        self._active_page = "general"
        self._header_title_handle: int | None = None
        self._header_subtitle_handle: int | None = None
        self._page_title_handle: int | None = None
        self._page_summary_handle: int | None = None
        self._page_hint_handle: int | None = None
        self._banner_handle: int | None = None
        self._record_button_handle: int | None = None
        self._save_button_handle: int | None = None
        self._cancel_button_handle: int | None = None
        self._defaults_button_handle: int | None = None
        self._preview_button_handle: int | None = None

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

            try:
                win32gui.PostMessage(self._hwnd, self.WM_APP_SHOW, 0, 0)
            except Exception:
                self._logger.exception("settings_window_show_failed")

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
            640,
            620,
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
            if control_id == 1004 and notify_code == win32con.BN_CLICKED:
                self._restore_active_page_defaults()
                return 0
            if control_id == 1005 and notify_code == win32con.BN_CLICKED:
                self._preview_from_controls(hwnd)
                return 0
            for index, page_name in enumerate(PAGE_ORDER):
                if control_id == 2000 + index and notify_code == win32con.BN_CLICKED:
                    self._activate_page(page_name)
                    return 0
            if control_id == self._control_ids.get("polish_mode") and notify_code == win32con.CBN_SELCHANGE:
                self._activate_page(self._active_page)
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
        import win32api
        import win32con
        import win32gui

        self._header_title_handle = win32gui.CreateWindowEx(
            0,
            "STATIC",
            "设置",
            win32con.WS_CHILD | win32con.WS_VISIBLE,
            24,
            18,
            220,
            24,
            hwnd,
            0,
            0,
            None,
        )
        self._set_control_font(self._header_title_handle)
        self._header_subtitle_handle = win32gui.CreateWindowEx(
            0,
            "STATIC",
            "常用设置在前，高级选项按分区查看。保存后才会真正生效。",
            win32con.WS_CHILD | win32con.WS_VISIBLE,
            24,
            44,
            560,
            20,
            hwnd,
            0,
            0,
            None,
        )
        self._set_control_font(self._header_subtitle_handle)

        button_x = 24
        button_width = 136
        for index, page_name in enumerate(PAGE_ORDER):
            style = win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP | win32con.BS_AUTORADIOBUTTON | win32con.BS_PUSHLIKE
            if index == 0:
                style |= win32con.WS_GROUP
            handle = win32gui.CreateWindowEx(
                0,
                "BUTTON",
                PAGE_LABELS[page_name],
                style,
                button_x + index * (button_width + 8),
                78,
                button_width,
                28,
                hwnd,
                2000 + index,
                win32api.GetModuleHandle(None),
                None,
            )
            self._set_control_font(handle)
            self._page_buttons[page_name] = handle

        self._page_title_handle = win32gui.CreateWindowEx(
            0,
            "STATIC",
            "",
            win32con.WS_CHILD | win32con.WS_VISIBLE,
            24,
            124,
            220,
            22,
            hwnd,
            0,
            0,
            None,
        )
        self._set_control_font(self._page_title_handle)
        self._page_summary_handle = win32gui.CreateWindowEx(
            0,
            "STATIC",
            "",
            win32con.WS_CHILD | win32con.WS_VISIBLE,
            24,
            148,
            580,
            34,
            hwnd,
            0,
            0,
            None,
        )
        self._set_control_font(self._page_summary_handle)
        self._page_hint_handle = win32gui.CreateWindowEx(
            0,
            "STATIC",
            "",
            win32con.WS_CHILD | win32con.WS_VISIBLE,
            24,
            520,
            360,
            18,
            hwnd,
            0,
            0,
            None,
        )
        self._set_control_font(self._page_hint_handle)
        self._banner_handle = win32gui.CreateWindowEx(
            0,
            "STATIC",
            "",
            win32con.WS_CHILD | win32con.WS_VISIBLE,
            24,
            112,
            580,
            18,
            hwnd,
            0,
            0,
            None,
        )
        self._set_control_font(self._banner_handle)

        for field_name in FIELD_TO_PAGE:
            label_handle = win32gui.CreateWindowEx(
                0,
                "STATIC",
                FIELD_LABELS[field_name],
                win32con.WS_CHILD,
                0,
                0,
                0,
                0,
                hwnd,
                0,
                0,
                None,
            )
            self._set_control_font(label_handle)
            self._labels[field_name] = label_handle

            if field_name == "hotkey_display":
                handle = win32gui.CreateWindowEx(
                    win32con.WS_EX_CLIENTEDGE,
                    "EDIT",
                    "",
                    win32con.WS_CHILD | win32con.ES_READONLY,
                    0,
                    0,
                    0,
                    0,
                    hwnd,
                    1098,
                    win32api.GetModuleHandle(None),
                    None,
                )
                self._record_button_handle = win32gui.CreateWindowEx(
                    0,
                    "BUTTON",
                    "录制热键",
                    win32con.WS_CHILD | win32con.WS_TABSTOP,
                    0,
                    0,
                    0,
                    0,
                    hwnd,
                    1003,
                    win32api.GetModuleHandle(None),
                    None,
                )
                self._set_control_font(self._record_button_handle)
            elif field_name in COMBO_FIELD_NAMES:
                handle = win32gui.CreateWindowEx(
                    win32con.WS_EX_CLIENTEDGE,
                    "COMBOBOX",
                    "",
                    win32con.WS_CHILD | win32con.WS_TABSTOP | win32con.CBS_DROPDOWNLIST | win32con.WS_VSCROLL,
                    0,
                    0,
                    0,
                    0,
                    hwnd,
                    1100 + len(self._control_ids),
                    win32api.GetModuleHandle(None),
                    None,
                )
            else:
                handle = win32gui.CreateWindowEx(
                    win32con.WS_EX_CLIENTEDGE,
                    "EDIT",
                    "",
                    win32con.WS_CHILD | win32con.WS_TABSTOP | win32con.ES_AUTOHSCROLL,
                    0,
                    0,
                    0,
                    0,
                    hwnd,
                    1100 + len(self._control_ids),
                    win32api.GetModuleHandle(None),
                    None,
                )
            self._set_control_font(handle)
            if field_name == "hotkey_display":
                control_id = 1098
            else:
                control_id = 1100 + len(self._control_ids)
            self._controls[field_name] = handle
            self._control_ids[field_name] = control_id

            help_handle = win32gui.CreateWindowEx(
                0,
                "STATIC",
                FIELD_HELP_TEXT.get(field_name, ""),
                win32con.WS_CHILD,
                0,
                0,
                0,
                0,
                hwnd,
                0,
                0,
                None,
            )
            self._set_control_font(help_handle)
            self._help_labels[field_name] = help_handle
            error_handle = win32gui.CreateWindowEx(
                0,
                "STATIC",
                "",
                win32con.WS_CHILD,
                0,
                0,
                0,
                0,
                hwnd,
                0,
                0,
                None,
            )
            self._set_control_font(error_handle)
            self._error_labels[field_name] = error_handle

        self._defaults_button_handle = win32gui.CreateWindowEx(
            0,
            "BUTTON",
            "恢复本页默认",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP,
            24,
            540,
            116,
            28,
            hwnd,
            1004,
            win32api.GetModuleHandle(None),
            None,
        )
        self._preview_button_handle = win32gui.CreateWindowEx(
            0,
            "BUTTON",
            "预览浮层",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP,
            152,
            540,
            104,
            28,
            hwnd,
            1005,
            win32api.GetModuleHandle(None),
            None,
        )
        self._cancel_button_handle = win32gui.CreateWindowEx(
            0,
            "BUTTON",
            "取消",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP,
            418,
            540,
            90,
            28,
            hwnd,
            1002,
            win32api.GetModuleHandle(None),
            None,
        )
        self._save_button_handle = win32gui.CreateWindowEx(
            0,
            "BUTTON",
            "保存",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_TABSTOP | win32con.BS_DEFPUSHBUTTON,
            518,
            540,
            90,
            28,
            hwnd,
            1001,
            win32api.GetModuleHandle(None),
            None,
        )
        for handle in (
            self._defaults_button_handle,
            self._preview_button_handle,
            self._cancel_button_handle,
            self._save_button_handle,
        ):
            self._set_control_font(handle)

    def _refresh_controls(self) -> None:
        with self._lock:
            config = replace(self._config_snapshot)

        self._set_banner_text("")
        self._clear_field_errors()
        for field_name in FIELD_TO_PAGE:
            self._set_field_value_from_config(field_name, config)
        self._activate_page(self._active_page)

    def _set_field_value_from_config(self, field_name: str, config: AgentConfig) -> None:
        import win32gui

        values = settings_values_from_config(config)
        if field_name == "hotkey_display":
            self._recorded_hotkey_vk = config.effective_hotkey_vk()
            self._recorded_hotkey_display = config.effective_hotkey_display()
            self._update_hotkey_label()
            return
        if field_name == "mode":
            self._set_combo_items("mode", MODE_OPTIONS, values["mode"])
            return
        if field_name == "microphone_device":
            self._set_combo_items(
                "microphone_device",
                list_microphone_choices(config.microphone_device),
                values["microphone_device"],
            )
            return
        if field_name == "streaming_text_mode":
            self._set_combo_items("streaming_text_mode", STREAMING_TEXT_MODE_OPTIONS, values["streaming_text_mode"])
            return
        if field_name == "injection_policy":
            self._set_combo_items("injection_policy", INJECTION_POLICY_OPTIONS, values["injection_policy"])
            return
        if field_name == "capture_output_policy":
            self._set_combo_items("capture_output_policy", CAPTURE_OUTPUT_OPTIONS, values["capture_output_policy"])
            return
        if field_name == "polish_mode":
            self._set_combo_items("polish_mode", POLISH_MODE_OPTIONS, values["polish_mode"])
            return
        if field_name == "final_commit_source":
            self._set_combo_items("final_commit_source", FINAL_COMMIT_SOURCE_OPTIONS, values["final_commit_source"])
            return
        if field_name == "ollama_warmup_enabled":
            self._set_combo_items("ollama_warmup_enabled", WARMUP_OPTIONS, values["ollama_warmup_enabled"])
            return
        win32gui.SetWindowText(self._controls[field_name], values[field_name])

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

    def _activate_page(self, page_name: str) -> None:
        import win32con
        import win32gui

        if page_name not in PAGE_ORDER:
            return
        self._active_page = page_name
        for current_page, handle in self._page_buttons.items():
            checked = win32con.BST_CHECKED if current_page == page_name else win32con.BST_UNCHECKED
            win32gui.SendMessage(handle, win32con.BM_SETCHECK, checked, 0)
        if self._page_title_handle is not None:
            win32gui.SetWindowText(self._page_title_handle, page_heading(page_name))
        if self._page_summary_handle is not None:
            win32gui.SetWindowText(self._page_summary_handle, PAGE_DESCRIPTIONS[page_name])
        if self._page_hint_handle is not None:
            win32gui.SetWindowText(self._page_hint_handle, page_footer_hint(page_name, self._current_polish_mode()))
        self._layout_active_page()
        self._update_action_buttons()

    def _layout_active_page(self) -> None:
        import win32con
        import win32gui

        label_x = 28
        label_width = 170
        field_x = 212
        field_width = 372
        y = 198
        row_gap = 54
        edit_height = 24
        combo_height = 220

        for field_name in FIELD_TO_PAGE:
            win32gui.ShowWindow(self._labels[field_name], win32con.SW_HIDE)
            win32gui.ShowWindow(self._controls[field_name], win32con.SW_HIDE)
            win32gui.ShowWindow(self._help_labels[field_name], win32con.SW_HIDE)
            win32gui.ShowWindow(self._error_labels[field_name], win32con.SW_HIDE)
        if self._record_button_handle is not None:
            win32gui.ShowWindow(self._record_button_handle, win32con.SW_HIDE)

        visible_fields = visible_fields_for_page(self._active_page, self._current_polish_mode())
        for field_name in visible_fields:
            label_handle = self._labels[field_name]
            control_handle = self._controls[field_name]
            help_handle = self._help_labels[field_name]
            win32gui.MoveWindow(label_handle, label_x, y + 4, label_width, edit_height, True)
            win32gui.ShowWindow(label_handle, win32con.SW_SHOW)

            if field_name == "hotkey_display":
                win32gui.MoveWindow(control_handle, field_x, y, 248, edit_height, True)
                win32gui.ShowWindow(control_handle, win32con.SW_SHOW)
                if self._record_button_handle is not None:
                    win32gui.MoveWindow(self._record_button_handle, field_x + 260, y, 112, edit_height, True)
                    win32gui.ShowWindow(self._record_button_handle, win32con.SW_SHOW)
            elif field_name in COMBO_FIELD_NAMES:
                win32gui.MoveWindow(control_handle, field_x, y, field_width, combo_height, True)
                win32gui.ShowWindow(control_handle, win32con.SW_SHOW)
            else:
                win32gui.MoveWindow(control_handle, field_x, y, field_width, edit_height, True)
                win32gui.ShowWindow(control_handle, win32con.SW_SHOW)

            help_text = FIELD_HELP_TEXT.get(field_name, "")
            error_text = self._field_errors.get(field_name, "")
            if error_text:
                win32gui.MoveWindow(help_handle, field_x, y + 26, field_width, 18, True)
                win32gui.ShowWindow(help_handle, win32con.SW_HIDE)
                error_handle = self._error_labels[field_name]
                win32gui.SetWindowText(error_handle, error_text)
                win32gui.MoveWindow(error_handle, field_x, y + 26, field_width, 18, True)
                win32gui.ShowWindow(error_handle, win32con.SW_SHOW)
            elif help_text:
                win32gui.MoveWindow(help_handle, field_x, y + 26, field_width, 18, True)
                win32gui.ShowWindow(help_handle, win32con.SW_SHOW)
            y += row_gap

    def _update_action_buttons(self) -> None:
        import win32con
        import win32gui

        preview_enabled = self._active_page == "overlay" and self._on_preview is not None
        if self._preview_button_handle is not None:
            win32gui.EnableWindow(self._preview_button_handle, preview_enabled)
            win32gui.ShowWindow(
                self._preview_button_handle,
                win32con.SW_SHOW if self._active_page == "overlay" else win32con.SW_HIDE,
            )

    def _current_polish_mode(self) -> str:
        import win32con
        import win32gui

        combo_handle = self._controls.get("polish_mode")
        if combo_handle is None:
            return self._config_snapshot.polish_mode
        values = self._combo_values.get("polish_mode")
        if not values:
            return self._config_snapshot.polish_mode
        index = int(win32gui.SendMessage(combo_handle, win32con.CB_GETCURSEL, 0, 0))
        if 0 <= index < len(values):
            return values[index]
        return self._config_snapshot.polish_mode

    def _restore_active_page_defaults(self) -> None:
        defaults = AgentConfig.default()
        self._clear_field_errors()
        for field_name in PAGE_FIELDS[self._active_page]:
            self._set_field_value_from_config(field_name, defaults)
        self._set_banner_text(restore_banner_message(self._active_page))
        self._activate_page(self._active_page)

    def _save_from_controls(self, hwnd: int) -> None:
        import win32con
        import win32gui

        try:
            next_config = self._build_config_from_controls()
        except SettingsValidationError as exc:
            self._handle_validation_error(hwnd, exc)
            return

        self._logger.info("settings_window_saved config=%s", next_config)
        self._set_banner_text("设置已提交，保存后会立即生效。")
        self._on_save(next_config)
        with self._lock:
            self._config_snapshot = replace(next_config)
        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)

    def _preview_from_controls(self, hwnd: int) -> None:
        import win32con
        import win32gui

        if self._on_preview is None:
            return
        try:
            preview_config = self._build_config_from_controls()
        except SettingsValidationError as exc:
            self._handle_validation_error(hwnd, exc)
            return
        try:
            self._set_banner_text(preview_banner_message())
            self._on_preview(preview_config)
        except Exception:
            self._logger.exception("settings_preview_dispatch_failed")
            self._set_banner_text("预览失败，请查看日志。")
            win32gui.MessageBox(hwnd, "预览失败，请查看日志。", "预览失败", win32con.MB_OK | win32con.MB_ICONERROR)

    def _build_config_from_controls(self) -> AgentConfig:
        with self._lock:
            base_config = replace(self._config_snapshot)
        return build_config_from_settings_values(base_config, self._collect_control_values())

    def _handle_validation_error(self, hwnd: int, exc: SettingsValidationError) -> None:
        import win32con
        import win32gui

        self._clear_field_errors()
        self._set_field_error(exc)
        self._set_banner_text(validation_banner_message(exc))
        self._focus_field(exc.field_name)
        win32gui.MessageBox(hwnd, str(exc), "设置无效", win32con.MB_OK | win32con.MB_ICONERROR)

    def _focus_field(self, field_name: str | None) -> None:
        import win32gui

        if not field_name:
            return
        focus_field = "hotkey_display" if field_name == "hotkey" else field_name
        page_name = FIELD_TO_PAGE.get(focus_field)
        if page_name is not None and page_name != self._active_page:
            self._activate_page(page_name)
        handle = self._controls.get(focus_field)
        if handle:
            win32gui.SetFocus(handle)

    def _set_banner_text(self, text: str) -> None:
        import win32gui

        if self._banner_handle is not None:
            win32gui.SetWindowText(self._banner_handle, text)

    def _clear_field_errors(self) -> None:
        self._field_errors.clear()

    def _set_field_error(self, exc: SettingsValidationError) -> None:
        if exc.field_name:
            self._field_errors[exc.field_name] = inline_error_message(exc)
            self._activate_page(FIELD_TO_PAGE.get(exc.field_name, self._active_page))

    def _collect_control_values(self) -> dict[str, str]:
        import win32con
        import win32gui

        values = {
            "hotkey_vk": str(self._recorded_hotkey_vk),
            "hotkey_display": self._recorded_hotkey_display,
        }
        for field_name in EDIT_FIELD_NAMES:
            values[field_name] = win32gui.GetWindowText(self._controls[field_name])

        for field_name in COMBO_FIELD_NAMES:
            combo_handle = self._controls[field_name]
            index = int(win32gui.SendMessage(combo_handle, win32con.CB_GETCURSEL, 0, 0))
            values[field_name] = self._combo_values[field_name][index] if index >= 0 else ""
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
        win32gui.SetWindowText(self._controls["hotkey_display"], "请按单键（支持 Right Ctrl）…")

        def on_key(vk: int, display: str) -> None:
            self._pending_hotkey_capture = (vk, display)
            win32gui.PostMessage(hwnd, self.WM_APP_HOTKEY_CAPTURED, vk, 0)

        self._recorder = SingleKeyRecorder(on_key=on_key, allowed_modifier_vks={VK_RCONTROL})
        self._recorder.start()

    def _update_hotkey_label(self) -> None:
        import win32gui

        label = self._recorded_hotkey_display or vk_to_display(self._recorded_hotkey_vk)
        win32gui.SetWindowText(self._controls["hotkey_display"], label)
