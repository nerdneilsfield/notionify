"""Sync and async HTTP transports for the Notion API.

Each transport handles the full request lifecycle described in PRD section 16.4:

1. Acquire a token-bucket slot (wait if needed).
2. Send the HTTP request with auth and version headers.
3. On ``2xx`` -- return the parsed JSON response.
4. On ``429`` -- extract ``Retry-After``, sleep, and retry.
5. On ``5xx`` / network error -- exponential backoff and retry.
6. On non-retryable ``4xx`` -- raise the appropriate typed error immediately.
7. On max attempts exceeded -- raise :class:`NotionifyRetryExhaustedError`.
"""

from __future__ import annotations

import json as _json
import sys
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from notionify.config import NotionifyConfig
from notionify.errors import (
    NotionifyAuthError,
    NotionifyDiffConflictError,
    NotionifyNetworkError,
    NotionifyNotFoundError,
    NotionifyPermissionError,
    NotionifyRetryExhaustedError,
    NotionifyValidationError,
)
from notionify.observability import NoopMetricsHook, get_logger

from .rate_limit import AsyncTokenBucket, TokenBucket
from .retries import _RETRYABLE_STATUSES, compute_backoff, should_retry

log = get_logger("notionify.transport")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_retry_after(response: httpx.Response) -> float | None:
    """Extract the ``Retry-After`` header value as a float, or ``None``."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _raise_for_status(response: httpx.Response, method: str, path: str) -> None:
    """Raise the appropriate :class:`NotionifyError` subclass for 4xx codes
    that should **not** be retried.
    """
    status = response.status_code
    try:
        body = response.json()
    except (ValueError, KeyError):
        body = {}

    notion_message = body.get("message", response.text[:500])
    notion_code = body.get("code", "")

    if status == 400:
        raise NotionifyValidationError(
            message=f"Validation error on {method} {path}: {notion_message}",
            context={"status_code": status, "notion_code": notion_code, "body": body},
        )
    if status == 401:
        raise NotionifyAuthError(
            message=f"Authentication failed on {method} {path}: {notion_message}",
            context={"status_code": status, "notion_code": notion_code},
        )
    if status == 403:
        raise NotionifyPermissionError(
            message=f"Permission denied on {method} {path}: {notion_message}",
            context={
                "status_code": status,
                "notion_code": notion_code,
                "operation": f"{method} {path}",
            },
        )
    if status == 404:
        raise NotionifyNotFoundError(
            message=f"Resource not found on {method} {path}: {notion_message}",
            context={"status_code": status, "notion_code": notion_code, "path": path},
        )
    if status == 409:
        raise NotionifyDiffConflictError(
            message=f"Conflict on {method} {path}: {notion_message}",
            context={"status_code": status, "notion_code": notion_code},
        )

    # Generic client error -- raise as validation error.
    raise NotionifyValidationError(
        message=f"Client error {status} on {method} {path}: {notion_message}",
        context={"status_code": status, "notion_code": notion_code, "body": body},
    )


def _dump_payload(
    method: str,
    url: str,
    payload: dict | None,
    response_status: int | None,
    response_body: Any | None,
    token: str | None = None,
) -> None:
    """Write a redacted debug dump of the request/response to stderr."""
    from notionify.utils.redact import redact

    dump: dict[str, Any] = {
        "method": method,
        "url": url,
    }
    if payload is not None:
        dump["request_body"] = payload
    if response_status is not None:
        dump["response_status"] = response_status
    if response_body is not None:
        dump["response_body"] = response_body
    safe_dump = redact(dump, token)
    print(
        _json.dumps(safe_dump, indent=2, default=str),
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Shared request helpers (used by both sync and async transports)
# ---------------------------------------------------------------------------

def _handle_network_exception(
    config: NotionifyConfig,
    metrics: Any,
    method: str,
    path: str,
    exc: Exception,
    attempt: int,
) -> float:
    """Handle a network error during a request attempt.

    Returns the backoff delay (seconds) if the request should be retried.
    Raises :class:`NotionifyNetworkError` if retries are exhausted.
    """
    max_attempts = config.retry_max_attempts
    metrics.increment(
        "notionify.requests_total",
        tags={"method": method, "path": path, "status": "error"},
    )
    log.warning(
        "Request network error",
        extra={
            "extra_fields": {
                "op": "request",
                "method": method,
                "path": path,
                "attempt": attempt + 1,
                "error": str(exc),
            }
        },
    )
    if should_retry(None, exc, attempt, max_attempts):
        delay = compute_backoff(
            attempt,
            base=config.retry_base_delay,
            maximum=config.retry_max_delay,
            jitter=config.retry_jitter,
        )
        metrics.increment(
            "notionify.retries_total",
            tags={"method": method, "path": path, "reason": "network_error"},
        )
        return delay
    raise NotionifyNetworkError(
        message=f"Network error on {method} {path}: {exc}",
        context={"url": path, "attempt": attempt + 1},
        cause=exc,
    ) from exc


def _emit_debug_dump(
    config: NotionifyConfig,
    method: str,
    response: httpx.Response,
    json_payload: Any,
) -> None:
    """Emit a redacted debug dump of request/response if enabled."""
    if not config.debug_dump_payload:
        return
    try:
        resp_body = response.json()
    except (ValueError, KeyError):
        resp_body = response.text[:1000]
    _dump_payload(
        method, str(response.url), json_payload,
        response.status_code, resp_body,
        token=config.token,
    )


# ---------------------------------------------------------------------------
# Sync transport
# ---------------------------------------------------------------------------

class NotionTransport:
    """Synchronous HTTP transport with auth, retry, and rate limiting.

    Parameters
    ----------
    config:
        A :class:`NotionifyConfig` instance controlling all transport behaviour.
    """

    def __init__(self, config: NotionifyConfig) -> None:
        self._config = config
        self._bucket = TokenBucket(
            rate_rps=config.rate_limit_rps,
            burst=10,
        )
        self._metrics = config.metrics if config.metrics is not None else NoopMetricsHook()

        proxy: httpx.URL | str | None = config.http_proxy
        self._client = httpx.Client(
            base_url=config.base_url,
            headers={
                "Authorization": f"Bearer {config.token}",
                "Notion-Version": config.notion_version,
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(config.timeout_seconds),
            proxy=proxy,
        )

    # -- public API --------------------------------------------------------

    def request(self, method: str, path: str, **kwargs: Any) -> dict:
        """Execute an HTTP request against the Notion API.

        Handles the full lifecycle: rate-limit pacing, sending, retries on
        429/5xx/network errors, and raising typed errors for 4xx.

        Parameters
        ----------
        method:
            HTTP method (``GET``, ``POST``, ``PATCH``, ``PUT``, ``DELETE``).
        path:
            API path relative to ``base_url`` (e.g. ``/pages``).
        **kwargs:
            Forwarded to :meth:`httpx.Client.request`.  Use ``json=`` for
            JSON bodies, ``content=`` for raw bytes, ``params=`` for query
            strings, and ``headers=`` for per-request header overrides.

        Returns
        -------
        dict
            Parsed JSON response body.

        Raises
        ------
        NotionifyAuthError
            On 401 responses.
        NotionifyPermissionError
            On 403 responses.
        NotionifyNotFoundError
            On 404 responses.
        NotionifyValidationError
            On 400 and other non-retryable 4xx responses.
        NotionifyDiffConflictError
            On 409 responses.
        NotionifyRetryExhaustedError
            When all retry attempts have been exhausted.
        NotionifyNetworkError
            On transport-level failures after exhausting retries.
        """
        max_attempts = self._config.retry_max_attempts
        last_exception: Exception | None = None
        last_status: int | None = None

        json_payload = kwargs.get("json")

        for attempt in range(max_attempts):
            # 1. Rate-limit pacing
            wait = self._bucket.acquire()
            if wait > 0:
                self._metrics.timing(
                    "notionify.rate_limit_wait_ms",
                    wait * 1000,
                    tags={"method": method, "path": path},
                )

            # 2. Send request
            t0 = time.monotonic()
            try:
                response = self._client.request(method, path, **kwargs)
                elapsed_ms = (time.monotonic() - t0) * 1000
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exception = exc
                last_status = None
                delay = _handle_network_exception(
                    self._config, self._metrics, method, path, exc, attempt,
                )
                time.sleep(delay)
                continue

            # 3. Process response
            last_status = response.status_code
            last_exception = None

            self._metrics.increment(
                "notionify.requests_total",
                tags={"method": method, "path": path, "status": str(response.status_code)},
            )
            self._metrics.timing(
                "notionify.request_duration_ms",
                elapsed_ms,
                tags={"method": method, "path": path, "status": str(response.status_code)},
            )

            _emit_debug_dump(self._config, method, response, json_payload)

            # 3a. Success
            if 200 <= response.status_code < 300:
                # Some endpoints return 204 with no body.
                if response.status_code == 204 or not response.content:
                    return {}
                result: dict = response.json()
                return result

            # 3b. Is this a retryable status code?
            is_retryable = response.status_code in _RETRYABLE_STATUSES

            # 3c. Non-retryable error -- raise immediately.
            if not is_retryable:
                _raise_for_status(response, method, path)

            # 3d. Retryable, but can we still retry?
            if not should_retry(response.status_code, None, attempt, max_attempts):
                # Exhausted -- will fall through to the bottom.
                break

            # 3e. Prepare and execute retry.
            retry_after: float | None = None
            reason = "server_error"

            if response.status_code == 429:
                retry_after = _parse_retry_after(response)
                reason = "rate_limited"
                self._metrics.increment(
                    "notionify.rate_limited_total",
                    tags={"method": method, "path": path},
                )
                log.warning(
                    "Rate limited by Notion API",
                    extra={
                        "extra_fields": {
                            "op": "request",
                            "method": method,
                            "path": path,
                            "status_code": 429,
                            "retry_after": retry_after,
                            "attempt": attempt + 1,
                        }
                    },
                )

            delay = compute_backoff(
                attempt,
                base=self._config.retry_base_delay,
                maximum=self._config.retry_max_delay,
                jitter=self._config.retry_jitter,
                retry_after=retry_after,
            )
            self._metrics.increment(
                "notionify.retries_total",
                tags={"method": method, "path": path, "reason": reason},
            )
            time.sleep(delay)
            continue

        # 4. All attempts exhausted.
        ctx: dict[str, Any] = {
            "attempts": max_attempts,
            "last_status_code": last_status,
        }
        if last_exception is not None:
            raise NotionifyRetryExhaustedError(
                message=(
                    f"All {max_attempts} attempts exhausted for {method} {path} "
                    f"(last error: {last_exception})"
                ),
                context=ctx,
                cause=last_exception,
            )
        raise NotionifyRetryExhaustedError(
            message=(
                f"All {max_attempts} attempts exhausted for {method} {path} "
                f"(last status: {last_status})"
            ),
            context=ctx,
        )

    def paginate(self, path: str, **kwargs: Any) -> Iterator[dict]:
        """Auto-paginate a Notion list endpoint, yielding each result item.

        Issues repeated ``POST`` requests (or ``GET`` if ``method`` is
        passed in *kwargs*) with ``start_cursor`` / ``page_size`` until
        ``has_more`` is ``False``.

        Parameters
        ----------
        path:
            API path to paginate (e.g. ``/blocks/{id}/children``).
        **kwargs:
            Forwarded to :meth:`request`.  A ``json`` dict will have
            ``start_cursor`` and ``page_size`` merged in automatically.
            If you need to use query parameters for a ``GET`` endpoint,
            pass ``params=`` and specify ``method="GET"``.

        Yields
        ------
        dict
            Individual result objects from each page.
        """
        method = kwargs.pop("method", "GET")
        cursor: str | None = None

        while True:
            # Merge pagination params into the correct location.
            if method.upper() in ("POST", "PATCH"):
                json_body: dict = kwargs.get("json", {}) or {}
                json_body["page_size"] = 100
                if cursor is not None:
                    json_body["start_cursor"] = cursor
                else:
                    json_body.pop("start_cursor", None)
                kwargs["json"] = json_body
            else:
                params: dict = kwargs.get("params", {}) or {}
                params["page_size"] = 100
                if cursor is not None:
                    params["start_cursor"] = cursor
                else:
                    params.pop("start_cursor", None)
                kwargs["params"] = params

            data = self.request(method, path, **kwargs)
            yield from data.get("results", [])

            if not data.get("has_more", False):
                break
            cursor = data.get("next_cursor")
            if cursor is None:
                break

    def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        self._client.close()

    def __enter__(self) -> NotionTransport:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Async transport
# ---------------------------------------------------------------------------

class AsyncNotionTransport:
    """Asynchronous HTTP transport with auth, retry, and rate limiting.

    Mirrors :class:`NotionTransport` but uses ``httpx.AsyncClient`` and
    ``asyncio.sleep`` for non-blocking I/O.

    Parameters
    ----------
    config:
        A :class:`NotionifyConfig` instance controlling all transport behaviour.
    """

    def __init__(self, config: NotionifyConfig) -> None:
        self._config = config
        self._bucket = AsyncTokenBucket(
            rate_rps=config.rate_limit_rps,
            burst=10,
        )
        self._metrics = config.metrics if config.metrics is not None else NoopMetricsHook()

        proxy: httpx.URL | str | None = config.http_proxy
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={
                "Authorization": f"Bearer {config.token}",
                "Notion-Version": config.notion_version,
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(config.timeout_seconds),
            proxy=proxy,
        )

    # -- public API --------------------------------------------------------

    async def request(self, method: str, path: str, **kwargs: Any) -> dict:
        """Execute an HTTP request against the Notion API (async).

        See :meth:`NotionTransport.request` for full documentation; the
        semantics are identical but all blocking calls are replaced with
        async equivalents.
        """
        import asyncio

        max_attempts = self._config.retry_max_attempts
        last_exception: Exception | None = None
        last_status: int | None = None

        json_payload = kwargs.get("json")

        for attempt in range(max_attempts):
            # 1. Rate-limit pacing
            wait = await self._bucket.acquire()
            if wait > 0:
                self._metrics.timing(
                    "notionify.rate_limit_wait_ms",
                    wait * 1000,
                    tags={"method": method, "path": path},
                )

            # 2. Send request
            t0 = time.monotonic()
            try:
                response = await self._client.request(method, path, **kwargs)
                elapsed_ms = (time.monotonic() - t0) * 1000
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exception = exc
                last_status = None
                delay = _handle_network_exception(
                    self._config, self._metrics, method, path, exc, attempt,
                )
                await asyncio.sleep(delay)
                continue

            # 3. Process response
            last_status = response.status_code
            last_exception = None

            self._metrics.increment(
                "notionify.requests_total",
                tags={"method": method, "path": path, "status": str(response.status_code)},
            )
            self._metrics.timing(
                "notionify.request_duration_ms",
                elapsed_ms,
                tags={"method": method, "path": path, "status": str(response.status_code)},
            )

            _emit_debug_dump(self._config, method, response, json_payload)

            # 3a. Success
            if 200 <= response.status_code < 300:
                if response.status_code == 204 or not response.content:
                    return {}
                result: dict = response.json()
                return result

            # 3b. Is this a retryable status code?
            is_retryable = response.status_code in _RETRYABLE_STATUSES

            # 3c. Non-retryable error -- raise immediately.
            if not is_retryable:
                _raise_for_status(response, method, path)

            # 3d. Retryable, but can we still retry?
            if not should_retry(response.status_code, None, attempt, max_attempts):
                # Exhausted -- will fall through to the bottom.
                break

            # 3e. Prepare and execute retry.
            retry_after: float | None = None
            reason = "server_error"

            if response.status_code == 429:
                retry_after = _parse_retry_after(response)
                reason = "rate_limited"
                self._metrics.increment(
                    "notionify.rate_limited_total",
                    tags={"method": method, "path": path},
                )
                log.warning(
                    "Rate limited by Notion API",
                    extra={
                        "extra_fields": {
                            "op": "request",
                            "method": method,
                            "path": path,
                            "status_code": 429,
                            "retry_after": retry_after,
                            "attempt": attempt + 1,
                        }
                    },
                )

            delay = compute_backoff(
                attempt,
                base=self._config.retry_base_delay,
                maximum=self._config.retry_max_delay,
                jitter=self._config.retry_jitter,
                retry_after=retry_after,
            )
            self._metrics.increment(
                "notionify.retries_total",
                tags={"method": method, "path": path, "reason": reason},
            )
            await asyncio.sleep(delay)
            continue

        # 4. All attempts exhausted.
        ctx: dict[str, Any] = {
            "attempts": max_attempts,
            "last_status_code": last_status,
        }
        if last_exception is not None:
            raise NotionifyRetryExhaustedError(
                message=(
                    f"All {max_attempts} attempts exhausted for {method} {path} "
                    f"(last error: {last_exception})"
                ),
                context=ctx,
                cause=last_exception,
            )
        raise NotionifyRetryExhaustedError(
            message=(
                f"All {max_attempts} attempts exhausted for {method} {path} "
                f"(last status: {last_status})"
            ),
            context=ctx,
        )

    async def paginate(self, path: str, **kwargs: Any) -> AsyncIterator[dict]:
        """Auto-paginate a Notion list endpoint, yielding each result item.

        Async equivalent of :meth:`NotionTransport.paginate`.
        """
        method = kwargs.pop("method", "GET")
        cursor: str | None = None

        while True:
            if method.upper() in ("POST", "PATCH"):
                json_body: dict = kwargs.get("json", {}) or {}
                json_body["page_size"] = 100
                if cursor is not None:
                    json_body["start_cursor"] = cursor
                else:
                    json_body.pop("start_cursor", None)
                kwargs["json"] = json_body
            else:
                params: dict = kwargs.get("params", {}) or {}
                params["page_size"] = 100
                if cursor is not None:
                    params["start_cursor"] = cursor
                else:
                    params.pop("start_cursor", None)
                kwargs["params"] = params

            data = await self.request(method, path, **kwargs)
            for item in data.get("results", []):
                yield item

            if not data.get("has_more", False):
                break
            cursor = data.get("next_cursor")
            if cursor is None:
                break

    async def close(self) -> None:
        """Close the underlying async HTTP client and release resources."""
        await self._client.aclose()

    async def __aenter__(self) -> AsyncNotionTransport:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
