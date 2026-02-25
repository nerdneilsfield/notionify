"""Iteration 22: Error context field validation, client lifecycle, and export tests.

Covers:
- Transport _raise_for_status populates correct context fields per PRD §15
- Every ErrorCode enum member has a matching error subclass
- Sync/async client context manager and close() lifecycle
- __all__ exports in __init__.py match actual module-level names
- Error pickling round-trip (for multiprocessing compatibility)
"""

from __future__ import annotations

import json
import pickle
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import notionify
from notionify import __all__ as PKG_ALL
from notionify.async_client import AsyncNotionifyClient
from notionify.client import NotionifyClient
from notionify.errors import (
    ErrorCode,
    NotionifyAuthError,
    NotionifyConversionError,
    NotionifyDiffConflictError,
    NotionifyError,
    NotionifyImageError,
    NotionifyImageNotFoundError,
    NotionifyImageParseError,
    NotionifyImageSizeError,
    NotionifyImageTypeError,
    NotionifyMathOverflowError,
    NotionifyNetworkError,
    NotionifyNotFoundError,
    NotionifyPermissionError,
    NotionifyRateLimitError,
    NotionifyRetryExhaustedError,
    NotionifyTextOverflowError,
    NotionifyUnsupportedBlockError,
    NotionifyUploadError,
    NotionifyUploadExpiredError,
    NotionifyUploadTransportError,
    NotionifyValidationError,
)
from notionify.notion_api.transport import _raise_for_status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(
    status_code: int,
    body: dict | None = None,
) -> httpx.Response:
    """Build a minimal httpx.Response for _raise_for_status testing."""
    content = json.dumps(body or {}).encode()
    resp = httpx.Response(status_code, content=content)
    resp.request = httpx.Request("GET", "https://api.notion.com/v1/test")
    return resp


# =========================================================================
# 1. TestRaiseForStatusContextFields
# =========================================================================


class TestRaiseForStatusContextFields:
    """Verify _raise_for_status populates the correct context fields for each
    HTTP status code as documented in the error class docstrings (PRD §15)."""

    def test_400_context_has_status_code_and_body(self):
        """400 → NotionifyValidationError with status_code, notion_code, body."""
        resp = _make_response(400, {"message": "Invalid input", "code": "validation_error"})
        with pytest.raises(NotionifyValidationError) as exc_info:
            _raise_for_status(resp, "POST", "/v1/pages")
        ctx = exc_info.value.context
        assert ctx["status_code"] == 400
        assert ctx["notion_code"] == "validation_error"
        assert "body" in ctx
        assert ctx["body"]["message"] == "Invalid input"

    def test_401_context_has_status_code(self):
        """401 → NotionifyAuthError with status_code and notion_code."""
        resp = _make_response(401, {"message": "API token invalid", "code": "unauthorized"})
        with pytest.raises(NotionifyAuthError) as exc_info:
            _raise_for_status(resp, "GET", "/v1/pages/abc")
        ctx = exc_info.value.context
        assert ctx["status_code"] == 401
        assert ctx["notion_code"] == "unauthorized"
        # Auth errors must NOT include the full token
        assert "token" not in ctx or len(str(ctx.get("token", ""))) <= 10

    def test_403_context_has_operation(self):
        """403 → NotionifyPermissionError with status_code, notion_code, operation."""
        resp = _make_response(403, {"message": "No access", "code": "restricted_resource"})
        with pytest.raises(NotionifyPermissionError) as exc_info:
            _raise_for_status(resp, "PATCH", "/v1/blocks/xyz")
        ctx = exc_info.value.context
        assert ctx["status_code"] == 403
        assert ctx["notion_code"] == "restricted_resource"
        assert ctx["operation"] == "PATCH /v1/blocks/xyz"

    def test_404_context_has_path(self):
        """404 → NotionifyNotFoundError with status_code, notion_code, path."""
        resp = _make_response(404, {"message": "Not found", "code": "object_not_found"})
        with pytest.raises(NotionifyNotFoundError) as exc_info:
            _raise_for_status(resp, "GET", "/v1/pages/missing")
        ctx = exc_info.value.context
        assert ctx["status_code"] == 404
        assert ctx["notion_code"] == "object_not_found"
        assert ctx["path"] == "/v1/pages/missing"

    def test_409_context_has_status_code(self):
        """409 → NotionifyDiffConflictError with status_code and notion_code."""
        resp = _make_response(409, {"message": "Conflict", "code": "conflict_error"})
        with pytest.raises(NotionifyDiffConflictError) as exc_info:
            _raise_for_status(resp, "PATCH", "/v1/blocks/blk")
        ctx = exc_info.value.context
        assert ctx["status_code"] == 409
        assert ctx["notion_code"] == "conflict_error"

    def test_422_falls_through_to_validation_error(self):
        """Non-specific 4xx (e.g., 422) → NotionifyValidationError."""
        resp = _make_response(422, {"message": "Unprocessable"})
        with pytest.raises(NotionifyValidationError) as exc_info:
            _raise_for_status(resp, "POST", "/v1/pages")
        assert exc_info.value.context["status_code"] == 422

    def test_400_with_empty_body(self):
        """400 with an empty/unparseable body still produces correct context."""
        resp = httpx.Response(400, content=b"not json")
        resp.request = httpx.Request("POST", "https://api.notion.com/v1/test")
        with pytest.raises(NotionifyValidationError) as exc_info:
            _raise_for_status(resp, "POST", "/v1/pages")
        ctx = exc_info.value.context
        assert ctx["status_code"] == 400
        assert ctx["notion_code"] == ""

    def test_error_message_includes_method_and_path(self):
        """Error messages should include the HTTP method and path for debugging."""
        resp = _make_response(404, {"message": "Not found"})
        with pytest.raises(NotionifyNotFoundError) as exc_info:
            _raise_for_status(resp, "DELETE", "/v1/blocks/xyz")
        assert "DELETE" in exc_info.value.message
        assert "/v1/blocks/xyz" in exc_info.value.message

    def test_401_error_message_no_token_leak(self):
        """Auth error messages must not contain the integration token."""
        resp = _make_response(401, {"message": "Token is invalid"})
        with pytest.raises(NotionifyAuthError) as exc_info:
            _raise_for_status(resp, "GET", "/v1/users/me")
        # The error message should contain the Notion API message, not our token
        assert "Token is invalid" in exc_info.value.message
        # Verify no raw token pattern leaks (ntn_ prefix)
        assert "ntn_" not in exc_info.value.message


