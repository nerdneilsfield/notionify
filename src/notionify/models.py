"""Public data models for the notionify SDK.

This module contains every result type, warning type, enum, and
supporting dataclass referenced by the public API surface.  All types
are plain dataclasses with no behaviour beyond what is needed for
structural equality and hashing (where frozen).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ImageSourceType(str, Enum):
    """Classification of an image ``src`` attribute."""

    EXTERNAL_URL = "external_url"
    """The image is referenced by an ``http://`` or ``https://`` URL."""

    LOCAL_FILE = "local_file"
    """The image is a path to a file on the local filesystem."""

    DATA_URI = "data_uri"
    """The image is encoded inline as a ``data:`` URI."""

    UNKNOWN = "unknown"
    """The source could not be classified."""


class UploadState(str, Enum):
    """Lifecycle states for a file upload tracked by the image pipeline."""

    PENDING = "pending"
    """Initial state — upload has not started."""

    UPLOADING = "uploading"
    """PUT / chunk transfer is in progress."""

    UPLOADED = "uploaded"
    """All bytes have been sent; block has not yet been attached."""

    ATTACHED = "attached"
    """The block referencing this upload has been appended to Notion."""

    FAILED = "failed"
    """An unrecoverable error occurred during upload."""

    EXPIRED = "expired"
    """The upload completed but the attachment window passed before the
    block was appended.  May transition back to ``UPLOADING`` on retry."""


class DiffOpType(str, Enum):
    """Operation types emitted by the diff engine."""

    KEEP = "keep"
    """Block is unchanged — no API call needed."""

    UPDATE = "update"
    """Same block type, content has changed — PATCH the block."""

    REPLACE = "replace"
    """Block type changed — archive the old block and insert a new one."""

    INSERT = "insert"
    """A new block that does not exist on the page yet."""

    DELETE = "delete"
    """An existing block that should be archived/deleted."""


# ---------------------------------------------------------------------------
# Conversion warnings
# ---------------------------------------------------------------------------

@dataclass
class ConversionWarning:
    """A non-fatal issue encountered during Markdown/Notion conversion.

    Warnings are accumulated in result objects so callers can inspect
    them after the operation completes.

    Attributes
    ----------
    code:
        A machine-readable warning code (e.g. ``"TEXT_OVERFLOW"``).
    message:
        A human-readable description of the issue.
    context:
        Arbitrary structured data for diagnostics.
    """

    code: str
    message: str
    context: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Conversion internals (used between converter and client)
# ---------------------------------------------------------------------------

@dataclass
class PendingImage:
    """An image discovered during conversion that needs upload or embedding.

    Attributes
    ----------
    src:
        The raw ``src`` value from the Markdown ``![alt](src)`` token.
    source_type:
        Classification of *src* (URL, local path, data URI, unknown).
    block_index:
        Index into the flat list of converted Notion blocks where this
        image should appear.  Used to splice in the final ``image``
        block once the upload completes.
    """

    src: str
    source_type: ImageSourceType
    block_index: int


@dataclass
class ConversionResult:
    """Output of the Markdown-to-Notion-blocks conversion phase.

    Attributes
    ----------
    blocks:
        The list of Notion block payloads (dicts) ready to be sent to
        the API.  Image blocks that require upload will initially have
        placeholder content; they are patched in-place once the upload
        pipeline resolves them.
    images:
        Images that need to be processed by the upload pipeline before
        the blocks can be sent to Notion.
    warnings:
        Non-fatal issues discovered during conversion.
    """

    blocks: list[dict] = field(default_factory=list)
    images: list[PendingImage] = field(default_factory=list)
    warnings: list[ConversionWarning] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Diff engine types
# ---------------------------------------------------------------------------

@dataclass
class DiffOp:
    """A single operation in a diff plan.

    Attributes
    ----------
    op_type:
        The kind of operation (keep, update, replace, insert, delete).
    existing_id:
        The Notion block ID of the existing block for operations that
        reference one (``DELETE``, ``UPDATE``, ``REPLACE``).
    new_block:
        The Notion block payload for operations that create or update a
        block (``INSERT``, ``UPDATE``, ``REPLACE``).
    position_after:
        The block ID after which a new block should be inserted
        (``INSERT``, ``REPLACE``).
    depth:
        Nesting depth at which this operation applies.
    """

    op_type: DiffOpType
    existing_id: str | None = None
    new_block: dict | None = None
    position_after: str | None = None
    depth: int = 0


