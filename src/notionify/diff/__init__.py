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

from .executor import AsyncDiffExecutor, DiffExecutor
from .planner import DiffPlanner

__all__ = [
    "AsyncDiffExecutor",
    "DiffExecutor",
    "DiffPlanner",
]
