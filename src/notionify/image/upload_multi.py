"""Multi-part upload flow for the Notion File Uploads API.

Handles large files by splitting them into chunks and uploading each
chunk as a separate part, then completing the upload.
"""

from __future__ import annotations

from typing import Any


def upload_multi(
    file_api: Any,
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
            # If the API doesn't pre-generate enough URLs, use the base
            # pattern.  This is a defensive fallback.
            part_url = f"{upload.get('upload_url', '')}"

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
    file_api: Any,
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
            part_url = f"{upload.get('upload_url', '')}"

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
