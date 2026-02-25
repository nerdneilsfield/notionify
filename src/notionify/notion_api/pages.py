"""Page API wrappers for the Notion API.

Provides :class:`PageAPI` (sync) and :class:`AsyncPageAPI` (async) thin
wrappers around the Notion ``/pages`` endpoints.  Both delegate all HTTP
concerns (auth, retries, rate limiting) to the underlying transport.

PRD reference: section 14.1.
"""

from __future__ import annotations

from typing import Any

from .transport import AsyncNotionTransport, NotionTransport


class PageAPI:
    """Synchronous wrapper for the Notion Pages API.

    Parameters
    ----------
    transport:
        A configured :class:`NotionTransport` instance.
    """

    def __init__(self, transport: NotionTransport) -> None:
        self._transport = transport

    def create(
        self,
        parent: dict[str, Any],
        properties: dict[str, Any],
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a new page.

        Parameters
        ----------
        parent:
            Parent object, e.g. ``{"page_id": "..."}`` or
            ``{"database_id": "..."}``.
        properties:
            Page properties.  For pages under another page the minimal
            required shape is
            ``{"title": [{"text": {"content": "Page title"}}]}``.
        children:
            Optional list of block objects to append as page content.
            Notion accepts up to 100 children per create call; for larger
            payloads the caller should chunk and use
            :meth:`BlockAPI.append_children`.

        Returns
        -------
        dict
            The created page object as returned by the Notion API.
        """
        body: dict[str, Any] = {
            "parent": parent,
            "properties": properties,
        }
        if children is not None:
            body["children"] = children
        return self._transport.request("POST", "/pages", json=body)

    def retrieve(self, page_id: str) -> dict[str, Any]:
        """Retrieve a page by its ID.

        Parameters
        ----------
        page_id:
            The UUID of the page to retrieve (with or without hyphens).

        Returns
        -------
        dict
            The full page object.
        """
        return self._transport.request("GET", f"/pages/{page_id}")

    def update(
        self,
        page_id: str,
        properties: dict[str, Any] | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        """Update a page's properties or archive status.

        Parameters
        ----------
        page_id:
            The UUID of the page to update.
        properties:
            Updated property values.  Only specified properties are changed;
            omitted properties are left untouched.
        archived:
            Set to ``True`` to archive (soft-delete) the page, or ``False``
            to un-archive it.

        Returns
        -------
        dict
            The updated page object.
        """
        body: dict[str, Any] = {}
        if properties is not None:
            body["properties"] = properties
        if archived is not None:
            body["archived"] = archived
        return self._transport.request("PATCH", f"/pages/{page_id}", json=body)


class AsyncPageAPI:
    """Asynchronous wrapper for the Notion Pages API.

    Mirrors :class:`PageAPI` but all methods are coroutines.

    Parameters
    ----------
    transport:
        A configured :class:`AsyncNotionTransport` instance.
    """

    def __init__(self, transport: AsyncNotionTransport) -> None:
        self._transport = transport

    async def create(
        self,
        parent: dict[str, Any],
        properties: dict[str, Any],
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a new page (async).

        See :meth:`PageAPI.create` for parameter documentation.
        """
        body: dict[str, Any] = {
            "parent": parent,
            "properties": properties,
        }
        if children is not None:
            body["children"] = children
        return await self._transport.request("POST", "/pages", json=body)

    async def retrieve(self, page_id: str) -> dict[str, Any]:
        """Retrieve a page by its ID (async).

        See :meth:`PageAPI.retrieve` for parameter documentation.
        """
        return await self._transport.request("GET", f"/pages/{page_id}")

    async def update(
        self,
        page_id: str,
        properties: dict[str, Any] | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        """Update a page's properties or archive status (async).

        See :meth:`PageAPI.update` for parameter documentation.
        """
        body: dict[str, Any] = {}
        if properties is not None:
            body["properties"] = properties
        if archived is not None:
            body["archived"] = archived
        return await self._transport.request("PATCH", f"/pages/{page_id}", json=body)
