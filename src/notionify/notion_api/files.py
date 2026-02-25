"""File upload API wrappers for the Notion API.

Provides :class:`FileAPI` (sync) and :class:`AsyncFileAPI` (async) wrappers
for the Notion file-upload lifecycle:

1. **Create upload** -- reserve an upload slot and get an upload URL.
2. **Send part(s)** -- upload raw bytes (single-part or multi-part).
3. **Complete upload** -- finalise a multi-part upload.
4. **Retrieve upload** -- poll the upload's status.

PRD reference: section 14.3.
"""

from __future__ import annotations

from typing import Any

from .transport import AsyncNotionTransport, NotionTransport


class FileAPI:
    """Synchronous wrapper for the Notion File Uploads API.

    Parameters
    ----------
    transport:
        A configured :class:`NotionTransport` instance.
    """

    def __init__(self, transport: NotionTransport) -> None:
        self._transport = transport

    def create_upload(
        self,
        name: str,
        content_type: str,
        mode: str = "single_part",
    ) -> dict[str, Any]:
        """Create a new file upload.

        Parameters
        ----------
        name:
            The file name (e.g. ``"photo.png"``).
        content_type:
            MIME type of the file (e.g. ``"image/png"``).
        mode:
            Upload mode: ``"single_part"`` (default) or ``"multi_part"``.

        Returns
        -------
        dict
            The upload object, including ``id`` and ``upload_url``.
        """
        body: dict[str, Any] = {
            "name": name,
            "content_type": content_type,
            "mode": mode,
        }
        return self._transport.request("POST", "/file-uploads", json=body)

    def send_part(
        self,
        upload_url: str,
        data: bytes,
        content_type: str,
    ) -> dict[str, Any] | None:
        """Upload raw bytes to an upload URL.

        For single-part uploads the *upload_url* is the URL returned by
        :meth:`create_upload`.  For multi-part uploads each part has its
        own URL.

        Parameters
        ----------
        upload_url:
            The full URL to ``PUT`` the bytes to.
        data:
            Raw file bytes.
        content_type:
            MIME type matching the original ``create_upload`` call.

        Returns
        -------
        dict | None
            The response body (if any).  Single-part uploads typically
            return the completed upload object; multi-part uploads may
            return ``None`` (204 No Content).
        """
        return self._transport.request(
            "PUT",
            upload_url,
            content=data,
            headers={"Content-Type": content_type},
        )

    def complete_upload(
        self,
        upload_id: str,
        parts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Complete a multi-part upload.

        Parameters
        ----------
        upload_id:
            The UUID of the upload to complete.
        parts:
            List of part descriptors as returned by the Notion API when each
            part was uploaded.

        Returns
        -------
        dict
            The completed upload object.
        """
        body: dict[str, Any] = {"parts": parts}
        return self._transport.request(
            "POST",
            f"/file-uploads/{upload_id}/complete",
            json=body,
        )

    def retrieve_upload(self, upload_id: str) -> dict[str, Any]:
        """Retrieve the current status of a file upload.

        Parameters
        ----------
        upload_id:
            The UUID of the upload.

        Returns
        -------
        dict
            The upload object including its current ``status``.
        """
        return self._transport.request("GET", f"/file-uploads/{upload_id}")


class AsyncFileAPI:
    """Asynchronous wrapper for the Notion File Uploads API.

    Mirrors :class:`FileAPI` but all methods are coroutines.

    Parameters
    ----------
    transport:
        A configured :class:`AsyncNotionTransport` instance.
    """

    def __init__(self, transport: AsyncNotionTransport) -> None:
        self._transport = transport

    async def create_upload(
        self,
        name: str,
        content_type: str,
        mode: str = "single_part",
    ) -> dict[str, Any]:
        """Create a new file upload (async).

        See :meth:`FileAPI.create_upload` for parameter documentation.
        """
        body: dict[str, Any] = {
            "name": name,
            "content_type": content_type,
            "mode": mode,
        }
        return await self._transport.request("POST", "/file-uploads", json=body)

    async def send_part(
        self,
        upload_url: str,
        data: bytes,
        content_type: str,
    ) -> dict[str, Any] | None:
        """Upload raw bytes to an upload URL (async).

        See :meth:`FileAPI.send_part` for parameter documentation.
        """
        return await self._transport.request(
            "PUT",
            upload_url,
            content=data,
            headers={"Content-Type": content_type},
        )

    async def complete_upload(
        self,
        upload_id: str,
        parts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Complete a multi-part upload (async).

        See :meth:`FileAPI.complete_upload` for parameter documentation.
        """
        body: dict[str, Any] = {"parts": parts}
        return await self._transport.request(
            "POST",
            f"/file-uploads/{upload_id}/complete",
            json=body,
        )

    async def retrieve_upload(self, upload_id: str) -> dict[str, Any]:
        """Retrieve the current status of a file upload (async).

        See :meth:`FileAPI.retrieve_upload` for parameter documentation.
        """
        return await self._transport.request("GET", f"/file-uploads/{upload_id}")
