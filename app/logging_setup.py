"""Logging setup with token redaction and a hard disk cap."""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
from pathlib import Path

REDACTIONS = (
    (re.compile(r"Bot\s+[A-Za-z0-9._-]{20,}"), "Bot [REDACTED]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(?i)(token|secret|authorization)(\s*[=:]\s*)\S+"), r"\1\2[REDACTED]"),
)


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        for pattern, replacement in REDACTIONS:
            message = pattern.sub(replacement, message)
        record.msg = message
        record.args = ()
        return True


def setup_logging(
    log_dir: Path, level: str = "INFO", max_bytes: int = 5_000_000, backups: int = 2
) -> None:
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(log_dir, 0o700)
    except OSError:
        pass

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    redaction = RedactionFilter()

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.addFilter(redaction)
    root.addHandler(stream)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "appardas.log",
        maxBytes=max_bytes,
        backupCount=max(0, backups),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redaction)
    root.addHandler(file_handler)

    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    if level.upper() == "DEBUG":
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("discord").setLevel(logging.INFO)
