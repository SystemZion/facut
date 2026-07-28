"""Private, rotating application logging."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from facut.config import default_log_directory

LOGGER_NAME = "facut"


def configure_logging(
    *,
    verbose: bool = False,
    quiet: bool = False,
    log_directory: Path | None = None,
) -> logging.Logger:
    """Configure one rotating file handler without duplicating handlers."""

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    directory = log_directory or default_log_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            directory / "facut.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%SZ",
            )
        )
        logger.addHandler(handler)
    except OSError:
        # Logging must never prevent editing. Quiet mode intentionally has no
        # console fallback; otherwise the caller may opt into verbose stderr.
        if verbose and not quiet:
            stream = logging.StreamHandler()
            stream.setLevel(logging.DEBUG)
            logger.addHandler(stream)
    return logger
