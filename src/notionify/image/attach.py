"""Build Notion image block payloads for external URLs and uploaded files.

These helper functions produce the dict structures that the Notion API
expects when creating image blocks, either referencing an external URL
or a previously uploaded file.
"""

from __future__ import annotations

from typing import Any


def build_image_block_external(url: str) -> dict[str, Any]:
    """Build a Notion image block dict for an external URL.

    Parameters
    ----------
    url:
        The external image URL (must be ``http://`` or ``https://``).

    Returns
    -------
    dict
        A Notion block payload ready to be sent via ``append_children``.
    """
    return {
        "type": "image",
        "image": {
            "type": "external",
            "external": {
                "url": url,
            },
        },
    }


def build_image_block_uploaded(upload_id: str) -> dict[str, Any]:
    """Build a Notion image block dict referencing an uploaded file.

    The ``upload_id`` must correspond to a completed upload that has not
    yet expired (the Notion API gives a limited window for attaching an
    upload to a block).

    Parameters
    ----------
    upload_id:
        The UUID of the completed file upload.

    Returns
    -------
    dict
        A Notion block payload ready to be sent via ``append_children``.
    """
    return {
        "type": "image",
        "image": {
            "type": "file_upload",
            "file_upload": {
                "id": upload_id,
            },
        },
    }
