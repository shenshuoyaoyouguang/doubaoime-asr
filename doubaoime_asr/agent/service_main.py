from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import logging
import os
from pathlib import Path
import sys

from .config import AgentConfig
from .runtime_logging import setup_named_logger
from .service_protocol import (
    SERVICE_PROTOCOL_VERSION,
    encode_service_event,
)
from .service_host import ServiceHost
from .service_transport import ServiceTransport, build_service_transport


def _configure_stdio_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")



def build_service_log_path(path_arg: str | None = None) -> Path:
    if path_arg:
        return Path(path_arg)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return AgentConfig.default_log_dir() / f"service-{timestamp}-{os.getpid()}.log"



def add_service_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--service", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--service-log-path", help=argparse.SUPPRESS)
    parser.add_argument(
        "--service-transport",
        choices=("stdio", "named_pipe", "named_pipe_placeholder"),
        default="stdio",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--service-pipe-name",
        default=r"\\.\pipe\doubao-tip-service",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--service-protocol-version",
        type=int,
        default=SERVICE_PROTOCOL_VERSION,
        help=argparse.SUPPRESS,
    )

def _emit_stdout(event_type: str, **payload: object) -> None:
    print(encode_service_event(event_type, **payload), flush=True)


async def run_service(args: argparse.Namespace) -> int:
    _configure_stdio_utf8()
    log_path = build_service_log_path(getattr(args, "service_log_path", None))
    logger = setup_named_logger(f"doubaoime_asr.agent.service.{id(log_path)}", log_path)
    config = AgentConfig.load()
    transport: ServiceTransport = build_service_transport(
        logger=logger,
        loop=asyncio.get_running_loop(),
        transport_kind=getattr(args, "service_transport", "stdio"),
        pipe_name=getattr(args, "service_pipe_name", r"\\.\pipe\doubao-tip-service"),
    )

    protocol_version = getattr(args, "service_protocol_version", SERVICE_PROTOCOL_VERSION)
    if protocol_version != SERVICE_PROTOCOL_VERSION:
        transport.emit_event(
            "error",
            message=(
                f"unsupported service protocol version {protocol_version}; "
                f"expected {SERVICE_PROTOCOL_VERSION}"
            ),
        )
        return 2

    line_queue: asyncio.Queue[str] = asyncio.Queue()
    transport.start_reader(line_queue)
    host = ServiceHost(logger=logger, transport=transport, config=config)
    ready_payload = host.runtime.service_ready_payload()

    logger.info(
        "service_ready protocol_version=%s skeleton=%s",
        SERVICE_PROTOCOL_VERSION,
        ready_payload["skeleton"],
    )

    try:
        return await host.run(line_queue)
    finally:
        transport.close()
