from __future__ import annotations

import argparse
import asyncio
import sys

from .service_main import add_service_args, run_service
from .worker_main import add_worker_args, run_worker


def _build_mode_probe_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    add_worker_args(parser)
    add_service_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    probe = _build_mode_probe_parser()
    mode_args, _ = probe.parse_known_args(argv)
    launch_args = list(sys.argv[1:] if argv is None else argv)
    try:
        if mode_args.worker and mode_args.service:
            probe.error("--worker and --service cannot be used together")
        if mode_args.service:
            args = probe.parse_args(argv)
            return asyncio.run(run_service(args))

        from .stable_simple_app import StableVoiceInputApp, build_arg_parser, build_config_from_args

        parser = build_arg_parser()
        add_worker_args(parser)
        args = parser.parse_args(argv)
        if args.worker:
            return asyncio.run(run_worker(args))
        config = build_config_from_args(args)
        app = StableVoiceInputApp(
            config,
            mode=getattr(args, "mode", None),
            enable_tray=not args.no_tray,
            console=args.console,
            launch_args=launch_args,
        )
        return asyncio.run(app.run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