@dataclass(frozen=True)
class BlockSignature:
    """Structural fingerprint of a Notion block used for diff matching.

    Two blocks with identical signatures are considered unchanged.  The
    class is frozen so instances can be used as dict keys and in sets.

    Attributes
    ----------
    block_type:
        The Notion block type string (e.g. ``"paragraph"``,
        ``"heading_2"``).
    rich_text_hash:
        MD5 hex digest of the normalised plain text content.
    structural_hash:
        MD5 hex digest of child-count and child-type information.
    attrs_hash:
        MD5 hex digest of type-specific attributes (language, checked
        state, heading level, etc.).
    nesting_depth:
        Depth of this block in the tree (root children are depth 0).
    """

    block_type: str
    rich_text_hash: str
    structural_hash: str
    attrs_hash: str
    nesting_depth: int


@dataclass
class PageSnapshot:
    """A point-in-time snapshot of a Notion page used for conflict detection.

    Attributes
    ----------
    page_id:
        The Notion page ID.
    last_edited:
        The ``last_edited_time`` of the page at snapshot time.
    block_etags:
        Mapping of block ID to its ``last_edited_time`` string.  Used
        to detect per-block concurrent modifications.
    """

    page_id: str
    last_edited: datetime
    block_etags: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public result types (returned from client methods)
# ---------------------------------------------------------------------------

@dataclass
class PageCreateResult:
    """Result of :meth:`NotionifyClient.create_page_with_markdown`.

    Attributes
    ----------
    page_id:
        The ID of the newly created Notion page.
    url:
        The URL of the newly created page.
    blocks_created:
        Total number of blocks appended to the page.
    images_uploaded:
        Number of images processed through the upload pipeline.
    warnings:
        Non-fatal issues encountered during conversion or upload.
    """

    page_id: str
    url: str
    blocks_created: int
    images_uploaded: int
    warnings: list[ConversionWarning] = field(default_factory=list)


@dataclass
class AppendResult:
    """Result of :meth:`NotionifyClient.append_markdown`.

    Attributes
    ----------
    blocks_appended:
        Number of blocks appended.
    images_uploaded:
        Number of images uploaded.
    warnings:
        Non-fatal conversion warnings.
    """

    blocks_appended: int
    images_uploaded: int
    warnings: list[ConversionWarning] = field(default_factory=list)


@dataclass
class UpdateResult:
    """Result of :meth:`NotionifyClient.update_page_from_markdown` or
    :meth:`NotionifyClient.overwrite_page_content`.

    Attributes
    ----------
    strategy_used:
        ``"diff"`` or ``"overwrite"``, reflecting the strategy that was
        actually applied (may differ from the requested strategy if a
        conflict forced a fallback).
    blocks_kept:
        Number of blocks that were unchanged (diff only).
    blocks_inserted:
        Number of new blocks added.
    blocks_deleted:
        Number of existing blocks archived.
    blocks_replaced:
        Number of blocks whose type changed (archive + insert).
    images_uploaded:
        Number of images uploaded.
    warnings:
        Non-fatal conversion warnings.
    """

    strategy_used: str
    blocks_kept: int
    blocks_inserted: int
    blocks_deleted: int
    blocks_replaced: int
    images_uploaded: int
    warnings: list[ConversionWarning] = field(default_factory=list)


@dataclass
class BlockUpdateResult:
    """Result of :meth:`NotionifyClient.update_block`.

    Attributes
    ----------
    block_id:
        The ID of the block that was updated.
    warnings:
        Non-fatal conversion warnings.
    """

    block_id: str
    warnings: list[ConversionWarning] = field(default_factory=list)


@dataclass
class InsertResult:
    """Result of :meth:`NotionifyClient.insert_after`.

    Attributes
    ----------
    inserted_block_ids:
        IDs of the blocks that were created, in order.
    warnings:
        Non-fatal conversion warnings.
    """

    inserted_block_ids: list[str] = field(default_factory=list)
    warnings: list[ConversionWarning] = field(default_factory=list)
