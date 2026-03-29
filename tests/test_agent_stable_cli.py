import sys
import types


if "pywinauto" not in sys.modules:
    pywinauto_stub = types.ModuleType("pywinauto")
    pywinauto_stub.Desktop = object
    keyboard_stub = types.ModuleType("pywinauto.keyboard")
    keyboard_stub.send_keys = lambda *args, **kwargs: None
    sys.modules["pywinauto"] = pywinauto_stub
    sys.modules["pywinauto.keyboard"] = keyboard_stub


import pytest
from doubaoime_asr.agent.stable_simple_app import build_arg_parser, build_config_from_args
from doubaoime_asr.agent.config import AgentConfig


@pytest.fixture(autouse=True)
def _stub_agent_config_load(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(AgentConfig, "load", classmethod(lambda cls, path=None: AgentConfig()))


def test_stable_cli_defaults_to_recognize(monkeypatch):
    monkeypatch.setattr(AgentConfig, "load", classmethod(lambda cls, path=None: cls.default()))
    parser = build_arg_parser()
    args = parser.parse_args([])
    config = build_config_from_args(args)

    assert getattr(args, "mode", None) is None
    assert config.polish_mode == "light"
    assert config.overlay_render_fps == 60
    assert config.streaming_text_mode == "safe_inline"


def test_stable_cli_config_override(monkeypatch):
    monkeypatch.setattr(AgentConfig, "load", classmethod(lambda cls, path=None: cls.default()))
    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "--mode",
            "recognize",
            "--hotkey",
            "f9",
            "--render-debounce-ms",
            "50",
            "--streaming-text-mode",
            "overlay_only",
            "--capture-output-policy",
            "mute_system_output",
            "--polish-mode",
            "ollama",
            "--ollama-base-url",
            "  http://127.0.0.1:11434/  ",
            "--ollama-model",
            "qwen2.5:3b",
            "--polish-timeout-ms",
            "900",
            "--disable-ollama-warmup",
        ]
    )
    config = build_config_from_args(args)

    assert config.mode == "recognize"
    assert config.hotkey == "f9"
    assert config.hotkey_vk == 0x78
    assert config.hotkey_display == "F9"
    assert config.render_debounce_ms == 50
    assert config.streaming_text_mode == "overlay_only"
    assert config.capture_output_policy == "mute_system_output"
    assert config.polish_mode == "ollama"
    assert config.ollama_base_url == "http://127.0.0.1:11434"
    assert config.ollama_model == "qwen2.5:3b"
    assert config.polish_timeout_ms == 900
    assert config.ollama_warmup_enabled is False


def test_stable_cli_accepts_console_and_no_tray():
    parser = build_arg_parser()
    args = parser.parse_args(["--mode", "recognize", "--console", "--no-tray"])

    assert args.mode == "recognize"
    assert args.console is True
    assert args.no_tray is True
