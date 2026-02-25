"""Conflict detection for diff-based page updates.

Compares a pre-diff :class:`PageSnapshot` against the current page state
to detect concurrent modifications.  Used by the client to enforce the
``on_conflict`` policy before applying diff operations.
"""

from __future__ import annotations

from datetime import datetime

from notionify.models import PageSnapshot


def take_snapshot(page_id: str, page: dict, blocks: list[dict]) -> PageSnapshot:
    """Build a snapshot from a fetched page object and its children.

    Parameters
    ----------
    page_id:
        The Notion page ID.
    page:
        The full page object as returned by
        :meth:`PageAPI.retrieve`.
    blocks:
        The top-level child blocks of the page.

    Returns
    -------
    PageSnapshot
    """
    last_edited_str = page.get("last_edited_time", "")
    if last_edited_str:
        try:
            last_edited = datetime.fromisoformat(last_edited_str.replace("Z", "+00:00"))
        except ValueError:
            last_edited = datetime.min
    else:
        last_edited = datetime.min
    block_etags: dict[str, str] = {}
    for block in blocks:
        block_id = block.get("id", "")
        edited = block.get("last_edited_time", "")
        if block_id and edited:
            block_etags[block_id] = edited

    return PageSnapshot(
        page_id=page_id,
        last_edited=last_edited,
        block_etags=block_etags,
    )


def detect_conflict(snapshot: PageSnapshot, current: PageSnapshot) -> bool:
    """Return ``True`` if the page has been modified since *snapshot*.

    A conflict is detected when:

    * The page-level ``last_edited`` timestamps differ, **or**
    * Any block present in the snapshot has a different ``last_edited_time``
      in the current state (indicating a concurrent edit to that block).

    Parameters
    ----------
    snapshot:
        The snapshot taken before the diff was computed.
    current:
        A fresh snapshot of the same page.

    Returns
    -------
    bool
    """
    return snapshot.last_edited != current.last_edited or any(
        snapshot.block_etags.get(bid) != current.block_etags.get(bid)
        for bid in snapshot.block_etags
    )
