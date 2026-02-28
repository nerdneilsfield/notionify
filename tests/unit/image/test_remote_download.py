"""Tests for image download module (sync and async).

Covers:
- Successful download returns bytes and content type
- Retry logic on transient failures
- NotionifyImageDownloadError after exhausting retries
- Custom headers merging
- Content-Type parsing (stripping charset params)
- Default Chrome User-Agent header
- Raw HTTP helpers via respx transport mocking

PRD hardening: remote image upload feature, iteration 29.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from notionify.config import NotionifyConfig
from notionify.errors import NotionifyImageDownloadError
from notionify.image.download import (
    DEFAULT_REMOTE_IMAGE_HEADERS,
    _async_try_download,
    _build_headers,
    _is_retryable,
    _parse_content_type,
    _try_download,
    async_download_image,
    download_image,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(**kwargs: object) -> NotionifyConfig:
    defaults = {
        "token": "test-token",
        "remote_image_upload": True,
        "remote_image_timeout_seconds": 5.0,
        "remote_image_retries": 2,
    }
    defaults.update(kwargs)
    return NotionifyConfig(**defaults)  # type: ignore[arg-type]


def _fake_response(
    status_code: int = 200,
    content: bytes = b"PNG_DATA",
    content_type: str = "image/png",
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=content,
        headers={"content-type": content_type},
    )


# =========================================================================
# Header building
# =========================================================================


class TestBuildHeaders:
    """_build_headers merges defaults with user headers."""

    def test_default_headers_include_user_agent(self):
        config = _config()
        headers = _build_headers(config)
        assert "User-Agent" in headers
        assert "Chrome" in headers["User-Agent"]

    def test_user_headers_override_default(self):
        config = _config(remote_image_headers={"User-Agent": "MyBot/1.0"})
        headers = _build_headers(config)
        assert headers["User-Agent"] == "MyBot/1.0"

    def test_user_headers_extend_defaults(self):
        config = _config(remote_image_headers={"Referer": "https://example.com"})
        headers = _build_headers(config)
        assert headers["Referer"] == "https://example.com"
        assert "User-Agent" in headers  # default still present

    def test_no_user_headers(self):
        config = _config(remote_image_headers=None)
        headers = _build_headers(config)
        assert headers == DEFAULT_REMOTE_IMAGE_HEADERS


# =========================================================================
# Content-Type parsing
# =========================================================================


class TestParseContentType:
    """_parse_content_type strips parameters from Content-Type."""

    def test_simple_mime(self):
        resp = _fake_response(content_type="image/png")
        assert _parse_content_type(resp) == "image/png"

    def test_mime_with_charset(self):
        resp = _fake_response(content_type="image/jpeg; charset=utf-8")
        assert _parse_content_type(resp) == "image/jpeg"

    def test_mime_with_boundary(self):
        resp = _fake_response(content_type="image/webp; boundary=something")
        assert _parse_content_type(resp) == "image/webp"

    def test_missing_content_type_defaults(self):
        resp = httpx.Response(200, content=b"data")
        assert _parse_content_type(resp) == "application/octet-stream"


# =========================================================================
# Sync download
# =========================================================================


class TestSyncDownload:
    """Synchronous download_image function."""

    @patch("notionify.image.download._try_download")
    def test_success_on_first_attempt(self, mock_try: MagicMock):
        mock_try.return_value = (b"PNG_DATA", "image/png")
        config = _config()
        data, ct = download_image("https://example.com/img.png", config)
        assert data == b"PNG_DATA"
        assert ct == "image/png"
        mock_try.assert_called_once()

    @patch("notionify.image.download._try_download")
    def test_success_on_retry(self, mock_try: MagicMock):
        mock_try.side_effect = [
            httpx.ConnectError("fail"),
            (b"PNG_DATA", "image/png"),
        ]
        config = _config(remote_image_retries=2)
        data, ct = download_image("https://example.com/img.png", config)
        assert data == b"PNG_DATA"
        assert mock_try.call_count == 2

    @patch("notionify.image.download._try_download")
    def test_raises_after_exhausting_retries(self, mock_try: MagicMock):
        mock_try.side_effect = httpx.ConnectError("always fail")
        config = _config(remote_image_retries=1)
        with pytest.raises(NotionifyImageDownloadError, match="Failed to download"):
            download_image("https://example.com/img.png", config)
        # 1 retry + 1 initial = 2 attempts
        assert mock_try.call_count == 2

    @patch("notionify.image.download._try_download")
    def test_zero_retries_single_attempt(self, mock_try: MagicMock):
        mock_try.side_effect = httpx.ConnectError("fail")
        config = _config(remote_image_retries=0)
        with pytest.raises(NotionifyImageDownloadError):
            download_image("https://example.com/img.png", config)
        assert mock_try.call_count == 1

    @patch("notionify.image.download._try_download")
    def test_error_context_contains_url(self, mock_try: MagicMock):
        mock_try.side_effect = httpx.ConnectError("fail")
        config = _config(remote_image_retries=0)
        with pytest.raises(NotionifyImageDownloadError) as exc_info:
            download_image("https://example.com/img.png", config)
        assert exc_info.value.context["url"] == "https://example.com/img.png"

    @patch("notionify.image.download._try_download")
    def test_os_error_is_retried(self, mock_try: MagicMock):
        mock_try.side_effect = [
            OSError("network unreachable"),
            (b"OK", "image/jpeg"),
        ]
        config = _config(remote_image_retries=1)
        data, _ = download_image("https://example.com/x.jpg", config)
        assert data == b"OK"


# =========================================================================
# Async download
# =========================================================================


class TestAsyncDownload:
    """Asynchronous async_download_image function."""

    @patch("notionify.image.download._async_try_download")
    async def test_success_on_first_attempt(self, mock_try: MagicMock):
        from unittest.mock import AsyncMock

        mock_try.side_effect = AsyncMock(return_value=(b"PNG", "image/png"))
        config = _config()
        data, ct = await async_download_image("https://example.com/img.png", config)
        assert data == b"PNG"
        assert ct == "image/png"

    @patch("notionify.image.download._async_try_download")
    async def test_raises_after_exhausting_retries(self, mock_try: MagicMock):
        from unittest.mock import AsyncMock

        mock_try.side_effect = AsyncMock(side_effect=httpx.ConnectError("fail"))
        config = _config(remote_image_retries=0)
        with pytest.raises(NotionifyImageDownloadError):
            await async_download_image("https://example.com/img.png", config)

    @patch("notionify.image.download._async_try_download")
    async def test_success_on_retry(self, mock_try: MagicMock):
        from unittest.mock import AsyncMock

        mock_try.side_effect = AsyncMock(
            side_effect=[httpx.ConnectError("fail"), (b"OK", "image/png")]
        )
        config = _config(remote_image_retries=1)
        data, _ = await async_download_image("https://example.com/img.png", config)
        assert data == b"OK"


# =========================================================================
# Config validation
# =========================================================================


class TestRemoteImageConfigValidation:
    """Config fields for remote image upload are validated."""

    def test_remote_image_timeout_must_be_positive(self):
        with pytest.raises(ValueError, match="remote_image_timeout_seconds"):
            _config(remote_image_timeout_seconds=0)

    def test_remote_image_timeout_negative(self):
        with pytest.raises(ValueError, match="remote_image_timeout_seconds"):
            _config(remote_image_timeout_seconds=-1.0)

    def test_remote_image_retries_negative(self):
        with pytest.raises(ValueError, match="remote_image_retries"):
            _config(remote_image_retries=-1)

    def test_remote_image_retries_zero_is_valid(self):
        config = _config(remote_image_retries=0)
        assert config.remote_image_retries == 0

    def test_defaults_are_sensible(self):
        config = NotionifyConfig(token="test")
        assert config.remote_image_upload is False
        assert config.remote_image_headers is None
        assert config.remote_image_timeout_seconds == 20.0
        assert config.remote_image_retries == 3


# =========================================================================
# Retryable classification
# =========================================================================


class TestIsRetryable:
    """_is_retryable distinguishes transient from permanent errors."""

    def test_404_is_not_retryable(self):
        resp = httpx.Response(404, request=httpx.Request("GET", "https://x.com/img"))
        exc = httpx.HTTPStatusError("Not Found", request=resp.request, response=resp)
        assert _is_retryable(exc) is False

    def test_403_is_not_retryable(self):
        resp = httpx.Response(403, request=httpx.Request("GET", "https://x.com/img"))
        exc = httpx.HTTPStatusError("Forbidden", request=resp.request, response=resp)
        assert _is_retryable(exc) is False

    def test_500_is_retryable(self):
        resp = httpx.Response(500, request=httpx.Request("GET", "https://x.com/img"))
        exc = httpx.HTTPStatusError("Server Error", request=resp.request, response=resp)
        assert _is_retryable(exc) is True

    def test_502_is_retryable(self):
        resp = httpx.Response(502, request=httpx.Request("GET", "https://x.com/img"))
        exc = httpx.HTTPStatusError("Bad Gateway", request=resp.request, response=resp)
        assert _is_retryable(exc) is True

    def test_connect_error_is_retryable(self):
        assert _is_retryable(httpx.ConnectError("fail")) is True

    def test_timeout_is_retryable(self):
        assert _is_retryable(httpx.ReadTimeout("timeout")) is True

    def test_os_error_is_retryable(self):
        assert _is_retryable(OSError("network")) is True


# =========================================================================
# Early abort on permanent errors
# =========================================================================


class TestPermanentErrorAbort:
    """4xx errors abort immediately without exhausting retries."""

    @patch("notionify.image.download._try_download")
    def test_404_aborts_immediately_sync(self, mock_try: MagicMock):
        resp = httpx.Response(404, request=httpx.Request("GET", "https://x.com/img"))
        mock_try.side_effect = httpx.HTTPStatusError(
            "Not Found", request=resp.request, response=resp,
        )
        config = _config(remote_image_retries=5)
        with pytest.raises(NotionifyImageDownloadError, match="1 attempt"):
            download_image("https://x.com/img", config)
        assert mock_try.call_count == 1  # no retries

    @patch("notionify.image.download._try_download")
    def test_500_retries_sync(self, mock_try: MagicMock):
        resp = httpx.Response(500, request=httpx.Request("GET", "https://x.com/img"))
        mock_try.side_effect = httpx.HTTPStatusError(
            "Server Error", request=resp.request, response=resp,
        )
        config = _config(remote_image_retries=2)
        with pytest.raises(NotionifyImageDownloadError, match="3 attempt"):
            download_image("https://x.com/img", config)
        assert mock_try.call_count == 3  # 1 initial + 2 retries

    @patch("notionify.image.download._async_try_download")
    async def test_403_aborts_immediately_async(self, mock_try: MagicMock):
        from unittest.mock import AsyncMock

        resp = httpx.Response(403, request=httpx.Request("GET", "https://x.com/img"))
        mock_try.side_effect = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Forbidden", request=resp.request, response=resp,
            ),
        )
        config = _config(remote_image_retries=5)
        with pytest.raises(NotionifyImageDownloadError, match="1 attempt"):
            await async_download_image("https://x.com/img", config)
        assert mock_try.call_count == 1


# =========================================================================
# Raw HTTP helpers (via respx)
# =========================================================================


class TestTryDownloadSync:
    """_try_download exercises the real httpx.Client code path."""

    @respx.mock
    def test_success_returns_bytes_and_content_type(self):
        respx.get("https://example.com/photo.jpg").mock(
            return_value=httpx.Response(
                200, content=b"JPEG_BYTES",
                headers={"content-type": "image/jpeg"},
            ),
        )
        data, ct = _try_download(
            "https://example.com/photo.jpg", {"User-Agent": "test"}, 10.0,
        )
        assert data == b"JPEG_BYTES"
        assert ct == "image/jpeg"

    @respx.mock
    def test_raises_on_http_error(self):
        respx.get("https://example.com/missing.png").mock(
            return_value=httpx.Response(404),
        )
        with pytest.raises(httpx.HTTPStatusError):
            _try_download(
                "https://example.com/missing.png", {}, 10.0,
            )

    @respx.mock
    def test_follows_redirects(self):
        respx.get("https://example.com/redir").mock(
            return_value=httpx.Response(
                200, content=b"REDIRECTED",
                headers={"content-type": "image/png"},
            ),
        )
        data, ct = _try_download("https://example.com/redir", {}, 10.0)
        assert data == b"REDIRECTED"


class TestAsyncTryDownload:
    """_async_try_download exercises the real httpx.AsyncClient code path."""

    @respx.mock
    async def test_success_returns_bytes_and_content_type(self):
        respx.get("https://example.com/photo.jpg").mock(
            return_value=httpx.Response(
                200, content=b"ASYNC_JPEG",
                headers={"content-type": "image/jpeg; charset=utf-8"},
            ),
        )
        data, ct = await _async_try_download(
            "https://example.com/photo.jpg", {"User-Agent": "test"}, 10.0,
        )
        assert data == b"ASYNC_JPEG"
        assert ct == "image/jpeg"

    @respx.mock
    async def test_raises_on_http_error(self):
        respx.get("https://example.com/gone.png").mock(
            return_value=httpx.Response(403),
        )
        with pytest.raises(httpx.HTTPStatusError):
            await _async_try_download(
                "https://example.com/gone.png", {}, 10.0,
            )
