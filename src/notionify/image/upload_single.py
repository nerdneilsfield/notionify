"""Single-part upload flow for the Notion File Uploads API.

Handles the simple case where an entire image fits in one PUT request.
"""

from __future__ import annotations


def upload_single(
    file_api,
    name: str,
    content_type: str,
    data: bytes,
) -> str:
    """Upload a file in a single part.

    1. Create an upload slot via ``file_api.create_upload``.
    2. PUT the bytes to the returned upload URL.

    Parameters
    ----------
    file_api:
        A :class:`FileAPI` instance.
    name:
        File name (e.g. ``"image.png"``).
    content_type:
        MIME type (e.g. ``"image/png"``).
    data:
        Raw file bytes.

    Returns
    -------
    str
        The upload ID returned by the Notion API.
    """
    upload = file_api.create_upload(
        name=name,
        content_type=content_type,
        mode="single_part",
    )
    upload_id: str = upload["id"]
    upload_url: str = upload["upload_url"]

    file_api.send_part(upload_url, data, content_type)

    return upload_id


async def async_upload_single(
    file_api,
    name: str,
    content_type: str,
    data: bytes,
) -> str:
    """Upload a file in a single part (async).

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

    Returns
    -------
    str
        The upload ID returned by the Notion API.
    """
    upload = await file_api.create_upload(
        name=name,
        content_type=content_type,
        mode="single_part",
    )
    upload_id: str = upload["id"]
    upload_url: str = upload["upload_url"]

    await file_api.send_part(upload_url, data, content_type)

    return upload_id
