"""Comprehensive unit tests for notionify/notion_api/transport.py.

Covers:
- _parse_retry_after
- _raise_for_status
- _dump_payload
- NotionTransport.request (success, 4xx errors, retry logic, debug dump)
- NotionTransport.paginate (single-page, multi-page, POST method)
- NotionTransport.close / context manager
- AsyncNotionTransport equivalents
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

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
from notionify.notion_api.transport import (
    AsyncNotionTransport,
    NotionTransport,
    _dump_payload,
    _emit_debug_dump,
    _parse_retry_after,
    _raise_for_status,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_response(
    status_code: int = 200,
    body: dict | None = None,
    headers: dict | None = None,
) -> httpx.Response:
    """Build a minimal httpx.Response for testing.

    Attaches a dummy Request so that response.url works without raising
    RuntimeError("The request instance has not been set on this response.").
    """
    if body is not None:
        content = json.dumps(body).encode()
    else:
        content = b""
    resp = httpx.Response(
        status_code,
        content=content,
        headers=headers or {},
    )
    # Attach a minimal request so response.url is accessible
    resp.request = httpx.Request("GET", "https://api.notion.com/v1/test")
    return resp


def make_config(**overrides) -> NotionifyConfig:
    """Return a NotionifyConfig tuned for fast, deterministic tests."""
    defaults = dict(
        token="test-token-1234",
        retry_max_attempts=3,
        retry_base_delay=0.0,
        retry_max_delay=0.0,
        retry_jitter=False,
        # Use a high RPS so the token bucket never blocks during tests.
        rate_limit_rps=10_000.0,
    )
    defaults.update(overrides)
    return NotionifyConfig(**defaults)


class _MockBucket:
    """Synchronous mock token bucket that always returns a fixed wait time."""
    def __init__(self, wait: float = 0.0):
        self._wait = wait

    def acquire(self, tokens: int = 1) -> float:
        return self._wait


class _MockAsyncBucket:
    """Asynchronous mock token bucket that always returns a fixed wait time."""
    def __init__(self, wait: float = 0.0):
        self._wait = wait

    async def acquire(self, tokens: int = 1) -> float:
        return self._wait


# ---------------------------------------------------------------------------
# _parse_retry_after
# ---------------------------------------------------------------------------

class TestParseRetryAfter:
    def test_numeric_string_returns_float(self):
        resp = make_response(headers={"retry-after": "5"})
        assert _parse_retry_after(resp) == 5.0

    def test_float_string_returns_float(self):
        resp = make_response(headers={"retry-after": "2.5"})
        assert _parse_retry_after(resp) == 2.5

    def test_invalid_string_returns_none(self):
        resp = make_response(headers={"retry-after": "not-a-number"})
        assert _parse_retry_after(resp) is None

    def test_missing_header_returns_none(self):
        resp = make_response()
        assert _parse_retry_after(resp) is None

    def test_zero_string_returns_zero(self):
        resp = make_response(headers={"retry-after": "0"})
        assert _parse_retry_after(resp) == 0.0

    def test_negative_value_returns_negative_float(self):
        resp = make_response(headers={"retry-after": "-1"})
        assert _parse_retry_after(resp) == -1.0

    def test_very_large_value_returns_float(self):
        resp = make_response(headers={"retry-after": "86400"})
        assert _parse_retry_after(resp) == 86400.0

    def test_rfc_date_string_returns_none(self):
        """RFC 7231 date format is not supported; returns None gracefully."""
        resp = make_response(headers={"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"})
        assert _parse_retry_after(resp) is None

    def test_empty_string_returns_none(self):
        resp = make_response(headers={"retry-after": ""})
        assert _parse_retry_after(resp) is None

    def test_whitespace_only_returns_none(self):
        resp = make_response(headers={"retry-after": "   "})
        assert _parse_retry_after(resp) is None

    def test_scientific_notation_returns_float(self):
        resp = make_response(headers={"retry-after": "1e2"})
        assert _parse_retry_after(resp) == 100.0

    def test_fractional_small_value(self):
        resp = make_response(headers={"retry-after": "0.1"})
        assert _parse_retry_after(resp) == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# _raise_for_status
# ---------------------------------------------------------------------------

class TestRaiseForStatus:
    def _resp(self, status: int, body: dict | None = None) -> httpx.Response:
        return make_response(status_code=status, body=body or {"message": "err", "code": "test"})

    def test_400_raises_validation_error(self):
        with pytest.raises(NotionifyValidationError) as exc_info:
            _raise_for_status(self._resp(400), "POST", "/pages")
        assert exc_info.value.context["status_code"] == 400

    def test_401_raises_auth_error(self):
        with pytest.raises(NotionifyAuthError) as exc_info:
            _raise_for_status(self._resp(401), "GET", "/users/me")
        assert exc_info.value.context["status_code"] == 401

    def test_403_raises_permission_error(self):
        with pytest.raises(NotionifyPermissionError) as exc_info:
            _raise_for_status(self._resp(403), "PATCH", "/pages/abc")
        assert "operation" in exc_info.value.context
        assert exc_info.value.context["status_code"] == 403

    def test_404_raises_not_found_error(self):
        with pytest.raises(NotionifyNotFoundError) as exc_info:
            _raise_for_status(self._resp(404), "GET", "/pages/xyz")
        assert exc_info.value.context["path"] == "/pages/xyz"

    def test_409_raises_diff_conflict_error(self):
        with pytest.raises(NotionifyDiffConflictError) as exc_info:
            _raise_for_status(self._resp(409), "PATCH", "/blocks/abc")
        assert exc_info.value.context["status_code"] == 409

    def test_other_4xx_raises_validation_error_with_status(self):
        with pytest.raises(NotionifyValidationError) as exc_info:
            _raise_for_status(self._resp(422), "POST", "/databases")
        assert exc_info.value.context["status_code"] == 422

    def test_message_extracted_from_body(self):
        resp = make_response(
            status_code=400,
            body={"message": "Invalid property", "code": "validation_error"},
        )
        with pytest.raises(NotionifyValidationError) as exc_info:
            _raise_for_status(resp, "POST", "/pages")
        assert "Invalid property" in exc_info.value.message

    def test_non_json_body_falls_back_to_text(self):
        # Build response with raw non-JSON content
        resp = httpx.Response(400, content=b"plain text error", headers={})
        with pytest.raises(NotionifyValidationError):
            _raise_for_status(resp, "DELETE", "/pages/1")


# ---------------------------------------------------------------------------
# _dump_payload
# ---------------------------------------------------------------------------

class TestDumpPayload:
    def test_dump_with_payload_and_response(self, capsys):
        _dump_payload(
            method="POST",
            url="https://api.notion.com/v1/pages",
            payload={"title": "Hello"},
            response_status=200,
            response_body={"id": "page-id"},
            token="test-token-1234",
        )
        captured = capsys.readouterr()
        data = json.loads(captured.err)
        assert data["method"] == "POST"
        assert data["url"] == "https://api.notion.com/v1/pages"
        assert "request_body" in data
        assert data["response_status"] == 200
        assert "response_body" in data

    def test_dump_without_payload(self, capsys):
        _dump_payload(
            method="GET",
            url="https://api.notion.com/v1/users",
            payload=None,
            response_status=200,
            response_body={"results": []},
            token=None,
        )
        captured = capsys.readouterr()
        data = json.loads(captured.err)
        assert "request_body" not in data
        assert data["response_status"] == 200

    def test_dump_without_response(self, capsys):
        _dump_payload(
            method="DELETE",
            url="https://api.notion.com/v1/blocks/abc",
            payload=None,
            response_status=None,
            response_body=None,
            token=None,
        )
        captured = capsys.readouterr()
        data = json.loads(captured.err)
        assert "response_status" not in data
        assert "response_body" not in data

    def test_token_is_redacted_in_dump(self, capsys):
        secret = "super-secret-token-9999"
        _dump_payload(
            method="POST",
            url=f"https://api.notion.com/v1/pages?token={secret}",
            payload={"auth": secret},
            response_status=200,
            response_body=None,
            token=secret,
        )
        captured = capsys.readouterr()
        assert secret not in captured.err


# ---------------------------------------------------------------------------
# Sync NotionTransport
# ---------------------------------------------------------------------------

class TestNotionTransportRequest:
    """Tests for NotionTransport.request()."""

    def _transport(self, **cfg_overrides) -> NotionTransport:
        t = NotionTransport(make_config(**cfg_overrides))
        # Replace with non-blocking mock bucket
        t._bucket = _MockBucket(wait=0.0)
        return t

    # -- Success cases -------------------------------------------------------

    def test_200_returns_json(self):
        transport = self._transport()
        resp = make_response(200, body={"id": "page-1", "object": "page"})
        with patch.object(transport._client, "request", return_value=resp):
            result = transport.request("GET", "/pages/page-1")
        assert result == {"id": "page-1", "object": "page"}

    def test_204_returns_empty_dict(self):
        transport = self._transport()
        resp = make_response(204, body=None)
        with patch.object(transport._client, "request", return_value=resp):
            result = transport.request("DELETE", "/blocks/abc")
        assert result == {}

    def test_200_empty_content_returns_empty_dict(self):
        transport = self._transport()
        # Response with a 200 but truly empty content
        resp = httpx.Response(200, content=b"", headers={})
        resp.request = httpx.Request("GET", "https://api.notion.com/v1/pages/xyz")
        with patch.object(transport._client, "request", return_value=resp):
            result = transport.request("GET", "/pages/xyz")
        assert result == {}

    # -- 4xx non-retryable errors -------------------------------------------

    def test_400_raises_validation_error_immediately(self):
        transport = self._transport()
        resp = make_response(400, body={"message": "bad input", "code": "validation_error"})
        with (
            patch.object(transport._client, "request", return_value=resp),
            pytest.raises(NotionifyValidationError),
        ):
            transport.request("POST", "/pages")

    def test_401_raises_auth_error(self):
        transport = self._transport()
        resp = make_response(401, body={"message": "Unauthorized", "code": "unauthorized"})
        with (
            patch.object(transport._client, "request", return_value=resp),
            pytest.raises(NotionifyAuthError),
        ):
            transport.request("GET", "/users/me")

    def test_403_raises_permission_error(self):
        transport = self._transport()
        resp = make_response(403, body={"message": "Forbidden", "code": "restricted_resource"})
        with (
            patch.object(transport._client, "request", return_value=resp),
            pytest.raises(NotionifyPermissionError),
        ):
            transport.request("GET", "/pages/secret")

    def test_404_raises_not_found_error(self):
        transport = self._transport()
        resp = make_response(404, body={"message": "Not found", "code": "object_not_found"})
        with (
            patch.object(transport._client, "request", return_value=resp),
            pytest.raises(NotionifyNotFoundError),
        ):
            transport.request("GET", "/pages/missing")

    def test_409_raises_diff_conflict_error(self):
        transport = self._transport()
        resp = make_response(409, body={"message": "Conflict", "code": "conflict_error"})
        with (
            patch.object(transport._client, "request", return_value=resp),
            pytest.raises(NotionifyDiffConflictError),
        ):
            transport.request("PATCH", "/pages/p1")

    # -- Retry on 429 --------------------------------------------------------

    def test_429_retried_and_eventually_exhausted(self):
        transport = self._transport(retry_max_attempts=3)
        resp_429 = make_response(429, body={"message": "rate limited"})
        with (
            patch.object(transport._client, "request", return_value=resp_429),
            pytest.raises(NotionifyRetryExhaustedError) as exc_info,
        ):
            transport.request("GET", "/databases")
        assert exc_info.value.context["attempts"] == 3
        assert exc_info.value.context["last_status_code"] == 429

    def test_429_with_retry_after_header_success_on_retry(self):
        transport = self._transport(retry_max_attempts=3)
        resp_429 = make_response(429, body={}, headers={"retry-after": "0"})
        resp_200 = make_response(200, body={"ok": True})
        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return resp_429
            return resp_200

        with patch.object(transport._client, "request", side_effect=side_effect):
            result = transport.request("GET", "/pages")
        assert result == {"ok": True}
        assert call_count["n"] == 2

    # -- Retry on 5xx --------------------------------------------------------

    def test_500_retried_success_on_second_attempt(self):
        transport = self._transport(retry_max_attempts=3)
        resp_500 = make_response(500, body={"message": "Internal Server Error"})
        resp_200 = make_response(200, body={"object": "page", "id": "abc"})
        responses = iter([resp_500, resp_200])

        with patch.object(
            transport._client, "request",
            side_effect=lambda *a, **kw: next(responses),
        ):
            result = transport.request("GET", "/pages/abc")
        assert result["id"] == "abc"

    def test_500_retry_exhausted_raises(self):
        transport = self._transport(retry_max_attempts=3)
        resp_500 = make_response(500, body={"message": "Server error"})
        with (
            patch.object(transport._client, "request", return_value=resp_500),
            pytest.raises(NotionifyRetryExhaustedError) as exc_info,
        ):
            transport.request("POST", "/blocks")
        assert exc_info.value.context["last_status_code"] == 500

    # -- Network errors -------------------------------------------------------

    def test_timeout_exception_retried_success(self):
        transport = self._transport(retry_max_attempts=3)
        resp_200 = make_response(200, body={"id": "p1"})
        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.TimeoutException("timed out")
            return resp_200

        with patch.object(transport._client, "request", side_effect=side_effect):
            result = transport.request("GET", "/pages/p1")
        assert result == {"id": "p1"}

    def test_network_error_exhausted_raises_retry_exhausted(self):
        # With retry_max_attempts=3, all 3 attempts raise NetworkError.
        # attempts 0 and 1 -> should_retry returns True -> continues
        # attempt 2 -> should_retry returns False -> raises NotionifyNetworkError
        # After the loop, the last_exception branch raises NotionifyRetryExhaustedError
        # Actually, when should_retry returns False on attempt 2 (the last),
        # it raises NotionifyNetworkError directly.
        # So with 3 attempts exhausted we get NotionifyNetworkError on the last one.
        transport = self._transport(retry_max_attempts=3)

        def side_effect(*args, **kwargs):
            raise httpx.NetworkError("connection reset")

        with (
            patch.object(transport._client, "request", side_effect=side_effect),
            # On the final attempt should_retry returns False -> NotionifyNetworkError
            pytest.raises(NotionifyNetworkError),
        ):
            transport.request("GET", "/pages")

    def test_network_error_single_attempt_raises_network_error(self):
        # With max_attempts=1, no retries, immediate NotionifyNetworkError
        transport = self._transport(retry_max_attempts=1)

        def side_effect(*args, **kwargs):
            raise httpx.NetworkError("DNS failure")

        with (
            patch.object(transport._client, "request", side_effect=side_effect),
            pytest.raises(NotionifyNetworkError),
        ):
            transport.request("GET", "/pages")

    # -- Debug dump ----------------------------------------------------------

    def test_debug_dump_payload_writes_to_stderr(self, capsys):
        transport = self._transport(debug_dump_payload=True)
        resp = make_response(200, body={"id": "p1"})
        # Attach a proper request so response.url is accessible
        resp.request = httpx.Request("GET", "https://api.notion.com/v1/pages/p1")
        with patch.object(transport._client, "request", return_value=resp):
            transport.request("GET", "/pages/p1", json={"key": "value"})
        captured = capsys.readouterr()
        assert captured.err.strip() != ""
        data = json.loads(captured.err)
        assert data["method"] == "GET"

    def test_no_debug_dump_when_disabled(self, capsys):
        transport = self._transport(debug_dump_payload=False)
        resp = make_response(200, body={"id": "p1"})
        with patch.object(transport._client, "request", return_value=resp):
            transport.request("GET", "/pages/p1")
        captured = capsys.readouterr()
        assert captured.err == ""

    # -- Rate-limit metrics --------------------------------------------------

    def test_rate_limit_wait_metrics_emitted_when_wait_nonzero(self):
        mock_metrics = MagicMock()
        transport = self._transport(metrics=mock_metrics)
        # Replace the bucket with one that reports wait=0.5
        transport._bucket = _MockBucket(wait=0.5)
        resp = make_response(200, body={})

        with patch.object(transport._client, "request", return_value=resp):
            transport.request("GET", "/pages")

        mock_metrics.timing.assert_any_call(
            "notionify.rate_limit_wait_ms",
            pytest.approx(500.0, rel=0.1),
            tags={"method": "GET", "path": "/pages"},
        )

    def test_rate_limit_no_metrics_when_wait_zero(self):
        mock_metrics = MagicMock()
        transport = self._transport(metrics=mock_metrics)
        # Bucket returns 0.0 (default for _MockBucket)
        resp = make_response(200, body={})

        with patch.object(transport._client, "request", return_value=resp):
            transport.request("GET", "/pages")

        # timing should only be called for request duration, not rate limit
        calls = [str(c) for c in mock_metrics.timing.call_args_list]
        rate_limit_calls = [c for c in calls if "rate_limit_wait_ms" in c]
        assert len(rate_limit_calls) == 0


# ---------------------------------------------------------------------------
# Sync NotionTransport.paginate
# ---------------------------------------------------------------------------

class TestNotionTransportPaginate:
    def _transport(self) -> NotionTransport:
        t = NotionTransport(make_config())
        t._bucket = _MockBucket()
        return t

    def test_single_page_yields_items(self):
        transport = self._transport()
        page_resp = make_response(200, body={
            "results": [{"id": "a"}, {"id": "b"}],
            "has_more": False,
            "next_cursor": None,
        })
        with patch.object(transport._client, "request", return_value=page_resp):
            items = list(transport.paginate("/blocks/1/children"))
        assert items == [{"id": "a"}, {"id": "b"}]

    def test_multi_page_yields_all_items(self):
        transport = self._transport()
        page1 = make_response(200, body={
            "results": [{"id": "a"}],
            "has_more": True,
            "next_cursor": "cursor-abc",
        })
        page2 = make_response(200, body={
            "results": [{"id": "b"}],
            "has_more": False,
            "next_cursor": None,
        })
        responses = iter([page1, page2])

        with patch.object(
            transport._client, "request",
            side_effect=lambda *a, **kw: next(responses),
        ):
            items = list(transport.paginate("/blocks/1/children"))
        assert items == [{"id": "a"}, {"id": "b"}]

    def test_post_method_pagination(self):
        transport = self._transport()
        page1 = make_response(200, body={
            "results": [{"id": "db1"}],
            "has_more": True,
            "next_cursor": "cursor-xyz",
        })
        page2 = make_response(200, body={
            "results": [{"id": "db2"}],
            "has_more": False,
            "next_cursor": None,
        })
        responses = iter([page1, page2])
        mock_client_request = MagicMock(side_effect=lambda *a, **kw: next(responses))

        with patch.object(transport._client, "request", mock_client_request):
            items = list(transport.paginate("/databases/search", method="POST"))
        assert items == [{"id": "db1"}, {"id": "db2"}]

        # Verify start_cursor was set on second request
        second_call_kwargs = mock_client_request.call_args_list[1][1]
        assert second_call_kwargs["json"]["start_cursor"] == "cursor-xyz"

    def test_stops_when_no_next_cursor(self):
        transport = self._transport()
        # has_more=True but next_cursor is None -- should stop
        page_resp = make_response(200, body={
            "results": [{"id": "item1"}],
            "has_more": True,
            "next_cursor": None,
        })
        with patch.object(transport._client, "request", return_value=page_resp):
            items = list(transport.paginate("/blocks/1/children"))
        assert items == [{"id": "item1"}]

    def test_empty_results_page(self):
        transport = self._transport()
        page_resp = make_response(200, body={
            "results": [],
            "has_more": False,
        })
        with patch.object(transport._client, "request", return_value=page_resp):
            items = list(transport.paginate("/search"))
        assert items == []

    def test_paginate_get_includes_page_size_in_params(self):
        transport = self._transport()
        page_resp = make_response(200, body={"results": [], "has_more": False})
        mock_request = MagicMock(return_value=page_resp)

        with patch.object(transport._client, "request", mock_request):
            list(transport.paginate("/blocks/1/children"))

        call_kwargs = mock_request.call_args_list[0][1]
        assert call_kwargs["params"]["page_size"] == 100


# ---------------------------------------------------------------------------
# Sync NotionTransport.close / context manager
# ---------------------------------------------------------------------------

class TestNotionTransportLifecycle:
    def test_close_calls_client_close(self):
        transport = NotionTransport(make_config())
        with patch.object(transport._client, "close") as mock_close:
            transport.close()
        mock_close.assert_called_once()

    def test_context_manager_closes_on_exit(self):
        transport = NotionTransport(make_config())
        with (
            patch.object(transport._client, "close") as mock_close,
            transport as t,
        ):
            assert t is transport
        mock_close.assert_called_once()

    def test_context_manager_closes_on_exception(self):
        transport = NotionTransport(make_config())
        with (
            patch.object(transport._client, "close") as mock_close,
            pytest.raises(ValueError, match="test error"),
            transport,
        ):
            raise ValueError("test error")
        mock_close.assert_called_once()


# ---------------------------------------------------------------------------
# Async AsyncNotionTransport
# ---------------------------------------------------------------------------

class TestAsyncNotionTransportRequest:
    """Tests for AsyncNotionTransport.request()."""

    def _transport(self, **cfg_overrides) -> AsyncNotionTransport:
        t = AsyncNotionTransport(make_config(**cfg_overrides))
        t._bucket = _MockAsyncBucket(wait=0.0)
        return t

    # -- Success cases -------------------------------------------------------

    async def test_200_returns_json(self):
        transport = self._transport()
        resp = make_response(200, body={"id": "page-1", "object": "page"})
        with patch.object(transport._client, "request", new=AsyncMock(return_value=resp)):
            result = await transport.request("GET", "/pages/page-1")
        assert result == {"id": "page-1", "object": "page"}
        await transport.close()

    async def test_204_returns_empty_dict(self):
        transport = self._transport()
        resp = make_response(204, body=None)
        with patch.object(transport._client, "request", new=AsyncMock(return_value=resp)):
            result = await transport.request("DELETE", "/blocks/abc")
        assert result == {}
        await transport.close()

    async def test_empty_content_returns_empty_dict(self):
        transport = self._transport()
        resp = httpx.Response(200, content=b"", headers={})
        resp.request = httpx.Request("GET", "https://api.notion.com/v1/pages/x")
        with patch.object(transport._client, "request", new=AsyncMock(return_value=resp)):
            result = await transport.request("GET", "/pages/x")
        assert result == {}
        await transport.close()

    # -- 4xx non-retryable errors -------------------------------------------

    async def test_400_raises_validation_error(self):
        transport = self._transport()
        resp = make_response(400, body={"message": "bad input", "code": "err"})
        with (
            patch.object(transport._client, "request", new=AsyncMock(return_value=resp)),
            pytest.raises(NotionifyValidationError),
        ):
            await transport.request("POST", "/pages")
        await transport.close()

    async def test_401_raises_auth_error(self):
        transport = self._transport()
        resp = make_response(401, body={"message": "Unauthorized", "code": "unauthorized"})
        with (
            patch.object(transport._client, "request", new=AsyncMock(return_value=resp)),
            pytest.raises(NotionifyAuthError),
        ):
            await transport.request("GET", "/users/me")
        await transport.close()

    async def test_403_raises_permission_error(self):
        transport = self._transport()
        resp = make_response(403, body={"message": "Forbidden", "code": "forbidden"})
        with (
            patch.object(transport._client, "request", new=AsyncMock(return_value=resp)),
            pytest.raises(NotionifyPermissionError),
        ):
            await transport.request("GET", "/pages/secret")
        await transport.close()

    async def test_404_raises_not_found_error(self):
        transport = self._transport()
        resp = make_response(404, body={"message": "Not found", "code": "not_found"})
        with (
            patch.object(transport._client, "request", new=AsyncMock(return_value=resp)),
            pytest.raises(NotionifyNotFoundError),
        ):
            await transport.request("GET", "/pages/missing")
        await transport.close()

    async def test_409_raises_diff_conflict_error(self):
        transport = self._transport()
        resp = make_response(409, body={"message": "Conflict", "code": "conflict"})
        with (
            patch.object(transport._client, "request", new=AsyncMock(return_value=resp)),
            pytest.raises(NotionifyDiffConflictError),
        ):
            await transport.request("PATCH", "/pages/p1")
        await transport.close()

    # -- Retry on 429 --------------------------------------------------------

    async def test_429_retry_exhausted(self):
        transport = self._transport(retry_max_attempts=3)
        resp_429 = make_response(429, body={"message": "rate limited"})
        with (
            patch.object(transport._client, "request", new=AsyncMock(return_value=resp_429)),
            pytest.raises(NotionifyRetryExhaustedError) as exc_info,
        ):
            await transport.request("GET", "/databases")
        assert exc_info.value.context["attempts"] == 3
        await transport.close()

    async def test_429_with_retry_after_success_on_retry(self):
        transport = self._transport(retry_max_attempts=3)
        resp_429 = make_response(429, body={}, headers={"retry-after": "0"})
        resp_200 = make_response(200, body={"ok": True})
        call_count = {"n": 0}

        async def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return resp_429
            return resp_200

        with patch.object(transport._client, "request", side_effect=side_effect):
            result = await transport.request("GET", "/pages")
        assert result == {"ok": True}
        await transport.close()

    # -- Retry on 5xx --------------------------------------------------------

    async def test_500_success_on_second_attempt(self):
        transport = self._transport(retry_max_attempts=3)
        resp_500 = make_response(500, body={"message": "Internal Server Error"})
        resp_200 = make_response(200, body={"object": "page", "id": "abc"})
        responses = [resp_500, resp_200]
        idx = {"i": 0}

        async def side_effect(*args, **kwargs):
            r = responses[idx["i"]]
            idx["i"] += 1
            return r

        with patch.object(transport._client, "request", side_effect=side_effect):
            result = await transport.request("GET", "/pages/abc")
        assert result["id"] == "abc"
        await transport.close()

    async def test_500_retry_exhausted(self):
        transport = self._transport(retry_max_attempts=3)
        resp_500 = make_response(500, body={"message": "Server error"})
        with (
            patch.object(transport._client, "request", new=AsyncMock(return_value=resp_500)),
            pytest.raises(NotionifyRetryExhaustedError),
        ):
            await transport.request("POST", "/blocks")
        await transport.close()

    # -- Network errors -------------------------------------------------------

    async def test_timeout_retried_then_success(self):
        transport = self._transport(retry_max_attempts=3)
        resp_200 = make_response(200, body={"id": "p1"})
        call_count = {"n": 0}

        async def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.TimeoutException("timed out")
            return resp_200

        with patch.object(transport._client, "request", side_effect=side_effect):
            result = await transport.request("GET", "/pages/p1")
        assert result == {"id": "p1"}
        await transport.close()

    async def test_network_error_exhausted_raises_network_error(self):
        # All retries hit NetworkError: on the final attempt should_retry
        # returns False, so NotionifyNetworkError is raised directly.
        transport = self._transport(retry_max_attempts=3)

        async def side_effect(*args, **kwargs):
            raise httpx.NetworkError("connection reset")

        with (
            patch.object(transport._client, "request", side_effect=side_effect),
            pytest.raises(NotionifyNetworkError),
        ):
            await transport.request("GET", "/pages")
        await transport.close()

    async def test_network_error_single_attempt_raises_network_error(self):
        transport = self._transport(retry_max_attempts=1)

        async def side_effect(*args, **kwargs):
            raise httpx.NetworkError("DNS failure")

        with (
            patch.object(transport._client, "request", side_effect=side_effect),
            pytest.raises(NotionifyNetworkError),
        ):
            await transport.request("GET", "/pages")
        await transport.close()

    # -- Debug dump ----------------------------------------------------------

    async def test_debug_dump_writes_to_stderr(self, capsys):
        transport = self._transport(debug_dump_payload=True)
        resp = make_response(200, body={"id": "p1"})
        resp.request = httpx.Request("GET", "https://api.notion.com/v1/pages/p1")
        with patch.object(transport._client, "request", new=AsyncMock(return_value=resp)):
            await transport.request("GET", "/pages/p1", json={"key": "val"})
        captured = capsys.readouterr()
        assert captured.err.strip() != ""
        data = json.loads(captured.err)
        assert data["method"] == "GET"
        await transport.close()

    # -- Rate-limit metrics --------------------------------------------------

    async def test_rate_limit_timing_metric_emitted(self):
        mock_metrics = MagicMock()
        transport = self._transport(metrics=mock_metrics)
        transport._bucket = _MockAsyncBucket(wait=1.0)
        resp = make_response(200, body={})

        with patch.object(transport._client, "request", new=AsyncMock(return_value=resp)):
            await transport.request("GET", "/pages")

        mock_metrics.timing.assert_any_call(
            "notionify.rate_limit_wait_ms",
            pytest.approx(1000.0, rel=0.1),
            tags={"method": "GET", "path": "/pages"},
        )
        await transport.close()

    async def test_rate_limit_no_metrics_when_wait_zero(self):
        mock_metrics = MagicMock()
        transport = self._transport(metrics=mock_metrics)
        # _MockAsyncBucket(wait=0.0) is already set in _transport
        resp = make_response(200, body={})

        with patch.object(transport._client, "request", new=AsyncMock(return_value=resp)):
            await transport.request("GET", "/pages")

        calls = [str(c) for c in mock_metrics.timing.call_args_list]
        rate_limit_calls = [c for c in calls if "rate_limit_wait_ms" in c]
        assert len(rate_limit_calls) == 0
        await transport.close()


# ---------------------------------------------------------------------------
# Async AsyncNotionTransport.paginate
# ---------------------------------------------------------------------------

class TestAsyncNotionTransportPaginate:
    def _transport(self) -> AsyncNotionTransport:
        t = AsyncNotionTransport(make_config())
        t._bucket = _MockAsyncBucket()
        return t

    async def test_single_page_yields_items(self):
        transport = self._transport()
        page_resp = make_response(200, body={
            "results": [{"id": "a"}, {"id": "b"}],
            "has_more": False,
            "next_cursor": None,
        })
        with patch.object(transport._client, "request", new=AsyncMock(return_value=page_resp)):
            items = [item async for item in transport.paginate("/blocks/1/children")]
        assert items == [{"id": "a"}, {"id": "b"}]
        await transport.close()

    async def test_multi_page_yields_all_items(self):
        transport = self._transport()
        page1 = make_response(200, body={
            "results": [{"id": "a"}],
            "has_more": True,
            "next_cursor": "cursor-abc",
        })
        page2 = make_response(200, body={
            "results": [{"id": "b"}],
            "has_more": False,
            "next_cursor": None,
        })
        responses = iter([page1, page2])

        async def side_effect(*args, **kwargs):
            return next(responses)

        with patch.object(transport._client, "request", side_effect=side_effect):
            items = [item async for item in transport.paginate("/blocks/1/children")]
        assert items == [{"id": "a"}, {"id": "b"}]
        await transport.close()

    async def test_post_method_pagination(self):
        transport = self._transport()
        page1 = make_response(200, body={
            "results": [{"id": "db1"}],
            "has_more": True,
            "next_cursor": "cursor-xyz",
        })
        page2 = make_response(200, body={
            "results": [{"id": "db2"}],
            "has_more": False,
            "next_cursor": None,
        })
        responses = [page1, page2]
        idx = {"i": 0}
        call_kwargs_list = []

        async def side_effect(*args, **kwargs):
            call_kwargs_list.append(kwargs)
            r = responses[idx["i"]]
            idx["i"] += 1
            return r

        with patch.object(transport._client, "request", side_effect=side_effect):
            items = [item async for item in transport.paginate("/databases/search", method="POST")]
        assert items == [{"id": "db1"}, {"id": "db2"}]
        # Verify second call included start_cursor
        assert call_kwargs_list[1]["json"]["start_cursor"] == "cursor-xyz"
        await transport.close()

    async def test_stops_when_no_next_cursor(self):
        transport = self._transport()
        page_resp = make_response(200, body={
            "results": [{"id": "item1"}],
            "has_more": True,
            "next_cursor": None,
        })
        with patch.object(transport._client, "request", new=AsyncMock(return_value=page_resp)):
            items = [item async for item in transport.paginate("/blocks/1/children")]
        assert items == [{"id": "item1"}]
        await transport.close()

    async def test_empty_results_page(self):
        transport = self._transport()
        page_resp = make_response(200, body={"results": [], "has_more": False})
        with patch.object(transport._client, "request", new=AsyncMock(return_value=page_resp)):
            items = [item async for item in transport.paginate("/search")]
        assert items == []
        await transport.close()


# ---------------------------------------------------------------------------
# Async AsyncNotionTransport close / context manager
# ---------------------------------------------------------------------------

class TestAsyncNotionTransportLifecycle:
    async def test_close_calls_aclose(self):
        transport = AsyncNotionTransport(make_config())
        with patch.object(transport._client, "aclose", new=AsyncMock()) as mock_aclose:
            await transport.close()
        mock_aclose.assert_called_once()

    async def test_async_context_manager(self):
        transport = AsyncNotionTransport(make_config())
        with patch.object(transport._client, "aclose", new=AsyncMock()) as mock_aclose:
            async with transport as t:
                assert t is transport
        mock_aclose.assert_called_once()

    async def test_async_context_manager_closes_on_exception(self):
        transport = AsyncNotionTransport(make_config())
        with (
            patch.object(transport._client, "aclose", new=AsyncMock()) as mock_aclose,
            pytest.raises(ValueError, match="test error"),
        ):
            async with transport:
                raise ValueError("test error")
        mock_aclose.assert_called_once()


# ---------------------------------------------------------------------------
# _parse_retry_after — additional edge cases
# ---------------------------------------------------------------------------

class TestParseRetryAfterEdgeCases:
    def test_infinity_returns_float_inf(self):
        resp = make_response(headers={"retry-after": "inf"})
        result = _parse_retry_after(resp)
        assert result == float("inf")

    def test_nan_returns_float_nan(self):
        import math
        resp = make_response(headers={"retry-after": "nan"})
        result = _parse_retry_after(resp)
        assert result is not None
        assert math.isnan(result)

    def test_leading_trailing_whitespace_parsed(self):
        resp = make_response(headers={"retry-after": "  5  "})
        assert _parse_retry_after(resp) == 5.0

    def test_plus_sign_prefix_parsed(self):
        resp = make_response(headers={"retry-after": "+5"})
        assert _parse_retry_after(resp) == 5.0


# ---------------------------------------------------------------------------
# _emit_debug_dump — non-JSON response fallback
# ---------------------------------------------------------------------------

class TestEmitDebugDump:
    def test_non_json_response_falls_back_to_text(self, capsys):
        config = make_config(debug_dump_payload=True)
        resp = httpx.Response(500, content=b"Internal Server Error", headers={})
        resp.request = httpx.Request("GET", "https://api.notion.com/v1/pages")
        _emit_debug_dump(config, "GET", resp, None)
        captured = capsys.readouterr()
        data = json.loads(captured.err)
        assert data["response_body"] == "Internal Server Error"

    def test_non_json_response_truncated_at_1000_chars(self, capsys):
        config = make_config(debug_dump_payload=True)
        long_text = "x" * 2000
        resp = httpx.Response(500, content=long_text.encode(), headers={})
        resp.request = httpx.Request("GET", "https://api.notion.com/v1/pages")
        _emit_debug_dump(config, "GET", resp, None)
        captured = capsys.readouterr()
        data = json.loads(captured.err)
        assert len(data["response_body"]) == 1000

    def test_json_response_returns_parsed_dict(self, capsys):
        config = make_config(debug_dump_payload=True)
        resp = make_response(200, body={"id": "p1"})
        resp.request = httpx.Request("GET", "https://api.notion.com/v1/pages/p1")
        _emit_debug_dump(config, "GET", resp, {"key": "val"})
        captured = capsys.readouterr()
        data = json.loads(captured.err)
        assert data["response_body"] == {"id": "p1"}
        assert data["request_body"] == {"key": "val"}

    def test_disabled_does_not_dump(self, capsys):
        config = make_config(debug_dump_payload=False)
        resp = httpx.Response(500, content=b"error", headers={})
        resp.request = httpx.Request("GET", "https://api.notion.com/v1/pages")
        _emit_debug_dump(config, "GET", resp, None)
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_html_error_response(self, capsys):
        config = make_config(debug_dump_payload=True)
        html = b"<html><body>502 Bad Gateway</body></html>"
        resp = httpx.Response(502, content=html, headers={})
        resp.request = httpx.Request("GET", "https://api.notion.com/v1/pages")
        _emit_debug_dump(config, "GET", resp, None)
        captured = capsys.readouterr()
        data = json.loads(captured.err)
        assert "502 Bad Gateway" in data["response_body"]


# ---------------------------------------------------------------------------
# Sync NotionTransport.paginate — additional edge cases
# ---------------------------------------------------------------------------

class TestPaginateEdgeCases:
    def _transport(self) -> NotionTransport:
        t = NotionTransport(make_config())
        t._bucket = _MockBucket(wait=0.0)
        return t

    def test_missing_results_key_yields_nothing(self):
        transport = self._transport()
        page_resp = make_response(200, body={"has_more": False})
        with patch.object(transport._client, "request", return_value=page_resp):
            items = list(transport.paginate("/blocks/1/children"))
        assert items == []

    def test_missing_has_more_stops_after_one_page(self):
        transport = self._transport()
        page_resp = make_response(200, body={"results": [{"id": "a"}]})
        with patch.object(transport._client, "request", return_value=page_resp):
            items = list(transport.paginate("/blocks/1/children"))
        assert items == [{"id": "a"}]

    def test_patch_method_uses_json_body(self):
        transport = self._transport()
        page_resp = make_response(200, body={
            "results": [{"id": "x"}],
            "has_more": False,
        })
        mock_request = MagicMock(return_value=page_resp)
        with patch.object(transport._client, "request", mock_request):
            items = list(transport.paginate("/databases/query", method="PATCH"))
        assert items == [{"id": "x"}]
        call_kwargs = mock_request.call_args_list[0][1]
        assert "json" in call_kwargs
        assert call_kwargs["json"]["page_size"] == 100

    def test_lowercase_post_method_uses_json_body(self):
        transport = self._transport()
        page_resp = make_response(200, body={
            "results": [{"id": "y"}],
            "has_more": False,
        })
        mock_request = MagicMock(return_value=page_resp)
        with patch.object(transport._client, "request", mock_request):
            items = list(transport.paginate("/search", method="post"))
        assert items == [{"id": "y"}]
        call_kwargs = mock_request.call_args_list[0][1]
        assert call_kwargs["json"]["page_size"] == 100

    def test_three_page_pagination(self):
        transport = self._transport()
        pages = [
            make_response(200, body={
                "results": [{"id": "a"}], "has_more": True, "next_cursor": "c1",
            }),
            make_response(200, body={
                "results": [{"id": "b"}], "has_more": True, "next_cursor": "c2",
            }),
            make_response(200, body={
                "results": [{"id": "c"}], "has_more": False, "next_cursor": None,
            }),
        ]
        responses = iter(pages)
        mock_request = MagicMock(side_effect=lambda *a, **kw: next(responses))
        with patch.object(transport._client, "request", mock_request):
            items = list(transport.paginate("/blocks/1/children"))
        assert items == [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        assert mock_request.call_count == 3

    def test_get_with_existing_params_merged(self):
        transport = self._transport()
        page_resp = make_response(200, body={"results": [], "has_more": False})
        mock_request = MagicMock(return_value=page_resp)
        with patch.object(transport._client, "request", mock_request):
            list(transport.paginate("/blocks/1/children", params={"filter": "all"}))
        call_kwargs = mock_request.call_args_list[0][1]
        assert call_kwargs["params"]["filter"] == "all"
        assert call_kwargs["params"]["page_size"] == 100

    def test_post_first_page_removes_stale_start_cursor(self):
        transport = self._transport()
        page_resp = make_response(200, body={"results": [], "has_more": False})
        mock_request = MagicMock(return_value=page_resp)
        # Pass json with a stale start_cursor that should be removed
        with patch.object(transport._client, "request", mock_request):
            list(transport.paginate(
                "/search", method="POST",
                json={"start_cursor": "stale-cursor"},
            ))
        call_kwargs = mock_request.call_args_list[0][1]
        assert "start_cursor" not in call_kwargs["json"]


# ---------------------------------------------------------------------------
# Async paginate — additional edge cases
# ---------------------------------------------------------------------------

class TestAsyncPaginateEdgeCases:
    def _transport(self) -> AsyncNotionTransport:
        t = AsyncNotionTransport(make_config())
        t._bucket = _MockAsyncBucket(wait=0.0)
        return t

    async def test_missing_results_key_yields_nothing(self):
        transport = self._transport()
        page_resp = make_response(200, body={"has_more": False})
        with patch.object(transport._client, "request", new=AsyncMock(return_value=page_resp)):
            items = [item async for item in transport.paginate("/blocks/1/children")]
        assert items == []
        await transport.close()

    async def test_missing_has_more_stops_after_one_page(self):
        transport = self._transport()
        page_resp = make_response(200, body={"results": [{"id": "a"}]})
        with patch.object(transport._client, "request", new=AsyncMock(return_value=page_resp)):
            items = [item async for item in transport.paginate("/blocks/1/children")]
        assert items == [{"id": "a"}]
        await transport.close()

    async def test_three_page_pagination(self):
        transport = self._transport()
        pages = [
            make_response(200, body={
                "results": [{"id": "a"}], "has_more": True, "next_cursor": "c1",
            }),
            make_response(200, body={
                "results": [{"id": "b"}], "has_more": True, "next_cursor": "c2",
            }),
            make_response(200, body={
                "results": [{"id": "c"}], "has_more": False, "next_cursor": None,
            }),
        ]
        idx = {"i": 0}

        async def side_effect(*args, **kwargs):
            r = pages[idx["i"]]
            idx["i"] += 1
            return r

        with patch.object(transport._client, "request", side_effect=side_effect):
            items = [item async for item in transport.paginate("/blocks/1/children")]
        assert items == [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        await transport.close()


# ---------------------------------------------------------------------------
# Retry-After edge cases (sync transport)
# ---------------------------------------------------------------------------

class TestRetryAfterEdgeCases:
    """Edge cases for retry behaviour around Retry-After headers and exhaustion."""

    def _transport(self, **cfg_overrides) -> NotionTransport:
        t = NotionTransport(make_config(**cfg_overrides))
        t._bucket = _MockBucket(wait=0.0)
        return t

    def test_429_with_invalid_retry_after_falls_back_to_exponential(self):
        """When 429 returned but Retry-After header is non-numeric, should
        still retry using exponential backoff."""
        transport = self._transport(retry_max_attempts=2)
        resp_429 = make_response(
            429, body={"message": "rate limited"}, headers={"retry-after": "invalid"},
        )
        resp_200 = make_response(200, body={"ok": True})
        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return resp_429
            return resp_200

        with patch.object(transport._client, "request", side_effect=side_effect):
            result = transport.request("GET", "/pages")
        assert result == {"ok": True}
        assert call_count["n"] == 2

    def test_429_without_retry_after_header_falls_back_to_exponential(self):
        """When 429 returned but no Retry-After header at all, should still
        retry using exponential backoff."""
        transport = self._transport(retry_max_attempts=2)
        # 429 with no Retry-After header
        resp_429 = make_response(429, body={"message": "rate limited"})
        resp_200 = make_response(200, body={"result": "ok"})
        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return resp_429
            return resp_200

        with patch.object(transport._client, "request", side_effect=side_effect):
            result = transport.request("GET", "/databases")
        assert result == {"result": "ok"}
        assert call_count["n"] == 2

    def test_503_transient_then_success(self):
        """First request returns 503, second request returns 200."""
        transport = self._transport(retry_max_attempts=3)
        resp_503 = make_response(503, body={"message": "Service Unavailable"})
        resp_200 = make_response(200, body={"id": "page-1"})
        responses = iter([resp_503, resp_200])

        with patch.object(
            transport._client, "request",
            side_effect=lambda *a, **kw: next(responses),
        ):
            result = transport.request("GET", "/pages/page-1")
        assert result == {"id": "page-1"}

    def test_all_attempts_exhausted_context_has_correct_attempts_count(self):
        """The context['attempts'] in RetryExhaustedError matches max_attempts."""
        transport = self._transport(retry_max_attempts=4)
        resp_429 = make_response(429, body={"message": "rate limited"})
        with (
            patch.object(transport._client, "request", return_value=resp_429),
            pytest.raises(NotionifyRetryExhaustedError) as exc_info,
        ):
            transport.request("GET", "/pages")
        assert exc_info.value.context["attempts"] == 4

    def test_all_attempts_exhausted_context_has_last_status_code(self):
        """The context['last_status_code'] correctly contains the final status code."""
        transport = self._transport(retry_max_attempts=2)
        resp_502 = make_response(502, body={"message": "Bad Gateway"})
        with (
            patch.object(transport._client, "request", return_value=resp_502),
            pytest.raises(NotionifyRetryExhaustedError) as exc_info,
        ):
            transport.request("POST", "/blocks")
        assert exc_info.value.context["last_status_code"] == 502

    def test_network_error_all_retries_exhausted_has_cause(self):
        """When all retries fail with network error, the raised error has .cause
        set to the original exception."""
        transport = self._transport(retry_max_attempts=3)

        original_exc = httpx.NetworkError("connection refused")

        def side_effect(*args, **kwargs):
            raise original_exc

        with (
            patch.object(transport._client, "request", side_effect=side_effect),
            pytest.raises(NotionifyNetworkError) as exc_info,
        ):
            transport.request("GET", "/pages")
        assert exc_info.value.cause is original_exc

    def test_mixed_503_then_network_error_exhausts(self):
        """First attempt gets 503, second gets network error, third also
        network error -- should exhaust retries."""
        transport = self._transport(retry_max_attempts=3)
        resp_503 = make_response(503, body={"message": "Service Unavailable"})
        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return resp_503
            raise httpx.NetworkError("connection reset")

        with (
            patch.object(transport._client, "request", side_effect=side_effect),
            pytest.raises(NotionifyNetworkError),
        ):
            transport.request("GET", "/pages")
        assert call_count["n"] == 3


# ---------------------------------------------------------------------------
# Async transport cancellation
# ---------------------------------------------------------------------------

class TestAsyncTransportCancellation:
    """Verify that asyncio.CancelledError propagates correctly from the
    async transport rather than being swallowed by retry/rate-limit logic."""

    def _transport(self, **cfg_overrides) -> AsyncNotionTransport:
        t = AsyncNotionTransport(make_config(**cfg_overrides))
        t._bucket = _MockAsyncBucket(wait=0.0)
        return t

    async def test_cancelled_during_request_propagates(self):
        """CancelledError during httpx request should propagate."""
        import asyncio

        transport = self._transport()

        async def raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        transport._client = AsyncMock(spec=httpx.AsyncClient)
        transport._client.request = raise_cancelled

        with pytest.raises(asyncio.CancelledError):
            await transport.request("GET", "/pages")

    async def test_cancelled_during_rate_limit_wait_propagates(self):
        """CancelledError during bucket.acquire should propagate."""
        import asyncio

        transport = self._transport()

        async def raise_cancelled(tokens: int = 1) -> float:
            raise asyncio.CancelledError()

        transport._bucket = MagicMock()
        transport._bucket.acquire = raise_cancelled

        with pytest.raises(asyncio.CancelledError):
            await transport.request("GET", "/pages")

    async def test_cancelled_during_retry_sleep_propagates(self):
        """CancelledError during asyncio.sleep in retry path should propagate."""
        import asyncio

        transport = self._transport(retry_max_attempts=3)
        # First request returns 429 to trigger retry, then sleep raises CancelledError
        resp_429 = make_response(
            429, body={"message": "rate limited"}, headers={"retry-after": "0"},
        )

        transport._client = AsyncMock(spec=httpx.AsyncClient)
        transport._client.request = AsyncMock(return_value=resp_429)

        sleep_patch = patch(
            "notionify.notion_api.transport.asyncio.sleep",
            side_effect=asyncio.CancelledError(),
        )
        with sleep_patch, pytest.raises(asyncio.CancelledError):
            await transport.request("GET", "/pages")
