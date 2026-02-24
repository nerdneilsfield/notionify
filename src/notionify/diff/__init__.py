"""Diff engine for incremental page updates.

Exports
-------
DiffPlanner
    Computes minimal edit operations between existing and desired blocks.
DiffExecutor
    Applies diff operations synchronously via the Notion API.
AsyncDiffExecutor
    Applies diff operations asynchronously via the Notion API.
"""

from .planner import DiffPlanner
from .executor import AsyncDiffExecutor, DiffExecutor

__all__ = [
    "DiffPlanner",
    "DiffExecutor",
    "AsyncDiffExecutor",
]
