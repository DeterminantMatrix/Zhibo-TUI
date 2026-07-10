"""Application file logging helpers."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "zhibo.log"

_CONFIGURED = False


def setup_logging() -> Path:
    """Configure the shared application logger once and return the log file path."""
    global _CONFIGURED
    if _CONFIGURED:
        return LOG_FILE

    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger("zhibo")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", "") == str(LOG_FILE) for handler in logger.handlers):
        handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=1_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        logger.addHandler(handler)

    _CONFIGURED = True
    return LOG_FILE


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
