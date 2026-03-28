from doubaoime_asr.agent.stable_simple_app import build_arg_parser, build_config_from_args


def test_stable_cli_defaults_to_recognize():
    parser = build_arg_parser()
    args = parser.parse_args([])

    assert args.mode == "inject"


def test_stable_cli_config_override():
    parser = build_arg_parser()
    args = parser.parse_args(["--hotkey", "f9", "--render-debounce-ms", "50"])
    config = build_config_from_args(args)

    assert config.hotkey == "f9"
    assert config.render_debounce_ms == 50


def test_stable_cli_accepts_console_and_no_tray():
    parser = build_arg_parser()
    args = parser.parse_args(["--mode", "recognize", "--console", "--no-tray"])

    assert args.mode == "recognize"
    assert args.console is True
    assert args.no_tray is True