# =========================================================================
# 2. TestErrorCodeCompleteness
# =========================================================================


class TestErrorCodeCompleteness:
    """Verify every ErrorCode enum member has a matching error subclass."""

    _CODE_TO_CLASS: ClassVar[dict[ErrorCode, type[NotionifyError]]] = {
        ErrorCode.VALIDATION_ERROR: NotionifyValidationError,
        ErrorCode.AUTH_ERROR: NotionifyAuthError,
        ErrorCode.PERMISSION_ERROR: NotionifyPermissionError,
        ErrorCode.NOT_FOUND: NotionifyNotFoundError,
        ErrorCode.RATE_LIMITED: NotionifyRateLimitError,
        ErrorCode.RETRY_EXHAUSTED: NotionifyRetryExhaustedError,
        ErrorCode.NETWORK_ERROR: NotionifyNetworkError,
        ErrorCode.CONVERSION_ERROR: NotionifyConversionError,
        ErrorCode.UNSUPPORTED_BLOCK: NotionifyUnsupportedBlockError,
        ErrorCode.TEXT_OVERFLOW: NotionifyTextOverflowError,
        ErrorCode.MATH_OVERFLOW: NotionifyMathOverflowError,
        ErrorCode.IMAGE_ERROR: NotionifyImageError,
        ErrorCode.IMAGE_NOT_FOUND: NotionifyImageNotFoundError,
        ErrorCode.IMAGE_TYPE_ERROR: NotionifyImageTypeError,
        ErrorCode.IMAGE_SIZE_ERROR: NotionifyImageSizeError,
        ErrorCode.IMAGE_PARSE_ERROR: NotionifyImageParseError,
        ErrorCode.UPLOAD_ERROR: NotionifyUploadError,
        ErrorCode.UPLOAD_EXPIRED: NotionifyUploadExpiredError,
        ErrorCode.UPLOAD_TRANSPORT_ERROR: NotionifyUploadTransportError,
        ErrorCode.DIFF_CONFLICT: NotionifyDiffConflictError,
    }

    def test_every_error_code_has_a_subclass(self):
        """Every ErrorCode member must map to a concrete error class."""
        for code in ErrorCode:
            assert code in self._CODE_TO_CLASS, f"ErrorCode.{code.name} has no mapped class"

    @pytest.mark.parametrize("code", list(ErrorCode))
    def test_error_class_sets_correct_code(self, code: ErrorCode):
        """Instantiating the mapped class produces an error with the correct code."""
        cls = self._CODE_TO_CLASS[code]
        if cls in (NotionifyConversionError, NotionifyImageError, NotionifyUploadError):
            # These base classes take code as first arg
            err = cls(code=code, message="test")
        else:
            err = cls(message="test")
        assert err.code == code

    def test_no_duplicate_error_codes(self):
        """No two ErrorCode members share the same string value."""
        values = [e.value for e in ErrorCode]
        assert len(values) == len(set(values))

    def test_error_code_count_matches_class_count(self):
        """The number of ErrorCode members equals the mapping size."""
        assert len(ErrorCode) == len(self._CODE_TO_CLASS)


