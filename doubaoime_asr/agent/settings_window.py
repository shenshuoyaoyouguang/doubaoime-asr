from __future__ import annotations

from dataclasses import replace
import logging
import threading
from typing import Callable, Mapping

from .config import (
    AgentConfig,
    CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT,
    CAPTURE_OUTPUT_POLICY_OFF,
    DEFAULT_OLLAMA_BASE_URL,
    INJECTION_POLICY_DIRECT_ONLY,
    INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
    POLISH_MODE_LIGHT,
    POLISH_MODE_OFF,
    POLISH_MODE_OLLAMA,
    SUPPORTED_CAPTURE_OUTPUT_POLICIES,
    SUPPORTED_INJECTION_POLICIES,
    SUPPORTED_MODES,
    SUPPORTED_POLISH_MODES,
)
from .win_hotkey import normalize_hotkey, vk_from_hotkey, vk_to_display, vk_to_hotkey
from .win_keyboard_hook import SingleKeyRecorder, VK_RCONTROL


MODE_OPTIONS: list[tuple[str, str]] = [
    ("inject", "自动上屏"),
    ("recognize", "仅识别（不自动上屏）"),
]

INJECTION_POLICY_OPTIONS: list[tuple[str, str]] = [
    (INJECTION_POLICY_DIRECT_THEN_CLIPBOARD, "智能兼容（默认，失败时用剪贴板）"),
    (INJECTION_POLICY_DIRECT_ONLY, "仅直接输入（绝不动剪贴板）"),
]

CAPTURE_OUTPUT_OPTIONS: list[tuple[str, str]] = [
    (CAPTURE_OUTPUT_POLICY_OFF, "保持现状"),
    (CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT, "录音时静音系统输出"),
]

POLISH_MODE_OPTIONS: list[tuple[str, str]] = [
    (POLISH_MODE_LIGHT, "轻量整理（推荐）"),
    (POLISH_MODE_OFF, "关闭"),
    (POLISH_MODE_OLLAMA, "Ollama 本地润色（较慢）"),
]

WARMUP_OPTIONS: list[tuple[str, str]] = [
    ("true", "开启预热（推荐）"),
    ("false", "按需加载"),
]

