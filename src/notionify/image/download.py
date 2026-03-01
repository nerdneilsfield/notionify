"""Download remote images for re-upload to Notion.

Provides sync and async helpers that download an image from an external
URL into memory, applying configurable headers, timeout, and retry
logic.  On success the raw bytes and detected content-type are returned;
on failure a :class:`NotionifyImageDownloadError` is raised so that
callers can fall back to embedding the original URL.

Client errors (4xx) are treated as permanent and abort immediately
without retries.  Server errors (5xx), timeouts, and connection failures
are retried with linear backoff.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

from notionify.config import NotionifyConfig
from notionify.errors import NotionifyImageDownloadError
from notionify.observability import get_logger

log = get_logger("notionify.image.download")

# ---------------------------------------------------------------------------
# Default headers
# ---------------------------------------------------------------------------

DEFAULT_REMOTE_IMAGE_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    ),
}


def _build_headers(config: NotionifyConfig) -> dict[str, str]:
    """Merge default and user-supplied remote image headers."""
    headers = dict(DEFAULT_REMOTE_IMAGE_HEADERS)
    if config.remote_image_headers:
        headers.update(config.remote_image_headers)
    return headers


def _parse_content_type(response: httpx.Response) -> str:
    """Extract the MIME type from the Content-Type header."""
    raw: str = response.headers.get("content-type", "application/octet-stream")
    return raw.split(";")[0].strip()


_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _validate_url_scheme(url: str) -> None:
    """Reject non-HTTP(S) URLs to prevent SSRF via file://, ftp://, etc."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise NotionifyImageDownloadError(
            message=f"URL scheme {parsed.scheme!r} is not allowed; only http/https are supported",
            context={"url": url, "scheme": parsed.scheme},
        )


def _is_retryable(exc: Exception) -> bool:
    """Return ``True`` if the error is transient and worth retrying.

    Client errors (4xx) are permanent — the server understood the request
    and rejected it.  Server errors (5xx), timeouts, and connection
    failures are transient.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, OSError))


# ---------------------------------------------------------------------------
# Sync download
# ---------------------------------------------------------------------------


def download_image(
    url: str,
    config: NotionifyConfig,
) -> tuple[bytes, str]:
    """Download a remote image synchronously.

    Parameters
    ----------
    url:
        The HTTP(S) URL of the image.
    config:
        SDK configuration (provides headers, timeout, retries).

    Returns
    -------
    tuple[bytes, str]
        ``(raw_bytes, content_type)`` on success.

    Raises
    ------
    NotionifyImageDownloadError
        If all attempts fail or the URL scheme is not http/https.
    """
    _validate_url_scheme(url)
    headers = _build_headers(config)
    timeout = config.remote_image_timeout_seconds
    max_attempts = config.remote_image_retries + 1  # retries + initial attempt

    last_error: Exception | None = None

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(max_attempts):
            try:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                return response.content, _parse_content_type(response)
            except (httpx.HTTPError, OSError) as exc:  # noqa: PERF203
                last_error = exc
                retryable = _is_retryable(exc)
                status_code = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else None
                )
                log.debug(
                    "Remote image download attempt failed",
                    extra={"extra_fields": {
                        "url": url,
                        "attempt": attempt + 1,
                        "max_attempts": max_attempts,
                        "status_code": status_code,
                        "retryable": retryable,
                        "error": str(exc),
                    }},
                )
                if not retryable:
                    break
                if attempt < max_attempts - 1:
                    time.sleep(min(attempt + 1, 5))

    attempts_used = attempt + 1 if last_error is not None else max_attempts
    last_status = (
        last_error.response.status_code
        if isinstance(last_error, httpx.HTTPStatusError)
        else None
    )
    is_permanent = last_status is not None and last_status < 500
    raise NotionifyImageDownloadError(
        message=f"Failed to download remote image after {attempts_used} attempt(s): {url}",
        context={
            "url": url,
            "error": str(last_error),
            "attempts_used": attempts_used,
            "max_attempts": max_attempts,
            "last_status_code": last_status,
            "is_permanent": is_permanent,
        },
    )


# ---------------------------------------------------------------------------
# Async download
# ---------------------------------------------------------------------------


async def async_download_image(
    url: str,
    config: NotionifyConfig,
) -> tuple[bytes, str]:
    """Download a remote image asynchronously.

    Parameters
    ----------
    url:
        The HTTP(S) URL of the image.
    config:
        SDK configuration (provides headers, timeout, retries).

    Returns
    -------
    tuple[bytes, str]
        ``(raw_bytes, content_type)`` on success.

    Raises
    ------
    NotionifyImageDownloadError
        If all attempts fail or the URL scheme is not http/https.
    """
    import asyncio

    _validate_url_scheme(url)
    headers = _build_headers(config)
    timeout = config.remote_image_timeout_seconds
    max_attempts = config.remote_image_retries + 1

    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(max_attempts):
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.content, _parse_content_type(response)
            except (httpx.HTTPError, OSError) as exc:  # noqa: PERF203
                last_error = exc
                retryable = _is_retryable(exc)
                status_code = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else None
                )
                log.debug(
                    "Remote image download attempt failed",
                    extra={"extra_fields": {
                        "url": url,
                        "attempt": attempt + 1,
                        "max_attempts": max_attempts,
                        "status_code": status_code,
                        "retryable": retryable,
                        "error": str(exc),
                    }},
                )
                if not retryable:
                    break
                if attempt < max_attempts - 1:
                    await asyncio.sleep(min(attempt + 1, 5))

    attempts_used = attempt + 1 if last_error is not None else max_attempts
    last_status = (
        last_error.response.status_code
        if isinstance(last_error, httpx.HTTPStatusError)
        else None
    )
    is_permanent = last_status is not None and last_status < 500
    raise NotionifyImageDownloadError(
        message=f"Failed to download remote image after {attempts_used} attempt(s): {url}",
        context={
            "url": url,
            "error": str(last_error),
            "attempts_used": attempts_used,
            "max_attempts": max_attempts,
            "last_status_code": last_status,
            "is_permanent": is_permanent,
        },
    )