# =========================================================================
# 3. TestErrorPickling
# =========================================================================


class TestErrorPickling:
    """Verify errors can be pickled/unpickled for multiprocessing support."""

    @pytest.mark.parametrize(
        ("cls", "kwargs"),
        [
            (NotionifyError, {"code": "TEST", "message": "test"}),
            (NotionifyValidationError, {"message": "bad input", "context": {"field": "x"}}),
            (NotionifyAuthError, {"message": "auth fail"}),
            (NotionifyPermissionError, {"message": "denied"}),
            (NotionifyNotFoundError, {"message": "not found"}),
            (NotionifyRateLimitError, {
                "message": "throttled", "context": {"retry_after_seconds": 5},
            }),
            (NotionifyRetryExhaustedError, {
                "message": "exhausted", "context": {"attempts": 3},
            }),
            (NotionifyNetworkError, {"message": "timeout"}),
            (NotionifyTextOverflowError, {
                "message": "too long", "context": {"content_length": 3000},
            }),
            (NotionifyMathOverflowError, {"message": "math too long"}),
            (NotionifyImageSizeError, {
                "message": "img too big", "context": {"size_bytes": 10_000_000},
            }),
            (NotionifyImageTypeError, {"message": "bad mime"}),
            (NotionifyImageParseError, {"message": "bad b64"}),
            (NotionifyImageNotFoundError, {"message": "no file"}),
            (NotionifyUploadExpiredError, {"message": "expired", "context": {"upload_id": "u1"}}),
            (NotionifyUploadTransportError, {"message": "upload failed"}),
            (NotionifyDiffConflictError, {"message": "conflict", "context": {"page_id": "p1"}}),
            (NotionifyUnsupportedBlockError, {"message": "unsupported"}),
        ],
    )
    def test_pickle_round_trip(self, cls, kwargs):
        """Every error class survives pickle round-trip with attributes preserved."""
        err = cls(**kwargs)
        restored = pickle.loads(pickle.dumps(err))
        assert type(restored) is cls
        assert restored.code == err.code
        assert restored.message == err.message
        assert restored.context == err.context

    def test_pickle_with_cause(self):
        """Pickling an error with a chained cause preserves the cause."""
        cause = ValueError("inner error")
        err = NotionifyNetworkError(
            message="network fail", cause=cause,
        )
        restored = pickle.loads(pickle.dumps(err))
        assert type(restored) is NotionifyNetworkError
        assert restored.message == "network fail"
        assert restored.cause is not None
        assert str(restored.cause) == "inner error"
        assert restored.__cause__ is not None


# =========================================================================
# 4. TestSyncClientLifecycle
# =========================================================================