PAGE_ORDER: tuple[str, ...] = ("general", "behavior", "polish", "overlay")
PAGE_LABELS: dict[str, str] = {
    "general": "常用",
    "behavior": "输入行为",
    "polish": "润色",
    "overlay": "浮层显示",
}
PAGE_DESCRIPTIONS: dict[str, str] = {
    "general": "先完成热键、模式和麦克风，常用设置都在这里。",
    "behavior": "控制识别过程中的上屏策略与录音体验。",
    "polish": "决定最终文本如何整理；仅在需要时展开 Ollama 高级项。",
    "overlay": "调节悬浮显示样式，并预览当前视觉效果。",
}
PAGE_FIELDS: dict[str, tuple[str, ...]] = {
    "general": ("hotkey_display", "mode", "microphone_device"),
    "behavior": ("render_debounce_ms", "injection_policy", "capture_output_policy"),
    "polish": (
        "polish_mode",
        "ollama_base_url",
        "ollama_model",
        "polish_timeout_ms",
        "ollama_warmup_enabled",
    ),
    "overlay": (
        "overlay_render_fps",
        "overlay_font_size",
        "overlay_max_width",
        "overlay_opacity_percent",
        "overlay_bottom_offset",
        "overlay_animation_ms",
    ),
}
FIELD_TO_PAGE: dict[str, str] = {
    field_name: page_name
    for page_name, field_names in PAGE_FIELDS.items()
    for field_name in field_names
}
FIELD_LABELS: dict[str, str] = {
    "hotkey_display": "热键",
    "mode": "模式",
    "microphone_device": "麦克风",
    "render_debounce_ms": "流式防抖(ms)",
    "injection_policy": "注入策略",
    "capture_output_policy": "系统输出处理",
    "polish_mode": "最终润色",
    "ollama_base_url": "Ollama 地址",
    "ollama_model": "Ollama 模型",
    "polish_timeout_ms": "润色超时(ms)",
    "ollama_warmup_enabled": "模型预热",
    "overlay_render_fps": "显示帧率(FPS)",
    "overlay_font_size": "字体大小",
    "overlay_max_width": "最大宽度(px)",
    "overlay_opacity_percent": "透明度(%)",
    "overlay_bottom_offset": "底部偏移(px)",
    "overlay_animation_ms": "动画时长(ms)",
}
FIELD_HELP_TEXT: dict[str, str] = {
    "hotkey_display": "支持录制单键，包含 Right Ctrl。",
    "mode": "自动上屏适合聊天；仅识别适合先确认文本。",
    "microphone_device": "建议先使用系统默认，异常时再手动切换。",
    "render_debounce_ms": "数值越小越灵敏，过低可能导致显示闪动。",
    "injection_policy": "兼容模式失败时会自动回退到剪贴板。",
    "capture_output_policy": "如果录音时担心系统声音干扰，可以临时静音输出。",
    "polish_mode": "轻量整理适合大多数场景；本地模型更强但更慢。",
    "ollama_base_url": "通常保持 http://localhost:11434 即可。",
    "ollama_model": "为空时仅在唯一模型场景下自动探测。",
    "polish_timeout_ms": "超时后会自动回退原始识别结果。",
    "ollama_warmup_enabled": "开启后首次使用更快，但会提前占用资源。",
    "overlay_render_fps": "调高更顺滑，调低更省资源。",
    "overlay_font_size": "建议 12–18，适合多数桌面分辨率。",
    "overlay_max_width": "宽度过大可能影响阅读，建议保持在 620 左右。",
    "overlay_opacity_percent": "透明度越高越清晰，也更容易挡住底下内容。",
    "overlay_bottom_offset": "决定浮层离屏幕底部的距离。",
    "overlay_animation_ms": "较短更利落，较长更平滑。",
}
OLLAMA_FIELD_NAMES: tuple[str, ...] = (
    "ollama_base_url",
    "ollama_model",
    "polish_timeout_ms",
    "ollama_warmup_enabled",
)
COMBO_FIELD_NAMES: tuple[str, ...] = (
    "mode",
    "microphone_device",
    "injection_policy",
    "capture_output_policy",
    "polish_mode",
    "ollama_warmup_enabled",
)
EDIT_FIELD_NAMES: tuple[str, ...] = (
    "render_debounce_ms",
    "ollama_base_url",
    "ollama_model",
    "polish_timeout_ms",
    "overlay_render_fps",
    "overlay_font_size",
    "overlay_max_width",
    "overlay_opacity_percent",
    "overlay_bottom_offset",
    "overlay_animation_ms",
)


class SettingsValidationError(ValueError):
    """设置值非法。"""

    def __init__(self, message: str, *, field_name: str | None = None) -> None:
        super().__init__(message)
        self.field_name = field_name


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


def settings_values_from_config(config: AgentConfig) -> dict[str, str]:
    return {
        "hotkey_vk": str(config.effective_hotkey_vk()),
        "hotkey_display": config.effective_hotkey_display(),
        "mode": config.mode,
        "microphone_device": _microphone_choice_value(config.microphone_device),
        "injection_policy": config.injection_policy,
        "capture_output_policy": config.capture_output_policy,
        "render_debounce_ms": str(config.render_debounce_ms),
        "polish_mode": config.polish_mode,
        "ollama_base_url": config.ollama_base_url,
        "ollama_model": config.ollama_model,
        "polish_timeout_ms": str(config.polish_timeout_ms),
        "ollama_warmup_enabled": "true" if config.ollama_warmup_enabled else "false",
        "overlay_render_fps": str(config.overlay_render_fps),
        "overlay_font_size": str(config.overlay_font_size),
        "overlay_max_width": str(config.overlay_max_width),
        "overlay_opacity_percent": str(config.overlay_opacity_percent),
        "overlay_bottom_offset": str(config.overlay_bottom_offset),
        "overlay_animation_ms": str(config.overlay_animation_ms),
    }


def should_show_ollama_fields(polish_mode: str) -> bool:
    return polish_mode == POLISH_MODE_OLLAMA


