import sys
import types


if "pywinauto" not in sys.modules:
    pywinauto_stub = types.ModuleType("pywinauto")
    pywinauto_stub.Desktop = object
    keyboard_stub = types.ModuleType("pywinauto.keyboard")
    keyboard_stub.send_keys = lambda *args, **kwargs: None
    sys.modules["pywinauto"] = pywinauto_stub
    sys.modules["pywinauto.keyboard"] = keyboard_stub


from doubaoime_asr.agent.stable_simple_app import build_arg_parser, build_config_from_args


def test_stable_cli_defaults_to_recognize():
    parser = build_arg_parser()
    args = parser.parse_args([])
    config = build_config_from_args(args)

    assert getattr(args, "mode", None) is None
    assert config.polish_mode == "light"


def test_stable_cli_config_override():
    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "--mode",
            "recognize",
            "--hotkey",
            "f9",
            "--render-debounce-ms",
            "50",
            "--polish-mode",
            "ollama",
            "--ollama-base-url",
            "http://127.0.0.1:11434/",
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
