import pytest

from doubaoime_asr.agent.config import (
    AgentConfig,
    CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT,
    INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
    POLISH_MODE_OLLAMA,
)
from doubaoime_asr.agent.settings_window import (
    FIELD_TO_PAGE,
    PAGE_FIELDS,
    PAGE_ORDER,
    SettingsValidationError,
    SettingsWindowController,
    build_config_from_settings_values,
    inline_error_message,
    page_footer_hint,
    page_heading,
    preview_banner_message,
    restore_banner_message,
    settings_values_from_config,
    should_show_ollama_fields,
    validation_banner_message,
    visible_fields_for_page,
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


def test_should_show_ollama_fields_tracks_polish_mode():
    assert should_show_ollama_fields("off") is False
    assert should_show_ollama_fields("light") is False
    assert should_show_ollama_fields("ollama") is True


def test_visible_fields_for_page_hides_ollama_details_until_needed():
    assert visible_fields_for_page("polish", "light") == ["polish_mode"]
    assert visible_fields_for_page("polish", "ollama") == list(PAGE_FIELDS["polish"])


def test_settings_values_from_config_roundtrip_matches_current_model():
    config = AgentConfig(
        mode="recognize",
        microphone_device=2,
        injection_policy="direct_only",
        capture_output_policy="mute_system_output",
        render_debounce_ms=40,
        polish_mode="light",
        overlay_font_size=16,
        overlay_opacity_percent=88,
    )

    values = settings_values_from_config(config)

    assert values["mode"] == "recognize"
    assert values["microphone_device"] == "index:2"
    assert values["injection_policy"] == "direct_only"
    assert values["capture_output_policy"] == "mute_system_output"
    assert values["render_debounce_ms"] == "40"
    assert values["overlay_font_size"] == "16"
    assert values["overlay_opacity_percent"] == "88"


def test_build_config_from_settings_values_keeps_hidden_ollama_settings_when_mode_not_ollama():
    base = AgentConfig(
        polish_mode="ollama",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="qwen2.5:7b",
        polish_timeout_ms=1600,
        ollama_warmup_enabled=False,
    )

    updated = build_config_from_settings_values(
        base,
        {
            "hotkey_vk": str(base.effective_hotkey_vk()),
            "hotkey_display": base.effective_hotkey_display(),
            "mode": "inject",
            "microphone_device": "__default__",
            "injection_policy": "direct_only",
            "capture_output_policy": "off",
            "render_debounce_ms": "80",
            "polish_mode": "light",
            "ollama_base_url": "http://bad.example",
            "ollama_model": "bad-model",
            "polish_timeout_ms": "9999",
            "ollama_warmup_enabled": "true",
            "overlay_render_fps": "30",
            "overlay_font_size": "14",
            "overlay_max_width": "620",
            "overlay_opacity_percent": "92",
            "overlay_bottom_offset": "120",
            "overlay_animation_ms": "150",
        },
    )

    assert updated.polish_mode == "light"
    assert updated.ollama_base_url == base.ollama_base_url
    assert updated.ollama_model == base.ollama_model
    assert updated.polish_timeout_ms == base.polish_timeout_ms
    assert updated.ollama_warmup_enabled is base.ollama_warmup_enabled


def test_build_config_from_settings_values_reports_invalid_field_name():
    with pytest.raises(SettingsValidationError) as exc_info:
        build_config_from_settings_values(
            AgentConfig(),
            {
                "hotkey_vk": "119",
                "hotkey_display": "F8",
                "mode": "inject",
                "microphone_device": "__default__",
                "injection_policy": "direct_only",
                "capture_output_policy": "off",
                "render_debounce_ms": "bad",
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

    assert exc_info.value.field_name == "render_debounce_ms"


def test_page_heading_reflects_progressive_hierarchy():
    assert page_heading("general") == f"1/{len(PAGE_ORDER)} · 常用"
    assert page_heading("overlay") == f"4/{len(PAGE_ORDER)} · 浮层显示"


def test_page_footer_hint_changes_with_polish_mode_state():
    assert "Ollama" in page_footer_hint("polish", "light")
    assert "本地模型" in page_footer_hint("polish", "ollama")
    assert "预览" in page_footer_hint("overlay", "light")


def test_validation_banner_message_uses_friendly_field_label():
    error = SettingsValidationError("必须是整数", field_name="render_debounce_ms")
    assert validation_banner_message(error) == "请检查「流式防抖(ms)」：必须是整数"


def test_restore_and_preview_banner_messages_are_actionable():
    assert restore_banner_message("overlay") == "已恢复「浮层显示」默认值，记得点保存。"
    assert preview_banner_message() == "已发送浮层预览；此操作不会保存设置。"


def test_inline_error_message_matches_validation_text():
    error = SettingsValidationError("必须在 0 到 100 之间", field_name="overlay_opacity_percent")
    assert inline_error_message(error) == "必须在 0 到 100 之间"


def test_controller_set_field_error_tracks_field_inline_message():
    controller = SettingsWindowController(
        logger=__import__("logging").getLogger("test-settings-window"),
        get_current_config=AgentConfig,
        on_save=lambda config: None,
    )
    controller._active_page = "general"
    controller._activate_page = lambda page_name: setattr(controller, "_active_page", page_name)

    controller._set_field_error(SettingsValidationError("必须是整数", field_name="render_debounce_ms"))

    assert controller._field_errors == {"render_debounce_ms": "必须是整数"}
    assert controller._active_page == FIELD_TO_PAGE["render_debounce_ms"]


def test_controller_clear_field_errors_removes_inline_messages():
    controller = SettingsWindowController(
        logger=__import__("logging").getLogger("test-settings-window"),
        get_current_config=AgentConfig,
        on_save=lambda config: None,
    )
    controller._field_errors["render_debounce_ms"] = "必须是整数"

    controller._clear_field_errors()

    assert controller._field_errors == {}
