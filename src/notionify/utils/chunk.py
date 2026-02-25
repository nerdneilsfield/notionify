"""Batch a list of Notion block dicts into groups of at most *size* items.

The Notion ``append_block_children`` endpoint accepts a maximum of 100 blocks
per request.  This helper splits an arbitrarily long list into compliant
batches so callers never have to worry about the limit.
"""

from __future__ import annotations

from typing import Any


def chunk_children(blocks: list[dict[str, Any]], size: int = 100) -> list[list[dict[str, Any]]]:
    """Split a list of Notion block dicts into batches of at most ``size``.

    Parameters
    ----------
    blocks:
        The full list of block dictionaries to partition.
    size:
        Maximum number of blocks per batch.  Defaults to **100** (the Notion
        API limit for ``append_block_children``).

    Returns
    -------
    list[list[dict]]
        A list of sublists, each containing at most *size* items.
        An empty input returns an empty list (not ``[[]]``).

    Raises
    ------
    ValueError
        If *size* is less than 1.

    Examples
    --------
    >>> chunk_children([{"type": "paragraph"}] * 250)  # doctest: +ELLIPSIS
    [[...], [...], [...]]
    """
    if size < 1:
        raise ValueError(f"size must be >= 1, got {size}")

    if not blocks:
        return []

    return [blocks[i : i + size] for i in range(0, len(blocks), size)]
