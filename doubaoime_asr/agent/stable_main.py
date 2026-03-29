from __future__ import annotations

import asyncio

from .stable_simple_app import StableVoiceInputApp, build_arg_parser, build_config_from_args
from .worker_main import add_worker_args, run_worker


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    add_worker_args(parser)
    args = parser.parse_args(argv)
    try:
        if args.worker:
            return asyncio.run(run_worker(args))
        config = build_config_from_args(args)
        app = StableVoiceInputApp(
            config,
            mode=getattr(args, "mode", None),
            enable_tray=not args.no_tray,
            console=args.console,
        )
        return asyncio.run(app.run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