def visible_fields_for_page(page_name: str, polish_mode: str) -> list[str]:
    fields = list(PAGE_FIELDS.get(page_name, ()))
    if page_name == "polish" and not should_show_ollama_fields(polish_mode):
        return [field_name for field_name in fields if field_name not in OLLAMA_FIELD_NAMES]
    return fields


def page_heading(page_name: str) -> str:
    if page_name not in PAGE_ORDER:
        return "设置"
    return f"{PAGE_ORDER.index(page_name) + 1}/{len(PAGE_ORDER)} · {PAGE_LABELS[page_name]}"


def page_footer_hint(page_name: str, polish_mode: str) -> str:
    if page_name == "general":
        return "提示：先完成热键和模式，再按需要调整后面的高级设置。"
    if page_name == "behavior":
        return "提示：行为设置会影响后续录音与上屏体验。"
    if page_name == "polish":
        if should_show_ollama_fields(polish_mode):
            return "提示：本地模型只处理最终文本；修改地址或模型后记得保存。"
        return "提示：轻量整理适合多数场景；选择 Ollama 后才会显示高级配置。"
    if page_name == "overlay":
        return "提示：可以先预览浮层，确认样式后再保存；预览不会写入配置。"
    return ""


def validation_banner_message(exc: SettingsValidationError) -> str:
    field_label = FIELD_LABELS.get(exc.field_name or "", "")
    if field_label:
        return f"请检查「{field_label}」：{exc}"
    return f"请检查输入：{exc}"


def inline_error_message(exc: SettingsValidationError) -> str:
    return str(exc)


def restore_banner_message(page_name: str) -> str:
    page_label = PAGE_LABELS.get(page_name, page_name)
    return f"已恢复「{page_label}」默认值，记得点保存。"


def preview_banner_message() -> str:
    return "已发送浮层预览；此操作不会保存设置。"


