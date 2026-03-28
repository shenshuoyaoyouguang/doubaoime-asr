from __future__ import annotations

import logging
from pathlib import Path


LOGGER_NAME = "doubaoime_asr.agent"


def setup_agent_logger(log_path: Path) -> logging.Logger:
    return setup_named_logger(LOGGER_NAME, log_path)


def setup_named_logger(name: str, log_path: Path) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(log_path, encoding="utf-8")
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    logger.info("logger initialized")
    return logger
