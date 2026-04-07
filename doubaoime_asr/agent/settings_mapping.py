from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from .config import (
    AgentConfig,
    DEFAULT_OLLAMA_BASE_URL,
    SUPPORTED_CAPTURE_OUTPUT_POLICIES,
    SUPPORTED_FINAL_COMMIT_SOURCES,
    SUPPORTED_INJECTION_POLICIES,
    SUPPORTED_MODES,
    SUPPORTED_POLISH_MODES,
    SUPPORTED_STREAMING_TEXT_MODES,
)
from .settings_schema import should_show_ollama_fields
from .settings_validation import SettingsValidationError
from .win_hotkey import normalize_hotkey, vk_from_hotkey, vk_to_display, vk_to_hotkey


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
        "streaming_text_mode": config.streaming_text_mode,
        "injection_policy": config.injection_policy,
        "capture_output_policy": config.capture_output_policy,
        "render_debounce_ms": str(config.render_debounce_ms),
        "polish_mode": config.polish_mode,
        "final_commit_source": config.final_commit_source,
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

    streaming_text_mode = values.get("streaming_text_mode", base_config.streaming_text_mode)
    if streaming_text_mode not in SUPPORTED_STREAMING_TEXT_MODES:
        raise SettingsValidationError("实时文本模式无效", field_name="streaming_text_mode")

    injection_policy = values.get("injection_policy", "")
    if injection_policy not in SUPPORTED_INJECTION_POLICIES:
        raise SettingsValidationError("注入策略无效", field_name="injection_policy")

    capture_output_policy = values.get("capture_output_policy", "")
    if capture_output_policy not in SUPPORTED_CAPTURE_OUTPUT_POLICIES:
        raise SettingsValidationError("系统输出处理无效", field_name="capture_output_policy")

    polish_mode = values.get("polish_mode", "")
    if polish_mode not in SUPPORTED_POLISH_MODES:
        raise SettingsValidationError("润色模式无效", field_name="polish_mode")

    final_commit_source = values.get("final_commit_source", base_config.final_commit_source)
    if final_commit_source not in SUPPORTED_FINAL_COMMIT_SOURCES:
        raise SettingsValidationError("最终提交内容无效", field_name="final_commit_source")

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
        streaming_text_mode=streaming_text_mode,
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
        final_commit_source=final_commit_source,
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


def _microphone_choice_value(value: int | str | None) -> str:
    if value is None:
        return "__default__"
    if isinstance(value, int):
        return f"index:{value}"
    return f"name:{value}"