class TestSyncClientLifecycle:
    """Verify NotionifyClient context manager and close() behaviour."""

    def test_context_manager_returns_self(self):
        """__enter__ returns the client instance."""
        client = NotionifyClient(token="test")
        with client as c:
            assert c is client

    def test_context_manager_calls_close(self):
        """__exit__ delegates to close()."""
        client = NotionifyClient(token="test")
        with patch.object(client, "close") as mock_close:
            with client:
                pass
            mock_close.assert_called_once()

    def test_close_is_idempotent(self):
        """Calling close() multiple times does not raise."""
        client = NotionifyClient(token="test")
        client.close()
        client.close()  # Should not raise

    def test_config_accessible_after_init(self):
        """The internal config is correctly initialized from kwargs."""
        client = NotionifyClient(token="my-tok", retry_max_attempts=5)
        assert client._config.token == "my-tok"
        assert client._config.retry_max_attempts == 5

    def test_context_manager_closes_on_exception(self):
        """close() is called even when an exception occurs inside the with block."""
        client = NotionifyClient(token="test")
        with (
            patch.object(client, "close") as mock_close,
            pytest.raises(RuntimeError, match="boom"),
            client,
        ):
            raise RuntimeError("boom")
        mock_close.assert_called_once()


# =========================================================================
# 5. TestAsyncClientLifecycle
# =========================================================================


class TestAsyncClientLifecycle:
    """Verify AsyncNotionifyClient async context manager and close() behaviour."""

    async def test_async_context_manager_returns_self(self):
        """__aenter__ returns the client instance."""
        client = AsyncNotionifyClient(token="test")
        async with client as c:
            assert c is client

    async def test_async_context_manager_calls_close(self):
        """__aexit__ delegates to close()."""
        client = AsyncNotionifyClient(token="test")
        with patch.object(client, "close", new_callable=AsyncMock) as mock_close:
            async with client:
                pass
            mock_close.assert_called_once()

    async def test_async_close_is_idempotent(self):
        """Calling close() multiple times does not raise."""
        client = AsyncNotionifyClient(token="test")
        await client.close()
        await client.close()  # Should not raise

    async def test_async_config_accessible(self):
        """Config is correctly initialized from kwargs."""
        client = AsyncNotionifyClient(token="my-tok", retry_max_attempts=7)
        assert client._config.token == "my-tok"
        assert client._config.retry_max_attempts == 7

    async def test_async_context_manager_closes_on_exception(self):
        """close() is called even when an exception occurs inside the async with block."""
        client = AsyncNotionifyClient(token="test")
        with patch.object(client, "close", new_callable=AsyncMock) as mock_close:
            with pytest.raises(RuntimeError, match="boom"):
                async with client:
                    raise RuntimeError("boom")
            mock_close.assert_called_once()


# =========================================================================
# 6. TestPublicExportsConsistency
# =========================================================================


class TestPublicExportsConsistency:
    """Verify __all__ in notionify/__init__.py is consistent with actual exports."""

    def test_all_names_are_importable(self):
        """Every name in __all__ must be importable from the notionify package."""
        for name in PKG_ALL:
            assert hasattr(notionify, name), f"{name!r} is in __all__ but not importable"

    def test_all_public_classes_in_all(self):
        """Key public classes must appear in __all__."""
        expected = [
            "NotionifyClient",
            "AsyncNotionifyClient",
            "NotionifyConfig",
            "NotionifyError",
            "ErrorCode",
            "PageCreateResult",
            "AppendResult",
            "UpdateResult",
            "ConversionResult",
            "DiffOp",
            "DiffOpType",
        ]
        for name in expected:
            assert name in PKG_ALL, f"{name!r} missing from __all__"

    def test_all_error_classes_in_all(self):
        """Every NotionifyError subclass must be in __all__."""
        error_classes = [
            "NotionifyValidationError",
            "NotionifyAuthError",
            "NotionifyPermissionError",
            "NotionifyNotFoundError",
            "NotionifyRateLimitError",
            "NotionifyRetryExhaustedError",
            "NotionifyNetworkError",
            "NotionifyConversionError",
            "NotionifyUnsupportedBlockError",
            "NotionifyTextOverflowError",
            "NotionifyMathOverflowError",
            "NotionifyImageError",
            "NotionifyImageNotFoundError",
            "NotionifyImageTypeError",
            "NotionifyImageSizeError",
            "NotionifyImageParseError",
            "NotionifyUploadError",
            "NotionifyUploadExpiredError",
            "NotionifyUploadTransportError",
            "NotionifyDiffConflictError",
        ]
        for name in error_classes:
            assert name in PKG_ALL, f"Error class {name!r} missing from __all__"

    def test_no_private_names_in_all(self):
        """No names starting with underscore should be in __all__ (except __version__)."""
        for name in PKG_ALL:
            if name.startswith("_"):
                assert name == "__version__", f"Private name {name!r} in __all__"

    def test_all_has_no_duplicates(self):
        """__all__ must not contain duplicate entries."""
        assert len(PKG_ALL) == len(set(PKG_ALL)), "Duplicate entries in __all__"

    def test_version_in_all(self):
        """__version__ must be in __all__."""
        assert "__version__" in PKG_ALL

    def test_version_is_string(self):
        """__version__ must be a string following semver pattern."""
        assert isinstance(notionify.__version__, str)
        parts = notionify.__version__.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)


