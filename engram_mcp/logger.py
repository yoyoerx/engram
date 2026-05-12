"""Structured logger for the Engram MCP server.

JSON-lines written to a rotating log file; human-readable warnings+
written to stderr (stdout is reserved for the MCP protocol).
"""

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path.home() / ".engram" / "logs"
LOG_FILE = LOG_DIR / "engram.log"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 3


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record, including any extra= fields."""

    _BASE_ATTRS = frozenset(
        logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    ) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.message,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, val in record.__dict__.items():
            if key not in self._BASE_ATTRS:
                try:
                    json.dumps(val)  # skip non-serialisable extras
                    payload[key] = val
                except (TypeError, ValueError):
                    payload[key] = repr(val)
        return json.dumps(payload, ensure_ascii=False)


def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            LOG_FILE,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setFormatter(_JsonFormatter())
        fh.setLevel(logging.DEBUG)
        logger.addHandler(fh)
    except OSError:
        pass  # log dir not writable; file logging silently disabled

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    sh.setLevel(logging.WARNING)
    logger.addHandler(sh)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a named child logger under the 'engram' namespace."""
    return _build_logger(f"engram.{name}")
