"""SDK configuration for notionify.

:class:`NotionifyConfig` is a frozen-friendly dataclass that captures every
tuneable knob exposed by the SDK.  Instances are passed to both
:class:`NotionifyClient` and :class:`AsyncNotionifyClient`.

Two module-level constants define the default MIME allowlists:

* :data:`DEFAULT_UPLOAD_MIMES` — accepted for file-upload images.
* :data:`DEFAULT_EXTERNAL_MIMES` — accepted for external-URL images.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# MIME allowlist constants
# ---------------------------------------------------------------------------

DEFAULT_UPLOAD_MIMES: list[str] = [
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",
]
"""MIME types accepted for local-file and data-URI uploads."""

DEFAULT_EXTERNAL_MIMES: list[str] = [
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "image/bmp",
    "image/tiff",
]
"""MIME types accepted for external-URL images (checked when
``image_verify_external=True``)."""


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass
class NotionifyConfig:
    """Complete configuration for a notionify client.

    Every parameter has a sensible default so that the only *required*
    value is ``token``.

    Parameters
    ----------
    token:
        Notion integration token.  **Required.**  Never logged.
    notion_version:
        Value of the ``Notion-Version`` header sent with every request.
    base_url:
        API root URL.  Override for proxy or testing environments.
    math_strategy:
        How to convert LaTeX math to Notion blocks.

        * ``"equation"`` — native Notion equation objects (recommended).
        * ``"code"`` — store as a code block with ``language="latex"``.
        * ``"latex_text"`` — keep as plain text with ``$``/``$$`` delimiters.
    math_overflow_inline:
        Fallback when an inline equation exceeds the 1 000-character limit.

        * ``"split"`` — best-effort split across multiple equation objects.
        * ``"code"`` — render as inline code.
        * ``"text"`` — render as plain text with ``$...$``.
    math_overflow_block:
        Fallback when a block equation exceeds the 1 000-character limit.

        * ``"split"`` — split into multiple equation blocks.
        * ``"code"`` — render as a code block with ``language="latex"``.
        * ``"text"`` — render as a paragraph with ``$$...$$``.
    detect_latex_code:
        On Notion-to-Markdown export, treat code blocks with
        ``language="latex"`` as math and render them with ``$$...$$``.
    image_upload:
        Enable the upload pipeline for local-file and data-URI images.
    image_max_concurrent:
        Maximum number of parallel upload tasks (async client only).
    image_fallback:
        Behaviour when an image cannot be processed.

        * ``"skip"`` — silently omit the image block.
        * ``"placeholder"`` — insert a text block ``[image: <src>]``.
        * ``"raise"`` — raise :class:`NotionifyImageError`.
    image_expiry_warnings:
        On export, annotate Notion-hosted image URLs with an expiry
        warning comment.
    image_allowed_mimes_upload:
        MIME types accepted for file-upload images.
    image_allowed_mimes_external:
        MIME types accepted for external-URL images (checked via
        ``Content-Type`` when ``image_verify_external=True``).
    image_max_size_bytes:
        Maximum file size in bytes for uploaded images.  Default is 5 MiB.
    image_verify_external:
        Issue a HEAD request against external image URLs before embedding
        to verify ``Content-Type`` and reachability.
    enable_tables:
        Convert Markdown tables to Notion table blocks.
    table_fallback:
        Behaviour when ``enable_tables=False`` or table conversion fails.

        * ``"paragraph"`` — render as a plain-text paragraph.
        * ``"comment"`` — emit an HTML comment marker.
        * ``"raise"`` — raise :class:`NotionifyConversionError`.
    heading_overflow:
        How to handle Markdown headings of level 4 and above (Notion only
        supports H1–H3).

        * ``"downgrade"`` — clamp to ``heading_3``.
        * ``"paragraph"`` — render as a bold paragraph.
    unsupported_block_policy:
        On export, how to render Notion block types that have no Markdown
        equivalent.

        * ``"comment"`` — emit ``<!-- notion-block: <type> -->``.
        * ``"skip"`` — silently omit.
        * ``"raise"`` — raise :class:`NotionifyUnsupportedBlockError`.
    retry_max_attempts:
        Maximum number of retries per request for retryable HTTP errors.
    retry_base_delay:
        Base delay (seconds) for exponential backoff.
    retry_max_delay:
        Upper cap (seconds) on computed backoff delay.
    retry_jitter:
        Add random jitter (plus/minus 50 %) to backoff intervals.
    rate_limit_rps:
        Target requests per second for client-side pacing (token bucket).
    timeout_seconds:
        HTTP request timeout in seconds.
    http_proxy:
        Optional HTTP/HTTPS proxy URL.
    debug_dump_ast:
        Write the normalised Mistune AST to *stderr* on each conversion.
    debug_dump_payload:
        Write the (redacted) Notion API payload to *stderr*.
    debug_dump_diff:
        Write the diff-engine operation plan to *stderr*.
    """

    # ── Core ────────────────────────────────────────────────────────────
    token: str = ""

    notion_version: str = "2025-09-03"

    base_url: str = "https://api.notion.com/v1"

    # ── Math ────────────────────────────────────────────────────────────
    math_strategy: Literal["equation", "code", "latex_text"] = "equation"

    math_overflow_inline: Literal["split", "code", "text"] = "code"

    math_overflow_block: Literal["split", "code", "text"] = "code"

    detect_latex_code: bool = True

    # ── Images ──────────────────────────────────────────────────────────
    image_upload: bool = True

    image_max_concurrent: int = 4

    image_fallback: Literal["skip", "placeholder", "raise"] = "skip"

    image_expiry_warnings: bool = True

    image_allowed_mimes_upload: list[str] = field(
        default_factory=lambda: list(DEFAULT_UPLOAD_MIMES),
    )

    image_allowed_mimes_external: list[str] = field(
        default_factory=lambda: list(DEFAULT_EXTERNAL_MIMES),
    )

    image_max_size_bytes: int = 5 * 1024 * 1024  # 5 MiB

    image_verify_external: bool = False

    image_base_dir: str | None = None
    """If set, local image file paths are resolved relative to this
    directory and must remain within it.  Prevents path traversal attacks
    when processing untrusted Markdown.  The value is resolved to an
    absolute path at use time."""

    # ── Tables ──────────────────────────────────────────────────────────
    enable_tables: bool = True

    table_fallback: Literal["paragraph", "comment", "raise"] = "comment"

    # ── Headings ────────────────────────────────────────────────────────
    heading_overflow: Literal["downgrade", "paragraph"] = "downgrade"

    # ── Unsupported blocks ──────────────────────────────────────────────
    unsupported_block_policy: Literal["comment", "skip", "raise"] = "comment"

    # ── Retry & rate ────────────────────────────────────────────────────
    retry_max_attempts: int = 5

    retry_base_delay: float = 1.0

    retry_max_delay: float = 60.0

    retry_jitter: bool = True

    rate_limit_rps: float = 3.0

    # ── HTTP ────────────────────────────────────────────────────────────
    timeout_seconds: float = 30.0

    http_proxy: str | None = None

    # ── Observability ──────────────────────────────────────────────────
    metrics: Any | None = None

    # ── Debug ───────────────────────────────────────────────────────────
    debug_dump_ast: bool = False

    debug_dump_payload: bool = False

    debug_dump_diff: bool = False

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        from urllib.parse import urlparse

        parsed = urlparse(self.base_url)
        if parsed.scheme == "http" and parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            raise ValueError(
                f"base_url uses insecure HTTP for non-local host '{parsed.hostname}'. "
                "Use HTTPS to protect your API token, or target localhost for testing."
            )

        # Numeric parameter validation — catch invalid values at config
        # time instead of letting them surface as confusing runtime errors.
        if self.retry_max_attempts < 0:
            raise ValueError(f"retry_max_attempts must be >= 0, got {self.retry_max_attempts}")
        if self.retry_base_delay < 0:
            raise ValueError(f"retry_base_delay must be >= 0, got {self.retry_base_delay}")
        if self.retry_max_delay < 0:
            raise ValueError(f"retry_max_delay must be >= 0, got {self.retry_max_delay}")
        if self.rate_limit_rps <= 0:
            raise ValueError(f"rate_limit_rps must be > 0, got {self.rate_limit_rps}")
        if self.timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be > 0, got {self.timeout_seconds}")
        if self.image_max_size_bytes <= 0:
            raise ValueError(f"image_max_size_bytes must be > 0, got {self.image_max_size_bytes}")
        if self.image_max_concurrent < 1:
            raise ValueError(f"image_max_concurrent must be >= 1, got {self.image_max_concurrent}")

    def __repr__(self) -> str:
        """Mask the token to prevent accidental credential leakage."""
        parts: list[str] = []
        for f in dataclasses.fields(self):
            val = getattr(self, f.name)
            if f.name == "token":
                masked = f"...{val[-4:]}" if len(val) >= 4 else "****"
                parts.append(f"token='{masked}'")
            else:
                parts.append(f"{f.name}={val!r}")
        return f"NotionifyConfig({', '.join(parts)})"
