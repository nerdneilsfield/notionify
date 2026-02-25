"""Observability: structured logging and metrics hooks for notionify."""

from __future__ import annotations

from .logger import StructuredFormatter, get_logger
from .metrics import MetricsHook, NoopMetricsHook

__all__ = [
    "MetricsHook",
    "NoopMetricsHook",
    "StructuredFormatter",
    "get_logger",
]
