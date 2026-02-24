"""notionify — High-fidelity bidirectional Markdown / Notion SDK.

Public re-exports
-----------------

* **Clients:** :class:`NotionifyClient`, :class:`AsyncNotionifyClient`
* **Configuration:** :class:`NotionifyConfig`
* **Errors:** Every :class:`NotionifyError` subclass and :class:`ErrorCode`
* **Models:** All result dataclasses, enums, and supporting types

Usage::

    from notionify import NotionifyClient, NotionifyConfig

    client = NotionifyClient(token="secret_xxx")
    result = client.create_page_with_markdown(
        parent_id="<page_id>",
        title="My Page",
        markdown="# Hello\\n\\nWorld",
    )
"""

from __future__ import annotations

from notionify.async_client import AsyncNotionifyClient

# ── Clients ────────────────────────────────────────────────────────────
from notionify.client import NotionifyClient

# ── Configuration ───────────────────────────────────────────────────────
from notionify.config import (
    DEFAULT_EXTERNAL_MIMES,
    DEFAULT_UPLOAD_MIMES,
    NotionifyConfig,
)

# ── Errors ──────────────────────────────────────────────────────────────
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

# ── Models ──────────────────────────────────────────────────────────────
from notionify.models import (
    AppendResult,
    BlockSignature,
    BlockUpdateResult,
    ConversionResult,
    ConversionWarning,
    DiffOp,
    DiffOpType,
    ImageSourceType,
    InsertResult,
    PageCreateResult,
    PageSnapshot,
    PendingImage,
    UpdateResult,
    UploadState,
)

# ── Public surface ──────────────────────────────────────────────────────

__all__ = [
    # Clients
    "NotionifyClient",
    "AsyncNotionifyClient",
    # Configuration
    "NotionifyConfig",
    "DEFAULT_UPLOAD_MIMES",
    "DEFAULT_EXTERNAL_MIMES",
    # Error base + code enum
    "NotionifyError",
    "ErrorCode",
    # API / transport errors
    "NotionifyValidationError",
    "NotionifyAuthError",
    "NotionifyPermissionError",
    "NotionifyNotFoundError",
    "NotionifyRateLimitError",
    "NotionifyRetryExhaustedError",
    "NotionifyNetworkError",
    # Conversion errors
    "NotionifyConversionError",
    "NotionifyUnsupportedBlockError",
    "NotionifyTextOverflowError",
    "NotionifyMathOverflowError",
    # Image errors
    "NotionifyImageError",
    "NotionifyImageNotFoundError",
    "NotionifyImageTypeError",
    "NotionifyImageSizeError",
    "NotionifyImageParseError",
    # Upload errors
    "NotionifyUploadError",
    "NotionifyUploadExpiredError",
    "NotionifyUploadTransportError",
    # Diff errors
    "NotionifyDiffConflictError",
    # Models — result types
    "PageCreateResult",
    "AppendResult",
    "UpdateResult",
    "BlockUpdateResult",
    "InsertResult",
    "ConversionWarning",
    # Models — conversion internals
    "ConversionResult",
    "PendingImage",
    # Models — enums
    "ImageSourceType",
    "UploadState",
    "DiffOpType",
    # Models — diff types
    "DiffOp",
    "BlockSignature",
    "PageSnapshot",
]
