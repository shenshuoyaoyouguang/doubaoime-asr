import pytest

from doubaoime_asr.agent.config import (
    AgentConfig,
    CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT,
    INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
    POLISH_MODE_OLLAMA,
)
from doubaoime_asr.agent.settings_window import (
    SettingsValidationError,
    build_config_from_settings_values,
)


def test_build_config_from_settings_values_updates_runtime_fields():
    config = AgentConfig()

    updated = build_config_from_settings_values(
        config,
        {
            "hotkey_vk": "120",
            "hotkey_display": "F9",
            "mode": "recognize",
            "microphone_device": "index:3",
            "injection_policy": INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
            "capture_output_policy": CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT,
            "render_debounce_ms": "40",
            "polish_mode": POLISH_MODE_OLLAMA,
            "ollama_base_url": "http://127.0.0.1:11434/",
            "ollama_model": "qwen2.5:3b",
            "polish_timeout_ms": "900",
            "ollama_warmup_enabled": "false",
            "overlay_render_fps": "45",
            "overlay_font_size": "16",
            "overlay_max_width": "700",
            "overlay_opacity_percent": "88",
            "overlay_bottom_offset": "150",
            "overlay_animation_ms": "180",
        },
    )

    assert updated.hotkey == "f9"
    assert updated.hotkey_vk == 120
    assert updated.hotkey_display == "F9"
    assert updated.mode == "recognize"
    assert updated.microphone_device == 3
    assert updated.injection_policy == INJECTION_POLICY_DIRECT_THEN_CLIPBOARD
    assert updated.capture_output_policy == CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT
    assert updated.polish_mode == POLISH_MODE_OLLAMA
    assert updated.ollama_base_url == "http://127.0.0.1:11434"
    assert updated.ollama_model == "qwen2.5:3b"
    assert updated.polish_timeout_ms == 900
    assert updated.ollama_warmup_enabled is False
    assert updated.overlay_render_fps == 45
    assert updated.overlay_font_size == 16


def test_build_config_from_settings_values_canonicalizes_right_ctrl():
    updated = build_config_from_settings_values(
        AgentConfig(),
        {
            "hotkey_vk": "163",
            "hotkey_display": "CTRL",
            "mode": "inject",
            "microphone_device": "__default__",
            "injection_policy": "direct_only",
            "capture_output_policy": "off",
            "render_debounce_ms": "80",
            "polish_mode": "off",
            "ollama_base_url": "http://localhost:11434",
            "ollama_model": "",
            "polish_timeout_ms": "800",
            "ollama_warmup_enabled": "true",
            "overlay_render_fps": "30",
            "overlay_font_size": "14",
            "overlay_max_width": "620",
            "overlay_opacity_percent": "92",
            "overlay_bottom_offset": "120",
            "overlay_animation_ms": "150",
        },
    )

    assert updated.hotkey == "right_ctrl"
    assert updated.hotkey_vk == 163
    assert updated.hotkey_display == "RIGHT CTRL"


def test_build_config_from_settings_values_rejects_invalid_hotkey():
    with pytest.raises(SettingsValidationError):
        build_config_from_settings_values(
            AgentConfig(),
            {
                "hotkey": "ctrl+space",
                "mode": "inject",
                "microphone_device": "__default__",
                "injection_policy": "direct_only",
                "capture_output_policy": "off",
                "render_debounce_ms": "80",
                "polish_mode": "off",
                "ollama_base_url": "http://localhost:11434",
                "ollama_model": "",
                "polish_timeout_ms": "800",
                "ollama_warmup_enabled": "true",
                "overlay_render_fps": "30",
                "overlay_font_size": "14",
                "overlay_max_width": "620",
                "overlay_opacity_percent": "92",
                "overlay_bottom_offset": "120",
                "overlay_animation_ms": "150",
            },
        )


def test_build_config_from_settings_values_rejects_invalid_capture_output_policy():
    with pytest.raises(SettingsValidationError):
        build_config_from_settings_values(
            AgentConfig(),
            {
                "hotkey_vk": "119",
                "hotkey_display": "F8",
                "mode": "inject",
                "microphone_device": "__default__",
                "injection_policy": "direct_only",
                "capture_output_policy": "invalid",
                "render_debounce_ms": "80",
                "polish_mode": "off",
                "ollama_base_url": "http://localhost:11434",
                "ollama_model": "",
                "polish_timeout_ms": "800",
                "ollama_warmup_enabled": "true",
                "overlay_render_fps": "30",
                "overlay_font_size": "14",
                "overlay_max_width": "620",
                "overlay_opacity_percent": "92",
                "overlay_bottom_offset": "120",
                "overlay_animation_ms": "150",
            },
        )
