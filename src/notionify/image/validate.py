"""Image validation: MIME type and size checks.

Validates that an image source conforms to the configured MIME-type
allowlists and maximum-size constraints before the upload pipeline
processes it.
"""

from __future__ import annotations

import base64
import mimetypes
import re

from notionify.config import NotionifyConfig
from notionify.errors import (
    NotionifyImageParseError,
    NotionifyImageSizeError,
    NotionifyImageTypeError,
)
from notionify.models import ImageSourceType

# Regex to parse data URIs: data:[<mediatype>][;base64],<data>
_DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[^;,]+)?(?:;(?P<encoding>base64))?,(?P<data>.*)$",
    re.IGNORECASE | re.DOTALL,
)

# Map of magic bytes to MIME types for sniffing.
_MAGIC_BYTES: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # RIFF....WEBP (check further)
    (b"<svg", "image/svg+xml"),
    (b"<?xml", "image/svg+xml"),  # SVG can start with XML declaration
    (b"BM", "image/bmp"),
]


def _sniff_mime(data: bytes) -> str | None:
    """Attempt to detect MIME type from the first bytes of image data."""
    for magic, mime in _MAGIC_BYTES:
        if data[:len(magic)] == magic:
            # Extra check for WEBP: RIFF....WEBP
            if magic == b"RIFF" and len(data) >= 12:
                if data[8:12] != b"WEBP":
                    continue
            return mime
    return None


def _guess_mime_from_path(src: str) -> str | None:
    """Guess MIME type from a file path or URL using the extension."""
    mime_type, _ = mimetypes.guess_type(src)
    return mime_type


def validate_image(
    src: str,
    source_type: ImageSourceType,
    data: bytes | None,
    config: NotionifyConfig,
) -> tuple[str, bytes | None]:
    """Validate an image's MIME type and size.

    For data-URI sources, the data is decoded from base64 and returned.
    For local files, *data* should already contain the file bytes.
    For external URLs, MIME type is guessed from the URL extension and
    *data* is ``None`` (no bytes to validate size against unless the
    caller pre-fetched them).

    Parameters
    ----------
    src:
        The raw image source string.
    source_type:
        The detected source type.
    data:
        Raw image bytes (for local files and data URIs after decoding).
        ``None`` for external URLs that have not been fetched.
    config:
        SDK configuration with MIME allowlists and size limits.

    Returns
    -------
    tuple[str, bytes | None]
        ``(mime_type, decoded_bytes_or_none)`` -- the validated MIME type
        and the image bytes (decoded from base64 for data URIs, passed
        through for local files, ``None`` for external URLs).

    Raises
    ------
    NotionifyImageTypeError
        If the detected MIME type is not in the configured allowlist.
    NotionifyImageSizeError
        If the image data exceeds ``config.image_max_size_bytes``.
    NotionifyImageParseError
        If a data URI cannot be decoded.
    """
    mime_type: str | None = None
    decoded_data: bytes | None = data

    if source_type == ImageSourceType.DATA_URI:
        mime_type, decoded_data = _parse_data_uri(src)
    elif source_type == ImageSourceType.LOCAL_FILE:
        # Try sniffing from data first, fall back to extension.
        if data:
            mime_type = _sniff_mime(data)
        if not mime_type:
            mime_type = _guess_mime_from_path(src)
    elif source_type == ImageSourceType.EXTERNAL_URL:
        mime_type = _guess_mime_from_path(src)
    else:
        # Unknown source type -- try guessing from path.
        mime_type = _guess_mime_from_path(src)

    if not mime_type:
        mime_type = "application/octet-stream"

    # Validate MIME type against the appropriate allowlist.
    if source_type == ImageSourceType.EXTERNAL_URL:
        allowed = config.image_allowed_mimes_external
    else:
        allowed = config.image_allowed_mimes_upload

    if mime_type not in allowed:
        raise NotionifyImageTypeError(
            message=f"Image MIME type {mime_type!r} is not allowed",
            context={
                "src": _truncate_src(src),
                "detected_mime": mime_type,
                "allowed_mimes": allowed,
            },
        )

    # Validate size.
    if decoded_data is not None and len(decoded_data) > config.image_max_size_bytes:
        raise NotionifyImageSizeError(
            message=(
                f"Image size {len(decoded_data)} bytes exceeds "
                f"maximum {config.image_max_size_bytes} bytes"
            ),
            context={
                "src": _truncate_src(src),
                "size_bytes": len(decoded_data),
                "max_bytes": config.image_max_size_bytes,
            },
        )

    return mime_type, decoded_data


def _parse_data_uri(src: str) -> tuple[str, bytes]:
    """Parse a data URI and return (mime_type, decoded_bytes).

    Raises
    ------
    NotionifyImageParseError
        If the data URI is malformed or cannot be decoded.
    """
    match = _DATA_URI_RE.match(src)
    if not match:
        raise NotionifyImageParseError(
            message="Invalid data URI format",
            context={"src": _truncate_src(src), "reason": "regex_no_match"},
        )

    mime_type = match.group("mime") or "application/octet-stream"
    encoding = match.group("encoding")
    raw_data = match.group("data")

    if encoding and encoding.lower() == "base64":
        try:
            decoded = base64.b64decode(raw_data, validate=True)
        except Exception as exc:
            raise NotionifyImageParseError(
                message="Failed to decode base64 data URI",
                context={"src": _truncate_src(src), "reason": "base64_decode_error"},
                cause=exc,
            ) from exc
    else:
        # URL-encoded or plain text data.
        try:
            from urllib.parse import unquote_to_bytes
            decoded = unquote_to_bytes(raw_data)
        except Exception as exc:
            raise NotionifyImageParseError(
                message="Failed to decode data URI payload",
                context={"src": _truncate_src(src), "reason": "url_decode_error"},
                cause=exc,
            ) from exc

    return mime_type, decoded


def _truncate_src(src: str, max_len: int = 200) -> str:
    """Truncate a source string for inclusion in error context."""
    if len(src) <= max_len:
        return src
    return src[:max_len] + "..."
