"""Image size boundary tests.

Verifies that validate_image enforces image_max_size_bytes correctly
at and around the configured limit, using data-URI sources with valid
PNG signatures.
"""

from __future__ import annotations

import base64

import pytest

from notionify.config import NotionifyConfig
from notionify.errors import NotionifyImageSizeError
from notionify.image.validate import validate_image
from notionify.models import ImageSourceType

# ── Helpers ────────────────────────────────────────────────────────────

# Minimal PNG signature (8 bytes) used as header for all test payloads.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _make_config(**kwargs) -> NotionifyConfig:
    """Create a test config with sensible defaults."""
    return NotionifyConfig(token="test-token", **kwargs)


def _make_png_data(total_size: int) -> bytes:
    """Build a byte string of exactly *total_size* bytes with a PNG header.

    The first 8 bytes are the PNG signature; the rest is zero-padding.
    *total_size* must be >= 8 (the length of the PNG signature).
    """
    assert total_size >= len(_PNG_SIGNATURE), "total_size must be >= 8"
    return _PNG_SIGNATURE + b"\x00" * (total_size - len(_PNG_SIGNATURE))


def _make_png_data_uri(total_size: int) -> str:
    """Build a ``data:image/png;base64,...`` URI whose decoded payload is
    exactly *total_size* bytes.
    """
    raw = _make_png_data(total_size)
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{b64}"


# =========================================================================
# Boundary tests
# =========================================================================


class TestImageSizeBoundaries:
    """Tests that exercise the exact boundary of image_max_size_bytes."""

    def test_image_under_5mb_accepted(self):
        """A small valid PNG data URI passes the default 5 MiB limit."""
        data_uri = _make_png_data_uri(256)
        mime, data = validate_image(
            data_uri, ImageSourceType.DATA_URI, None, _make_config()
        )
        assert mime == "image/png"
        assert data is not None
        assert len(data) == 256

    def test_image_exactly_at_max_size_accepted(self):
        """Data exactly equal to image_max_size_bytes is accepted.

        Uses a small custom limit (512 bytes) for speed.
        """
        limit = 512
        config = _make_config(image_max_size_bytes=limit)
        data_uri = _make_png_data_uri(limit)

        mime, data = validate_image(
            data_uri, ImageSourceType.DATA_URI, None, config
        )
        assert mime == "image/png"
        assert data is not None
        assert len(data) == limit

    def test_image_over_max_size_rejected(self):
        """Data 1 byte over image_max_size_bytes raises NotionifyImageSizeError.

        Uses a small custom limit (512 bytes) for speed.
        """
        limit = 512
        config = _make_config(image_max_size_bytes=limit)
        data_uri = _make_png_data_uri(limit + 1)

        with pytest.raises(NotionifyImageSizeError) as exc_info:
            validate_image(data_uri, ImageSourceType.DATA_URI, None, config)

        assert exc_info.value.context["size_bytes"] == limit + 1
        assert exc_info.value.context["max_bytes"] == limit

    def test_custom_max_size_1kb(self):
        """image_max_size_bytes=1024; a 1025-byte image is rejected."""
        config = _make_config(image_max_size_bytes=1024)
        data_uri = _make_png_data_uri(1025)

        with pytest.raises(NotionifyImageSizeError) as exc_info:
            validate_image(data_uri, ImageSourceType.DATA_URI, None, config)

        assert exc_info.value.context["size_bytes"] == 1025
        assert exc_info.value.context["max_bytes"] == 1024

    def test_custom_max_size_1kb_under(self):
        """image_max_size_bytes=1024; a 1000-byte image is accepted."""
        config = _make_config(image_max_size_bytes=1024)
        data_uri = _make_png_data_uri(1000)

        mime, data = validate_image(
            data_uri, ImageSourceType.DATA_URI, None, config
        )
        assert mime == "image/png"
        assert data is not None
        assert len(data) == 1000
