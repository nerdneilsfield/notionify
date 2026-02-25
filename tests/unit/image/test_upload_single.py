"""Tests for the single-part upload flow.

Covers both sync (upload_single) and async (async_upload_single) paths,
including success paths, API error propagation, and missing-key scenarios.

PRD hardening: image upload resilience, iteration 16.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notionify.image.upload_single import async_upload_single, upload_single

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file_api(
    upload_id: str = "upload-abc",
    upload_url: str = "https://files.notion.so/upload/abc",
) -> MagicMock:
    api = MagicMock()
    api.create_upload.return_value = {"id": upload_id, "upload_url": upload_url}
    api.send_part.return_value = None
    return api


def _make_async_file_api(
    upload_id: str = "upload-abc",
    upload_url: str = "https://files.notion.so/upload/abc",
) -> MagicMock:
    api = MagicMock()
    api.create_upload = AsyncMock(
        return_value={"id": upload_id, "upload_url": upload_url}
    )
    api.send_part = AsyncMock(return_value=None)
    return api


# =========================================================================
# Sync: happy path
# =========================================================================


class TestUploadSingleSync:
    """Synchronous single-part upload tests."""

    def test_success_returns_upload_id(self):
        api = _make_file_api(upload_id="upload-123")
        result = upload_single(api, "img.png", "image/png", b"bytes")
        assert result == "upload-123"

    def test_create_upload_called_with_correct_params(self):
        api = _make_file_api()
        upload_single(api, "photo.jpg", "image/jpeg", b"data")
        api.create_upload.assert_called_once_with(
            name="photo.jpg",
            content_type="image/jpeg",
            mode="single_part",
        )

    def test_send_part_called_with_upload_url_and_data(self):
        url = "https://files.notion.so/upload/xyz"
        api = _make_file_api(upload_url=url)
        data = b"image data here"
        upload_single(api, "img.png", "image/png", data)
        api.send_part.assert_called_once_with(url, data, "image/png")

    def test_send_part_called_after_create_upload(self):
        """Verify ordering: create_upload first, then send_part."""
        call_order: list[str] = []
        api = MagicMock()
        api.create_upload.side_effect = lambda **kw: call_order.append("create") or {
            "id": "u1", "upload_url": "http://example.com"
        }
        api.send_part.side_effect = lambda *a: call_order.append("send")
        upload_single(api, "img.png", "image/png", b"x")
        assert call_order == ["create", "send"]


# =========================================================================
# Sync: failure paths
# =========================================================================


class TestUploadSingleSyncFailures:
    """Sync: error propagation from API calls."""

    def test_create_upload_exception_propagates(self):
        api = MagicMock()
        api.create_upload.side_effect = RuntimeError("API unreachable")
        with pytest.raises(RuntimeError, match="API unreachable"):
            upload_single(api, "img.png", "image/png", b"data")

    def test_create_upload_returns_missing_id_raises_key_error(self):
        api = MagicMock()
        api.create_upload.return_value = {"upload_url": "https://example.com"}
        with pytest.raises(KeyError):
            upload_single(api, "img.png", "image/png", b"data")

    def test_create_upload_returns_missing_upload_url_raises_key_error(self):
        api = MagicMock()
        api.create_upload.return_value = {"id": "upload-123"}
        with pytest.raises(KeyError):
            upload_single(api, "img.png", "image/png", b"data")

    def test_send_part_exception_propagates(self):
        api = _make_file_api()
        api.send_part.side_effect = OSError("Network error")
        with pytest.raises(OSError, match="Network error"):
            upload_single(api, "img.png", "image/png", b"data")

    def test_send_part_raises_after_create_upload_already_called(self):
        """If send_part fails, create_upload was already called (orphaned upload)."""
        api = _make_file_api()
        api.send_part.side_effect = ConnectionError("Dropped")
        with pytest.raises(ConnectionError):
            upload_single(api, "img.png", "image/png", b"data")
        api.create_upload.assert_called_once()

    def test_timeout_from_send_part_propagates(self):
        api = _make_file_api()
        api.send_part.side_effect = TimeoutError("Request timed out")
        with pytest.raises(TimeoutError, match="Request timed out"):
            upload_single(api, "img.png", "image/png", b"data")


# =========================================================================
# Async: happy path
# =========================================================================


class TestUploadSingleAsync:
    """Asynchronous single-part upload tests."""

    async def test_success_returns_upload_id(self):
        api = _make_async_file_api(upload_id="async-upload-1")
        result = await async_upload_single(api, "img.png", "image/png", b"bytes")
        assert result == "async-upload-1"

    async def test_create_upload_called_with_correct_params(self):
        api = _make_async_file_api()
        await async_upload_single(api, "banner.gif", "image/gif", b"data")
        api.create_upload.assert_awaited_once_with(
            name="banner.gif",
            content_type="image/gif",
            mode="single_part",
        )

    async def test_send_part_called_with_upload_url_and_data(self):
        url = "https://files.notion.so/upload/async-xyz"
        api = _make_async_file_api(upload_url=url)
        data = b"async image bytes"
        await async_upload_single(api, "img.png", "image/png", data)
        api.send_part.assert_awaited_once_with(url, data, "image/png")


# =========================================================================
# Async: failure paths
# =========================================================================


class TestUploadSingleAsyncFailures:
    """Async: error propagation from API calls."""

    async def test_create_upload_exception_propagates(self):
        api = _make_async_file_api()
        api.create_upload.side_effect = RuntimeError("Async API down")
        with pytest.raises(RuntimeError, match="Async API down"):
            await async_upload_single(api, "img.png", "image/png", b"data")

    async def test_create_upload_missing_id_raises_key_error(self):
        api = _make_async_file_api()
        api.create_upload.return_value = {"upload_url": "https://example.com"}
        with pytest.raises(KeyError):
            await async_upload_single(api, "img.png", "image/png", b"data")

    async def test_create_upload_missing_upload_url_raises_key_error(self):
        api = _make_async_file_api()
        api.create_upload.return_value = {"id": "upload-123"}
        with pytest.raises(KeyError):
            await async_upload_single(api, "img.png", "image/png", b"data")

    async def test_send_part_exception_propagates(self):
        api = _make_async_file_api()
        api.send_part.side_effect = OSError("Async network error")
        with pytest.raises(OSError, match="Async network error"):
            await async_upload_single(api, "img.png", "image/png", b"data")

    async def test_send_part_raises_after_create_upload_called(self):
        """Async: send_part failure leaves orphaned upload slot."""
        api = _make_async_file_api()
        api.send_part.side_effect = ConnectionError("Async dropped")
        with pytest.raises(ConnectionError):
            await async_upload_single(api, "img.png", "image/png", b"data")
        api.create_upload.assert_awaited_once()

    async def test_timeout_from_send_part_propagates(self):
        api = _make_async_file_api()
        api.send_part.side_effect = TimeoutError("Async timeout")
        with pytest.raises(TimeoutError, match="Async timeout"):
            await async_upload_single(api, "img.png", "image/png", b"data")
