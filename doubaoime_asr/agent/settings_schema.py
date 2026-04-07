from __future__ import annotations

from .config import (
    CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT,
    CAPTURE_OUTPUT_POLICY_OFF,
    FINAL_COMMIT_SOURCE_POLISHED,
    FINAL_COMMIT_SOURCE_RAW,
    INJECTION_POLICY_DIRECT_ONLY,
    INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
    POLISH_MODE_LIGHT,
    POLISH_MODE_OFF,
    POLISH_MODE_OLLAMA,
    STREAMING_TEXT_MODE_OVERLAY_ONLY,
    STREAMING_TEXT_MODE_SAFE_INLINE,
)


MODE_OPTIONS: list[tuple[str, str]] = [
    ("inject", "自动上屏"),
    ("recognize", "仅识别（不自动上屏）"),
]

INJECTION_POLICY_OPTIONS: list[tuple[str, str]] = [
    (INJECTION_POLICY_DIRECT_THEN_CLIPBOARD, "智能兼容（默认，失败时用剪贴板）"),
    (INJECTION_POLICY_DIRECT_ONLY, "仅直接输入（绝不动剪贴板）"),
]

STREAMING_TEXT_MODE_OPTIONS: list[tuple[str, str]] = [
    (STREAMING_TEXT_MODE_SAFE_INLINE, "安全实时上屏（普通编辑框）"),
    (STREAMING_TEXT_MODE_OVERLAY_ONLY, "仅显示浮层（更稳）"),
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

FINAL_COMMIT_SOURCE_OPTIONS: list[tuple[str, str]] = [
    (FINAL_COMMIT_SOURCE_POLISHED, "提交润色结果（兼容当前行为）"),
    (FINAL_COMMIT_SOURCE_RAW, "提交原始识别（更贴近浮层）"),
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
    "behavior": (
        "streaming_text_mode",
        "render_debounce_ms",
        "injection_policy",
        "capture_output_policy",
    ),
    "polish": (
        "polish_mode",
        "final_commit_source",
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
    "streaming_text_mode": "实时文本模式",
    "render_debounce_ms": "流式防抖(ms)",
    "injection_policy": "注入策略",
    "capture_output_policy": "系统输出处理",
    "polish_mode": "最终润色",
    "final_commit_source": "最终提交内容",
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
    "streaming_text_mode": "普通编辑框可尝试实时上屏；如果遇到丢字重字，优先切到仅显示浮层。",
    "render_debounce_ms": "数值越小越灵敏，过低可能导致显示闪动。",
    "injection_policy": "兼容模式失败时会自动回退到剪贴板。",
    "capture_output_policy": "如果录音时担心系统声音干扰，可以临时静音输出。",
    "polish_mode": "轻量整理适合大多数场景；本地模型更强但更慢。",
    "final_commit_source": "可选择最终提交原文或润色结果，避免“浮层更准、落字被改写”的困惑。",
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
    "streaming_text_mode",
    "injection_policy",
    "capture_output_policy",
    "polish_mode",
    "final_commit_source",
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
        return "提示：如果某些软件实时上屏不稳，先把实时文本模式切到“仅显示浮层”。"
    if page_name == "polish":
        if should_show_ollama_fields(polish_mode):
            return "提示：可单独决定最终提交原文还是润色结果；修改地址或模型后记得保存。"
        return "提示：轻量整理适合多数场景；也可以选择只润色显示、最终仍提交原文。"
    if page_name == "overlay":
        return "提示：可以先预览浮层，确认样式后再保存；预览不会写入配置。"
    return ""
