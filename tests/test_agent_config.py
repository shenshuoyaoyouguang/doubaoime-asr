from pathlib import Path
import sys

from doubaoime_asr.agent.config import (
    AgentConfig,
    INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
    POLISH_MODE_OLLAMA,
    discover_preferred_credential_path,
)


def test_agent_config_roundtrip(tmp_path: Path):
    path = tmp_path / "config.json"
    config = AgentConfig(
        hotkey="f9",
        hotkey_vk=0x78,
        hotkey_display="F9",
        mode="recognize",
        microphone_device="USB Mic",
        credential_path=str(tmp_path / "credentials.json"),
        injection_policy=INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
        render_debounce_ms=120,
        polish_mode=POLISH_MODE_OLLAMA,
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="qwen2.5:3b",
        polish_timeout_ms=1200,
        ollama_warmup_enabled=False,
        ollama_keep_alive="10m",
        ollama_prompt_template="请润色：{text}",
        overlay_render_fps=45,
        overlay_font_size=15,
        overlay_max_width=700,
        overlay_opacity_percent=88,
        overlay_bottom_offset=144,
        overlay_animation_ms=180,
    )

    config.save(path)
    loaded = AgentConfig.load(path)

    assert loaded == config


def test_agent_config_creates_default_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    loaded = AgentConfig.load()

    assert Path(loaded.default_path()).exists()
    assert AgentConfig.default_log_path() == tmp_path / "DoubaoVoiceInput" / "logs" / "agent.log"


def test_discover_preferred_credential_path_prefers_cwd(tmp_path: Path, monkeypatch):
    cred = tmp_path / "credentials.json"
    cred.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

    assert discover_preferred_credential_path() == cred


def test_agent_config_load_migrates_old_default_credential_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo_cred = workspace / "credentials.json"
    repo_cred.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

    config_path = tmp_path / "custom-config.json"
    old_default = str(Path(tmp_path / "appdata") / "DoubaoVoiceInput" / "credentials.json")
    config_path.write_text(
        f'{{"hotkey":"f8","microphone_device":null,"credential_path":"{old_default.replace("\\", "\\\\")}","render_debounce_ms":80}}',
        encoding="utf-8",
    )

    loaded = AgentConfig.load(config_path)

    assert loaded.credential_path == str(repo_cred)
    assert loaded.hotkey_vk == 0x77
    assert loaded.hotkey_display == "F8"


def test_agent_config_overlay_style_payload():
    config = AgentConfig(
        overlay_font_size=16,
        overlay_max_width=640,
        overlay_opacity_percent=87,
        overlay_bottom_offset=132,
        overlay_animation_ms=190,
    )

    assert config.overlay_style_payload() == {
        "font_size": "16",
        "max_width": "640",
        "opacity_percent": "87",
        "bottom_offset": "132",
        "animation_ms": "190",
    }


def test_agent_config_effective_hotkey_uses_vk_fields():
    config = AgentConfig(hotkey="f8", hotkey_vk=0x41, hotkey_display="A")

    assert config.effective_hotkey_vk() == 0x41
    assert config.effective_hotkey_display() == "A"


def test_agent_config_load_sanitizes_polish_fields(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(
        '{"polish_mode":"invalid","ollama_base_url":"  ","polish_timeout_ms":"99999","ollama_warmup_enabled":"false","ollama_keep_alive":"","ollama_prompt_template":""}',
        encoding="utf-8",
    )

    loaded = AgentConfig.load(path)

    assert loaded.polish_mode == "light"
    assert loaded.ollama_base_url == "http://localhost:11434"
    assert loaded.ollama_model == "qwen35-opus-fixed:latest"
    assert loaded.polish_timeout_ms == 5000
    assert loaded.ollama_warmup_enabled is False
    assert loaded.ollama_keep_alive == "15m"
    assert "{text}" in loaded.ollama_prompt_template