# =========================================================================
# 7. TestErrorContextDocumentation
# =========================================================================


class TestErrorContextDocumentation:
    """Verify documented context keys for each error subclass match usage patterns."""

    def test_validation_error_context_keys(self):
        """NotionifyValidationError context can include field, value, constraint."""
        err = NotionifyValidationError(
            message="Invalid",
            context={"field": "title", "value": "", "constraint": "non-empty"},
        )
        assert err.context["field"] == "title"
        assert err.context["constraint"] == "non-empty"

    def test_auth_error_context_keys(self):
        """NotionifyAuthError context can include token_prefix."""
        err = NotionifyAuthError(
            message="Auth failed",
            context={"token_prefix": "...1234"},
        )
        assert err.context["token_prefix"] == "...1234"

    def test_permission_error_context_keys(self):
        """NotionifyPermissionError context can include page_id, operation."""
        err = NotionifyPermissionError(
            message="Denied",
            context={"page_id": "p-123", "operation": "PATCH /v1/blocks"},
        )
        assert err.context["page_id"] == "p-123"
        assert err.context["operation"] == "PATCH /v1/blocks"

    def test_not_found_error_context_keys(self):
        """NotionifyNotFoundError context can include resource_type, resource_id."""
        err = NotionifyNotFoundError(
            message="Missing",
            context={"resource_type": "page", "resource_id": "p-abc"},
        )
        assert err.context["resource_type"] == "page"

    def test_rate_limit_error_context_keys(self):
        """NotionifyRateLimitError context can include retry_after_seconds, attempt."""
        err = NotionifyRateLimitError(
            message="Throttled",
            context={"retry_after_seconds": 2.5, "attempt": 3},
        )
        assert err.context["retry_after_seconds"] == 2.5
        assert err.context["attempt"] == 3

    def test_retry_exhausted_error_context_keys(self):
        """NotionifyRetryExhaustedError context can include attempts, last_status_code."""
        err = NotionifyRetryExhaustedError(
            message="Exhausted",
            context={
                "attempts": 5,
                "last_status_code": 502,
                "last_error_code": "service_unavailable",
            },
        )
        assert err.context["attempts"] == 5
        assert err.context["last_status_code"] == 502

    def test_network_error_context_keys(self):
        """NotionifyNetworkError context can include url, attempt."""
        err = NotionifyNetworkError(
            message="Timeout",
            context={"url": "https://api.notion.com/v1/pages", "attempt": 2},
        )
        assert err.context["url"] == "https://api.notion.com/v1/pages"

    def test_text_overflow_error_context_keys(self):
        """NotionifyTextOverflowError context can include content_length, limit, block_type."""
        err = NotionifyTextOverflowError(
            message="Too long",
            context={"content_length": 2500, "limit": 2000, "block_type": "paragraph"},
        )
        assert err.context["content_length"] == 2500
        assert err.context["limit"] == 2000
        assert err.context["block_type"] == "paragraph"

    def test_math_overflow_error_context_keys(self):
        """NotionifyMathOverflowError context can include expression_length, limit, strategy."""
        err = NotionifyMathOverflowError(
            message="Math too long",
            context={"expression_length": 1500, "limit": 1000, "strategy": "equation"},
        )
        assert err.context["expression_length"] == 1500
        assert err.context["strategy"] == "equation"

    def test_unsupported_block_error_context_keys(self):
        """NotionifyUnsupportedBlockError context can include block_id, block_type."""
        err = NotionifyUnsupportedBlockError(
            message="Unsupported",
            context={"block_id": "blk-1", "block_type": "child_database"},
        )
        assert err.context["block_id"] == "blk-1"
        assert err.context["block_type"] == "child_database"

    def test_image_not_found_error_context_keys(self):
        """NotionifyImageNotFoundError context can include src, resolved_path."""
        err = NotionifyImageNotFoundError(
            message="Missing",
            context={"src": "photo.png", "resolved_path": "/abs/photo.png"},
        )
        assert err.context["src"] == "photo.png"

    def test_image_type_error_context_keys(self):
        """NotionifyImageTypeError context can include src, detected_mime, allowed_mimes."""
        err = NotionifyImageTypeError(
            message="Bad MIME",
            context={"src": "x.bmp", "detected_mime": "image/bmp", "allowed_mimes": ["image/png"]},
        )
        assert err.context["detected_mime"] == "image/bmp"

    def test_image_size_error_context_keys(self):
        """NotionifyImageSizeError context can include src, size_bytes, max_bytes."""
        err = NotionifyImageSizeError(
            message="Too big",
            context={"src": "huge.png", "size_bytes": 10_000_000, "max_bytes": 5_000_000},
        )
        assert err.context["size_bytes"] == 10_000_000
        assert err.context["max_bytes"] == 5_000_000

    def test_image_parse_error_context_keys(self):
        """NotionifyImageParseError context can include src, reason."""
        err = NotionifyImageParseError(
            message="Bad data URI",
            context={"src": "data:...", "reason": "base64_error"},
        )
        assert err.context["reason"] == "base64_error"

    def test_upload_expired_error_context_keys(self):
        """NotionifyUploadExpiredError context can include upload_id, elapsed_seconds."""
        err = NotionifyUploadExpiredError(
            message="Expired",
            context={"upload_id": "up-1", "elapsed_seconds": 3600},
        )
        assert err.context["upload_id"] == "up-1"
        assert err.context["elapsed_seconds"] == 3600

    def test_upload_transport_error_context_keys(self):
        """NotionifyUploadTransportError context can include upload_id, part_number, status_code."""
        err = NotionifyUploadTransportError(
            message="Upload failed",
            context={"upload_id": "up-2", "part_number": 3, "status_code": 502},
        )
        assert err.context["upload_id"] == "up-2"
        assert err.context["part_number"] == 3
        assert err.context["status_code"] == 502

    def test_diff_conflict_error_context_keys(self):
        """NotionifyDiffConflictError context can include page_id, snapshot_time, detected_time."""
        err = NotionifyDiffConflictError(
            message="Conflict",
            context={
                "page_id": "p-1",
                "snapshot_time": "2025-01-01T00:00:00Z",
                "detected_time": "2025-01-01T00:01:00Z",
            },
        )
        assert err.context["page_id"] == "p-1"
        assert err.context["snapshot_time"] == "2025-01-01T00:00:00Z"


