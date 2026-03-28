import pytest

from doubaoime_asr.agent.config import AgentConfig, INJECTION_POLICY_DIRECT_THEN_CLIPBOARD
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
            "render_debounce_ms": "40",
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
    assert updated.overlay_render_fps == 45
    assert updated.overlay_font_size == 16


def test_build_config_from_settings_values_rejects_invalid_hotkey():
    with pytest.raises(SettingsValidationError):
        build_config_from_settings_values(
            AgentConfig(),
            {
                "hotkey": "ctrl+space",
                "mode": "inject",
                "microphone_device": "__default__",
                "injection_policy": "direct_only",
                "render_debounce_ms": "80",
                "overlay_render_fps": "30",
                "overlay_font_size": "14",
                "overlay_max_width": "620",
                "overlay_opacity_percent": "92",
                "overlay_bottom_offset": "120",
                "overlay_animation_ms": "150",
            },
        )
