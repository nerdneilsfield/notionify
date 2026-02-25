"""Full error hierarchy for the notionify SDK.

Every public error class inherits from NotionifyError. Each carries a
machine-readable ``code`` (from :class:`ErrorCode`), a human-readable
``message``, an optional structured ``context`` dict, and an optional
``cause`` (chained exception).

Error codes are defined as a :class:`str` enum so that they serialise
naturally to JSON and can be matched with simple ``==`` comparisons.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Error code enum
# ---------------------------------------------------------------------------

class ErrorCode(str, Enum):
    """Machine-readable error codes for every error the SDK can raise."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    PERMISSION_ERROR = "PERMISSION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    NETWORK_ERROR = "NETWORK_ERROR"
    CONVERSION_ERROR = "CONVERSION_ERROR"
    UNSUPPORTED_BLOCK = "UNSUPPORTED_BLOCK"
    TEXT_OVERFLOW = "TEXT_OVERFLOW"
    MATH_OVERFLOW = "MATH_OVERFLOW"
    IMAGE_ERROR = "IMAGE_ERROR"
    IMAGE_NOT_FOUND = "IMAGE_NOT_FOUND"
    IMAGE_TYPE_ERROR = "IMAGE_TYPE_ERROR"
    IMAGE_SIZE_ERROR = "IMAGE_SIZE_ERROR"
    IMAGE_PARSE_ERROR = "IMAGE_PARSE_ERROR"
    UPLOAD_ERROR = "UPLOAD_ERROR"
    UPLOAD_EXPIRED = "UPLOAD_EXPIRED"
    UPLOAD_TRANSPORT_ERROR = "UPLOAD_TRANSPORT_ERROR"
    DIFF_CONFLICT = "DIFF_CONFLICT"


# ---------------------------------------------------------------------------
# Base error
# ---------------------------------------------------------------------------

class NotionifyError(Exception):
    """Base exception for all notionify errors.

    Parameters
    ----------
    code:
        A value from :class:`ErrorCode` (or any string) identifying the
        error category.
    message:
        A developer-friendly description of what went wrong.
    context:
        Arbitrary structured data providing extra diagnostic detail.
        Keys and expected types are documented per subclass.
    cause:
        The underlying exception, if this error wraps another.
    """

    def __init__(
        self,
        code: str,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        self.code: str = code
        self.message: str = message
        self.context: dict[str, Any] = context or {}
        self.cause: Exception | None = cause
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause

    def __repr__(self) -> str:
        ctx = f", context={self.context!r}" if self.context else ""
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r}{ctx})"


# ---------------------------------------------------------------------------
# API / transport errors
# ---------------------------------------------------------------------------

