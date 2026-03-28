from __future__ import annotations

from .app import VoiceInputAgent, build_arg_parser, build_config_from_args


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    config = build_config_from_args(args)
    enable_tray = not args.headless
    app = VoiceInputAgent(config, enable_tray=enable_tray)
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
