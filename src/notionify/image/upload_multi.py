"""Multi-part upload flow for the Notion File Uploads API.

Handles large files by splitting them into chunks and uploading each
chunk as a separate part, then completing the upload.
"""

from __future__ import annotations

from typing import Any

from notionify.notion_api.files import AsyncFileAPI, FileAPI


def upload_multi(
    file_api: FileAPI,
    name: str,
    content_type: str,
    data: bytes,
    chunk_size: int = 5 * 1024 * 1024,
) -> str:
    """Upload a file in multiple parts.

    1. Create a multi-part upload slot.
    2. Split *data* into chunks of *chunk_size* bytes.
    3. PUT each chunk to its upload URL.
    4. Complete the upload.

    Parameters
    ----------
    file_api:
        A :class:`FileAPI` instance.
    name:
        File name (e.g. ``"large_image.png"``).
    content_type:
        MIME type.
    data:
        Raw file bytes.
    chunk_size:
        Maximum bytes per part.  Defaults to 5 MiB.

    Returns
    -------
    str
        The upload ID returned by the Notion API.
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")

    upload = file_api.create_upload(
        name=name,
        content_type=content_type,
        mode="multi_part",
    )
    upload_id: str = upload["id"]

    # Split data into chunks and upload each part.
    parts: list[dict[str, Any]] = []
    part_number = 1
    offset = 0

    while offset < len(data):
        chunk = data[offset : offset + chunk_size]
        offset += chunk_size

        # Each part has its own upload URL.
        # The API provides upload URLs per part via the upload object.
        upload_urls = upload.get("upload_urls", [])
        if part_number <= len(upload_urls):
            part_url = upload_urls[part_number - 1]["upload_url"]
        else:
            # Defensive fallback: use the base upload URL if individual
            # part URLs are not provided.
            part_url = upload.get("upload_url") or ""
            if not part_url:
                msg = (
                    f"Upload {upload_id} has no upload_url and insufficient "
                    f"upload_urls (need {part_number}, have {len(upload_urls)})"
                )
                raise ValueError(msg)

        result = file_api.send_part(part_url, chunk, content_type)

        part_info = {
            "part_number": part_number,
        }
        if result and isinstance(result, dict):
            part_info.update(result)
        parts.append(part_info)
        part_number += 1

    # Complete the upload.
    file_api.complete_upload(upload_id, parts)

    return upload_id


async def async_upload_multi(
    file_api: AsyncFileAPI,
    name: str,
    content_type: str,
    data: bytes,
    chunk_size: int = 5 * 1024 * 1024,
) -> str:
    """Upload a file in multiple parts (async).

    Parameters
    ----------
    file_api:
        An :class:`AsyncFileAPI` instance.
    name:
        File name.
    content_type:
        MIME type.
    data:
        Raw file bytes.
    chunk_size:
        Maximum bytes per part.  Defaults to 5 MiB.

    Returns
    -------
    str
        The upload ID returned by the Notion API.
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")

    upload = await file_api.create_upload(
        name=name,
        content_type=content_type,
        mode="multi_part",
    )
    upload_id: str = upload["id"]

    parts: list[dict[str, Any]] = []
    part_number = 1
    offset = 0

    while offset < len(data):
        chunk = data[offset : offset + chunk_size]
        offset += chunk_size

        upload_urls = upload.get("upload_urls", [])
        if part_number <= len(upload_urls):
            part_url = upload_urls[part_number - 1]["upload_url"]
        else:
            part_url = upload.get("upload_url") or ""
            if not part_url:
                msg = (
                    f"Upload {upload_id} has no upload_url and insufficient "
                    f"upload_urls (need {part_number}, have {len(upload_urls)})"
                )
                raise ValueError(msg)

        result = await file_api.send_part(part_url, chunk, content_type)

        part_info = {
            "part_number": part_number,
        }
        if result and isinstance(result, dict):
            part_info.update(result)
        parts.append(part_info)
        part_number += 1

    await file_api.complete_upload(upload_id, parts)

    return upload_id