def build_config_from_settings_values(
    base_config: AgentConfig,
    values: Mapping[str, str],
) -> AgentConfig:
    hotkey_vk_raw = values.get("hotkey_vk", "").strip()
    if hotkey_vk_raw:
        try:
            hotkey_vk = int(hotkey_vk_raw)
        except ValueError as exc:
            raise SettingsValidationError("热键值无效", field_name="hotkey_display") from exc
        canonical_hotkey = vk_to_hotkey(hotkey_vk)
        if canonical_hotkey is not None:
            hotkey = canonical_hotkey
            hotkey_display = vk_to_display(hotkey_vk)
        else:
            hotkey_display = values.get("hotkey_display", "").strip() or vk_to_display(hotkey_vk)
            try:
                hotkey = normalize_hotkey(hotkey_display)
            except ValueError as exc:
                raise SettingsValidationError(str(exc), field_name="hotkey_display") from exc
    else:
        hotkey = normalize_hotkey(values.get("hotkey", ""))
        if not hotkey:
            raise SettingsValidationError("热键不能为空", field_name="hotkey_display")
        try:
            hotkey_vk = vk_from_hotkey(hotkey)
        except ValueError as exc:
            raise SettingsValidationError(str(exc), field_name="hotkey_display") from exc
        hotkey_display = vk_to_display(hotkey_vk)

    mode = values.get("mode", "")
    if mode not in SUPPORTED_MODES:
        raise SettingsValidationError("模式无效", field_name="mode")

    injection_policy = values.get("injection_policy", "")
    if injection_policy not in SUPPORTED_INJECTION_POLICIES:
        raise SettingsValidationError("注入策略无效", field_name="injection_policy")

    capture_output_policy = values.get("capture_output_policy", "")
    if capture_output_policy not in SUPPORTED_CAPTURE_OUTPUT_POLICIES:
        raise SettingsValidationError("系统输出处理无效", field_name="capture_output_policy")

    polish_mode = values.get("polish_mode", "")
    if polish_mode not in SUPPORTED_POLISH_MODES:
        raise SettingsValidationError("润色模式无效", field_name="polish_mode")

    try:
        microphone_device = _parse_microphone_value(values.get("microphone_device", "__default__"))
    except ValueError as exc:
        raise SettingsValidationError("麦克风设置无效", field_name="microphone_device") from exc

    if should_show_ollama_fields(polish_mode):
        ollama_base_url = _parse_optional_text(
            values.get("ollama_base_url", ""),
            default=DEFAULT_OLLAMA_BASE_URL,
            strip_trailing_slash=True,
        )
        ollama_model = _parse_optional_text(values.get("ollama_model", ""))
        polish_timeout_ms = _parse_int(
            values.get("polish_timeout_ms", ""),
            "润色超时",
            100,
            5000,
            field_name="polish_timeout_ms",
        )
        ollama_warmup_enabled = _parse_bool_choice(
            values.get("ollama_warmup_enabled", "true"),
            "模型预热",
            field_name="ollama_warmup_enabled",
        )
    else:
        ollama_base_url = base_config.ollama_base_url
        ollama_model = base_config.ollama_model
        polish_timeout_ms = base_config.polish_timeout_ms
        ollama_warmup_enabled = base_config.ollama_warmup_enabled

    return replace(
        base_config,
        hotkey=hotkey,
        hotkey_vk=hotkey_vk,
        hotkey_display=hotkey_display,
        mode=mode,
        microphone_device=microphone_device,
        injection_policy=injection_policy,
        capture_output_policy=capture_output_policy,
        render_debounce_ms=_parse_int(
            values.get("render_debounce_ms", ""),
            "流式防抖",
            0,
            1000,
            field_name="render_debounce_ms",
        ),
        polish_mode=polish_mode,
        ollama_base_url=ollama_base_url,
        ollama_model=ollama_model,
        polish_timeout_ms=polish_timeout_ms,
        ollama_warmup_enabled=ollama_warmup_enabled,
        overlay_render_fps=_parse_int(
            values.get("overlay_render_fps", ""),
            "显示帧率",
            1,
            120,
            field_name="overlay_render_fps",
        ),
        overlay_font_size=_parse_int(
            values.get("overlay_font_size", ""),
            "字体大小",
            10,
            36,
            field_name="overlay_font_size",
        ),
        overlay_max_width=_parse_int(
            values.get("overlay_max_width", ""),
            "最大宽度",
            320,
            1200,
            field_name="overlay_max_width",
        ),
        overlay_opacity_percent=_parse_int(
            values.get("overlay_opacity_percent", ""),
            "透明度",
            35,
            100,
            field_name="overlay_opacity_percent",
        ),
        overlay_bottom_offset=_parse_int(
            values.get("overlay_bottom_offset", ""),
            "底部偏移",
            20,
            500,
            field_name="overlay_bottom_offset",
        ),
        overlay_animation_ms=_parse_int(
            values.get("overlay_animation_ms", ""),
            "动画时长",
            0,
            600,
            field_name="overlay_animation_ms",
        ),
    )


def _parse_int(raw: str, label: str, minimum: int, maximum: int, *, field_name: str) -> int:
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise SettingsValidationError(f"{label}必须是整数", field_name=field_name) from exc
    if value < minimum or value > maximum:
        raise SettingsValidationError(f"{label}必须在 {minimum} 到 {maximum} 之间", field_name=field_name)
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


def _parse_optional_text(raw: str, *, default: str = "", strip_trailing_slash: bool = False) -> str:
    value = raw.strip()
    if strip_trailing_slash:
        value = value.rstrip("/")
    return value or default


def _parse_bool_choice(raw: str, label: str, *, field_name: str) -> bool:
    normalized = raw.strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise SettingsValidationError(f"{label}无效", field_name=field_name)


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
        if field_name == "injection_policy":
            self._set_combo_items("injection_policy", INJECTION_POLICY_OPTIONS, values["injection_policy"])
            return
        if field_name == "capture_output_policy":
            self._set_combo_items("capture_output_policy", CAPTURE_OUTPUT_OPTIONS, values["capture_output_policy"])
            return
        if field_name == "polish_mode":
            self._set_combo_items("polish_mode", POLISH_MODE_OPTIONS, values["polish_mode"])
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


def _microphone_choice_value(value: int | str | None) -> str:
    if value is None:
        return "__default__"
    if isinstance(value, int):
        return f"index:{value}"
    return f"name:{value}"
