"""Block API wrappers for the Notion API.

Provides :class:`BlockAPI` (sync) and :class:`AsyncBlockAPI` (async) thin
wrappers around the Notion ``/blocks`` endpoints.  The ``get_children``
method auto-paginates to retrieve all children of a block in a single call.

PRD reference: section 14.2.
"""

from __future__ import annotations

from typing import Any

from .transport import AsyncNotionTransport, NotionTransport


def extract_block_ids(response: dict[str, Any]) -> list[str]:
    """Extract block IDs from an ``append_children`` API response.

    Parameters
    ----------
    response:
        The JSON dict returned by
        ``PATCH /blocks/{id}/children``.

    Returns
    -------
    list[str]
        The ``id`` values of each block in the ``results`` array.
    """
    results = response.get("results", [])
    return [r["id"] for r in results if "id" in r]


class BlockAPI:
    """Synchronous wrapper for the Notion Blocks API.

    Parameters
    ----------
    transport:
        A configured :class:`NotionTransport` instance.
    """

    def __init__(self, transport: NotionTransport) -> None:
        self._transport = transport

    def retrieve(self, block_id: str) -> dict[str, Any]:
        """Retrieve a single block by its ID.

        Parameters
        ----------
        block_id:
            The UUID of the block to retrieve.

        Returns
        -------
        dict
            The full block object.
        """
        return self._transport.request("GET", f"/blocks/{block_id}")

    def update(self, block_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update a block's content.

        Parameters
        ----------
        block_id:
            The UUID of the block to update.
        payload:
            The update payload, typically ``{block_type: {rich_text: [...], ...}}``.
            Only the fields included in the payload are modified.

        Returns
        -------
        dict
            The updated block object.
        """
        return self._transport.request("PATCH", f"/blocks/{block_id}", json=payload)

    def delete(self, block_id: str) -> dict[str, Any]:
        """Delete (archive) a block.

        Parameters
        ----------
        block_id:
            The UUID of the block to delete.

        Returns
        -------
        dict
            The archived block object.
        """
        return self._transport.request("DELETE", f"/blocks/{block_id}")

    def get_children(self, block_id: str) -> list[dict[str, Any]]:
        """Retrieve all children of a block, auto-paginating.

        Issues as many ``GET /blocks/{block_id}/children`` requests as
        necessary to fetch every child block, transparently handling
        Notion's pagination cursors.

        Parameters
        ----------
        block_id:
            The UUID of the parent block (or page).

        Returns
        -------
        list[dict]
            All child block objects in order.
        """
        return list(
            self._transport.paginate(
                f"/blocks/{block_id}/children",
                method="GET",
            )
        )

    def append_children(
        self,
        block_id: str,
        children: list[dict[str, Any]],
        after: str | None = None,
    ) -> dict[str, Any]:
        """Append child blocks to a parent block or page.

        Parameters
        ----------
        block_id:
            The UUID of the parent block (or page) to append to.
        children:
            List of block objects to append.  Notion accepts a maximum of
            100 blocks per call; for larger lists the caller should use
            :func:`notionify.utils.chunk_children` to batch.
        after:
            Optional UUID of an existing child block.  The new children
            will be inserted immediately after this block.  If ``None``,
            children are appended at the end.

        Returns
        -------
        dict
            The API response containing the appended block objects.
        """
        body: dict[str, Any] = {"children": children}
        if after is not None:
            body["after"] = after
        return self._transport.request(
            "PATCH", f"/blocks/{block_id}/children", json=body
        )


class AsyncBlockAPI:
    """Asynchronous wrapper for the Notion Blocks API.

    Mirrors :class:`BlockAPI` but all methods are coroutines.

    Parameters
    ----------
    transport:
        A configured :class:`AsyncNotionTransport` instance.
    """

    def __init__(self, transport: AsyncNotionTransport) -> None:
        self._transport = transport

    async def retrieve(self, block_id: str) -> dict[str, Any]:
        """Retrieve a single block by its ID (async).

        See :meth:`BlockAPI.retrieve` for parameter documentation.
        """
        return await self._transport.request("GET", f"/blocks/{block_id}")

    async def update(self, block_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update a block's content (async).

        See :meth:`BlockAPI.update` for parameter documentation.
        """
        return await self._transport.request("PATCH", f"/blocks/{block_id}", json=payload)

    async def delete(self, block_id: str) -> dict[str, Any]:
        """Delete (archive) a block (async).

        See :meth:`BlockAPI.delete` for parameter documentation.
        """
        return await self._transport.request("DELETE", f"/blocks/{block_id}")

    async def get_children(self, block_id: str) -> list[dict[str, Any]]:
        """Retrieve all children of a block, auto-paginating (async).

        See :meth:`BlockAPI.get_children` for parameter documentation.
        """
        return [
            item
            async for item in self._transport.paginate(
                f"/blocks/{block_id}/children",
                method="GET",
            )
        ]

    async def append_children(
        self,
        block_id: str,
        children: list[dict[str, Any]],
        after: str | None = None,
    ) -> dict[str, Any]:
        """Append child blocks to a parent block or page (async).

        See :meth:`BlockAPI.append_children` for parameter documentation.
        """
        body: dict[str, Any] = {"children": children}
        if after is not None:
            body["after"] = after
        return await self._transport.request(
            "PATCH", f"/blocks/{block_id}/children", json=body
        )
