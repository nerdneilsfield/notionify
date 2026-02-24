"""Multi-byte safe string splitting.

Notion's ``rich_text[].text.content`` field is limited to 2 000 characters.
This module provides :func:`split_string`, which partitions an arbitrary
Python string into chunks that each contain **at most** *limit* characters
without ever cutting a multi-byte character or emoji in half.

Because Python ``str`` is a sequence of *Unicode code-points*, ordinary
slicing (``text[i:j]``) is already character-safe — it will never produce an
invalid surrogate or half a code-point.  We leverage that property here so
no special byte-level logic is needed.
"""

from __future__ import annotations


def split_string(text: str, limit: int = 2000) -> list[str]:
    """Split *text* into chunks of at most *limit* characters.

    The split is performed on **character** boundaries (Python code-points).
    Because Python's native ``str`` indexing is code-point based, this is
    inherently safe for multi-byte UTF-8 characters, surrogate pairs, and
    grapheme clusters — no character is ever bisected.

    Parameters
    ----------
    text:
        The input string to partition.
    limit:
        Maximum number of characters per chunk.  Defaults to **2000**
        (the Notion ``rich_text.text.content`` limit).

    Returns
    -------
    list[str]
        A list of non-empty string chunks whose concatenation equals *text*.
        If *text* is empty, an empty list is returned.

    Raises
    ------
    ValueError
        If *limit* is less than 1.

    Examples
    --------
    >>> split_string("hello world", 5)
    ['hello', ' worl', 'd']

    >>> split_string("", 100)
    []

    Multi-byte safety:

    >>> split_string("ab\U0001f600cd", 3)  # \U0001f600 is 😀
    ['ab\\U0001f600', 'cd']
    """
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")

    if not text:
        return []

    return [text[i : i + limit] for i in range(0, len(text), limit)]