# =========================================================================
# 8. TestErrorCauseChaining
# =========================================================================


class TestErrorCauseChaining:
    """Verify that exception chaining works correctly for all error types."""

    @pytest.mark.parametrize(
        "cls",
        [
            NotionifyValidationError,
            NotionifyAuthError,
            NotionifyPermissionError,
            NotionifyNotFoundError,
            NotionifyRateLimitError,
            NotionifyRetryExhaustedError,
            NotionifyNetworkError,
            NotionifyTextOverflowError,
            NotionifyMathOverflowError,
            NotionifyUnsupportedBlockError,
            NotionifyImageNotFoundError,
            NotionifyImageTypeError,
            NotionifyImageSizeError,
            NotionifyImageParseError,
            NotionifyUploadExpiredError,
            NotionifyUploadTransportError,
            NotionifyDiffConflictError,
        ],
    )
    def test_cause_is_chained(self, cls):
        """Each error class correctly chains __cause__ from the cause parameter."""
        original = ConnectionError("network down")
        err = cls(message="wrapper", cause=original)
        assert err.__cause__ is original
        assert err.cause is original

    @pytest.mark.parametrize(
        "cls",
        [
            NotionifyValidationError,
            NotionifyAuthError,
            NotionifyNotFoundError,
            NotionifyNetworkError,
        ],
    )
    def test_cause_none_by_default(self, cls):
        """When no cause is given, __cause__ is None."""
        err = cls(message="no cause")
        assert err.__cause__ is None
        assert err.cause is None