class NotionifyValidationError(NotionifyError):
    """Notion API returned 400 — the request payload was invalid.

    Context keys: ``field``, ``value``, ``constraint``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.VALIDATION_ERROR,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyAuthError(NotionifyError):
    """Notion API returned 401 — the integration token is invalid or expired.

    Context keys: ``token_prefix`` (last 4 characters only, for diagnostics).
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.AUTH_ERROR,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyPermissionError(NotionifyError):
    """Notion API returned 403 — the integration lacks access to the resource.

    Context keys: ``page_id``, ``operation``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.PERMISSION_ERROR,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyNotFoundError(NotionifyError):
    """Notion API returned 404 — the requested resource does not exist.

    Context keys: ``resource_type``, ``resource_id``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.NOT_FOUND,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyRateLimitError(NotionifyError):
    """Notion API returned 429 — rate limit exceeded.

    Context keys: ``retry_after_seconds``, ``attempt``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.RATE_LIMITED,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyRetryExhaustedError(NotionifyError):
    """All retry attempts have been exhausted for a retryable request.

    Context keys: ``attempts``, ``last_status_code``, ``last_error_code``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.RETRY_EXHAUSTED,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyNetworkError(NotionifyError):
    """A transport-level failure occurred (timeout, DNS, connection reset).

    Context keys: ``url``, ``attempt``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.NETWORK_ERROR,
            message=message,
            context=context,
            cause=cause,
        )


# ---------------------------------------------------------------------------
# Conversion errors
# ---------------------------------------------------------------------------

class NotionifyConversionError(NotionifyError):
    """Base class for errors during Markdown/Notion conversion.

    Context varies by subclass.
    """

    def __init__(
        self,
        code: str = ErrorCode.CONVERSION_ERROR,
        message: str = "Conversion error",
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyUnsupportedBlockError(NotionifyConversionError):
    """A Notion block type has no Markdown equivalent and the configured
    policy is ``"raise"``.

    Context keys: ``block_id``, ``block_type``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.UNSUPPORTED_BLOCK,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyTextOverflowError(NotionifyConversionError):
    """A rich-text segment exceeds the Notion 2000-character limit and
    cannot be automatically split.

    Context keys: ``content_length``, ``limit``, ``block_type``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.TEXT_OVERFLOW,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyMathOverflowError(NotionifyConversionError):
    """A math expression exceeds the Notion 1000-character equation limit.

    Context keys: ``expression_length``, ``limit``, ``strategy``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.MATH_OVERFLOW,
            message=message,
            context=context,
            cause=cause,
        )


# ---------------------------------------------------------------------------
# Image errors
# ---------------------------------------------------------------------------

class NotionifyImageError(NotionifyError):
    """Base class for image-related errors.

    Context varies by subclass.
    """

    def __init__(
        self,
        code: str = ErrorCode.IMAGE_ERROR,
        message: str = "Image error",
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyImageNotFoundError(NotionifyImageError):
    """The referenced image file does not exist on disk.

    Context keys: ``src``, ``resolved_path``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.IMAGE_NOT_FOUND,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyImageTypeError(NotionifyImageError):
    """The detected MIME type is not in the configured allowlist.

    Context keys: ``src``, ``detected_mime``, ``allowed_mimes``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.IMAGE_TYPE_ERROR,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyImageSizeError(NotionifyImageError):
    """The image exceeds the configured maximum upload size.

    Context keys: ``src``, ``size_bytes``, ``max_bytes``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.IMAGE_SIZE_ERROR,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyImageParseError(NotionifyImageError):
    """A data-URI image could not be decoded (malformed base64 / header).

    Context keys: ``src``, ``reason``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.IMAGE_PARSE_ERROR,
            message=message,
            context=context,
            cause=cause,
        )


# ---------------------------------------------------------------------------
# Upload errors
# ---------------------------------------------------------------------------

class NotionifyUploadError(NotionifyError):
    """Base class for file-upload errors.

    Context varies by subclass.
    """

    def __init__(
        self,
        code: str = ErrorCode.UPLOAD_ERROR,
        message: str = "Upload error",
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyUploadExpiredError(NotionifyUploadError):
    """The upload was completed but the attachment window expired before
    the block could be appended.

    Context keys: ``upload_id``, ``elapsed_seconds``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.UPLOAD_EXPIRED,
            message=message,
            context=context,
            cause=cause,
        )


class NotionifyUploadTransportError(NotionifyUploadError):
    """A transport-level failure occurred during a file upload chunk.

    Context keys: ``upload_id``, ``part_number``, ``status_code``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.UPLOAD_TRANSPORT_ERROR,
            message=message,
            context=context,
            cause=cause,
        )


# ---------------------------------------------------------------------------
# Diff errors
# ---------------------------------------------------------------------------

class NotionifyDiffConflictError(NotionifyError):
    """The page has been modified since the snapshot was taken and the
    configured conflict policy is ``"raise"``.

    Context keys: ``page_id``, ``snapshot_time``, ``detected_time``.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.DIFF_CONFLICT,
            message=message,
            context=context,
            cause=cause,
        )
