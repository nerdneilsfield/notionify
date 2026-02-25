"""Structured JSON logger for notionify.

Every log record is emitted as a single-line JSON object so it can be
consumed by log aggregation pipelines (ELK, Datadog, CloudWatch, etc.)
without additional parsing.

Typical structured output::

    {"ts": "2025-07-01T12:00:00.123456+00:00", "level": "INFO",
     "logger": "notionify", "message": "append_markdown complete",
     "op": "append_markdown", "page_id": "abc123", "blocks": 12}

Usage::

    from notionify.observability import get_logger

    log = get_logger()
    log.info("page created", extra={"extra_fields": {"page_id": "abc"}})

    # Or create a child logger for a sub-module
    log = get_logger("notionify.converter")
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class StructuredFormatter(logging.Formatter):
    """Format log records as single-line JSON objects.

    The formatter produces a JSON object with the following guaranteed keys:

    * ``ts`` -- ISO-8601 UTC timestamp
    * ``level`` -- Python log level name (``DEBUG``, ``INFO``, ...)
    * ``logger`` -- Logger name
    * ``message`` -- Formatted log message

    Any extra structured fields can be passed via
    ``extra={"extra_fields": {...}}`` on the logging call and will be merged
    into the top-level JSON object.  Standard ``LogRecord`` attributes that
    are useful for debugging (``exc_info``, ``stack_info``) are serialised
    when present.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge caller-supplied structured fields.
        extra_fields: dict[str, Any] | None = getattr(
            record, "extra_fields", None
        )
        if extra_fields is not None:
            log_entry.update(extra_fields)

        # Include exception info when present.
        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Include stack info when present.
        if record.stack_info:
            log_entry["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(log_entry, default=str)


# ---------------------------------------------------------------------------
# Internal registry -- one handler per logger name so that ``get_logger``
# is idempotent even when called from multiple threads/modules.
# ---------------------------------------------------------------------------
_configured_loggers: set[str] = set()


def get_logger(
    name: str = "notionify",
    *,
    level: int | str = logging.DEBUG,
    stream: Any | None = None,
) -> logging.Logger:
    """Get or create a structured JSON logger.

    Parameters
    ----------
    name:
        Logger name.  Defaults to ``"notionify"``.  Child loggers such as
        ``"notionify.converter"`` will propagate to the root ``"notionify"``
        logger unless they have their own handler.
    level:
        Minimum log level.  Accepts an ``int`` (e.g. ``logging.INFO``) or a
        case-insensitive string (``"INFO"``).  Defaults to ``DEBUG`` so that
        the *handler* is not the bottleneck -- callers should set the desired
        level on the logger or handler after retrieval.
    stream:
        Output stream for the handler.  Defaults to ``sys.stderr``.

    Returns
    -------
    logging.Logger
        A logger instance with a :class:`StructuredFormatter` handler
        attached.  Repeated calls with the same *name* return the same
        logger and do **not** add duplicate handlers.
    """
    logger = logging.getLogger(name)

    if name not in _configured_loggers:
        # Resolve the level if given as a string.
        resolved_level = (
            logging.getLevelName(level.upper())
            if isinstance(level, str)
            else level
        )
        logger.setLevel(resolved_level)

        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)

        # Prevent duplicate messages when a parent logger (e.g. root) also
        # has handlers configured.
        logger.propagate = False

        _configured_loggers.add(name)

    return logger
