"""Convert normalized AST tokens to Notion block dicts.

This module handles ALL block types from PRD section 10.1:

- heading (levels 1-3 map to heading_1/2/3; level 4+ per heading_overflow)
- paragraph -> paragraph block with rich_text
- block_quote -> quote block (nested supported via children)
- list -> bulleted_list_item / numbered_list_item with nesting
- task_list_item -> to_do with checked state
- block_code -> code block with language
- thematic_break -> divider
- table -> delegate to tables.py
- block_math -> delegate to math.py
- image -> detect source, create image block or PendingImage
- html_block -> skip with warning
"""

from __future__ import annotations

import re
from collections.abc import Callable as _Callable
from urllib.parse import urlparse

from notionify.config import NotionifyConfig
from notionify.converter.math import build_block_math
from notionify.converter.rich_text import build_rich_text, split_rich_text
from notionify.converter.tables import build_table
from notionify.models import ConversionWarning, ImageSourceType, PendingImage

# ---------------------------------------------------------------------------
# Notion code language mapping
# ---------------------------------------------------------------------------

# Notion API accepts a specific set of language identifiers.
# Map common aliases to Notion-accepted values.
_NOTION_LANGUAGES: frozenset[str] = frozenset({
    "abap", "arduino", "bash", "basic", "c", "clojure", "coffeescript",
    "c++", "c#", "css", "dart", "diff", "docker", "elixir", "elm",
    "erlang", "flow", "fortran", "f#", "gherkin", "glsl", "go", "graphql",
    "groovy", "haskell", "html", "java", "javascript", "json", "julia",
    "kotlin", "latex", "less", "lisp", "livescript", "lua", "makefile",
    "markdown", "markup", "matlab", "mermaid", "nix", "objective-c",
    "ocaml", "pascal", "perl", "php", "plain text", "powershell",
    "prolog", "protobuf", "python", "r", "reason", "ruby", "rust",
    "sass", "scala", "scheme", "scss", "shell", "sql", "swift",
    "typescript", "vb.net", "verilog", "vhdl", "visual basic",
    "webassembly", "xml", "yaml", "java/c/c++/c#",
})

_LANGUAGE_ALIASES: dict[str, str] = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "sh": "shell",
    "zsh": "shell",
    "rb": "ruby",
    "rs": "rust",
    "yml": "yaml",
    "md": "markdown",
    "cs": "c#",
    "cpp": "c++",
    "objc": "objective-c",
    "objective_c": "objective-c",
    "dockerfile": "docker",
    "make": "makefile",
    "tex": "latex",
    "htm": "html",
    "jsx": "javascript",
    "tsx": "typescript",
    "jsonc": "json",
    "vb": "visual basic",
    "fs": "f#",
    "fsharp": "f#",
    "csharp": "c#",
    "golang": "go",
    "hs": "haskell",
    "kt": "kotlin",
    "pl": "perl",
    "ps1": "powershell",
    "psm1": "powershell",
    "asm": "webassembly",
    "wasm": "webassembly",
}


def _normalize_language(info: str | None) -> str:
    """Map a code fence info string to a Notion-accepted language name."""
    if not info:
        return "plain text"
    lang = info.strip().lower()
    # Sometimes the info string has extra words (e.g. "python3")
    lang = lang.split()[0] if lang else "plain text"
    if lang in _NOTION_LANGUAGES:
        return lang
    if lang in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[lang]
    # Strip trailing digits (e.g. "python3" -> "python")
    stripped = re.sub(r"\d+$", "", lang)
    if stripped in _NOTION_LANGUAGES:
        return stripped
    if stripped in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[stripped]
    return "plain text"


# ---------------------------------------------------------------------------
# Image source detection
# ---------------------------------------------------------------------------

def _classify_image_source(url: str) -> ImageSourceType:
    """Classify an image URL as external, local, data URI, or unknown."""
    if not url:
        return ImageSourceType.UNKNOWN
    if url.startswith("data:"):
        return ImageSourceType.DATA_URI
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        return ImageSourceType.EXTERNAL_URL
    if parsed.scheme in ("file", "") and (not parsed.netloc or parsed.netloc == "localhost"):
        # Relative or absolute file paths (including file://localhost/...)
        return ImageSourceType.LOCAL_FILE
    return ImageSourceType.UNKNOWN


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_blocks(
    tokens: list[dict],
    config: NotionifyConfig,
) -> tuple[list[dict], list[PendingImage], list[ConversionWarning]]:
    """Convert normalized AST tokens to Notion block dicts.

    Parameters
    ----------
    tokens:
        List of canonical AST tokens from :class:`ASTNormalizer`.
    config:
        SDK configuration.

    Returns
    -------
    tuple[list[dict], list[PendingImage], list[ConversionWarning]]
        (blocks, pending_images, warnings)
    """
    ctx = _BuildContext(config)
    _process_tokens(tokens, ctx)
    return ctx.blocks, ctx.images, ctx.warnings


class _BuildContext:
    """Mutable accumulator for the block building pass."""

    __slots__ = ("blocks", "config", "images", "warnings")

    def __init__(self, config: NotionifyConfig) -> None:
        self.config = config
        self.blocks: list[dict] = []
        self.images: list[PendingImage] = []
        self.warnings: list[ConversionWarning] = []

    def add_block(self, block: dict) -> int:
        """Append a block and return its index."""
        idx = len(self.blocks)
        self.blocks.append(block)
        return idx

    def add_warning(self, code: str, message: str, **context: object) -> None:
        self.warnings.append(ConversionWarning(
            code=code, message=message, context=dict(context),
        ))

    def add_image(self, src: str, source_type: ImageSourceType, block_index: int) -> None:
        self.images.append(PendingImage(
            src=src, source_type=source_type, block_index=block_index,
        ))


# ---------------------------------------------------------------------------
# Token dispatch
# ---------------------------------------------------------------------------

def _process_tokens(tokens: list[dict], ctx: _BuildContext) -> list[dict]:
    """Process a list of tokens and return the blocks produced.

    The blocks are also added to ctx.blocks.  The return value is the
    subset of blocks produced by *this* call (useful for nesting).
    """
    produced: list[dict] = []
    for token in tokens:
        new_blocks = _process_token(token, ctx)
        produced.extend(new_blocks)
    return produced


def _process_token(token: dict, ctx: _BuildContext) -> list[dict]:
    """Process a single token and return the block(s) produced."""
    token_type = token.get("type", "")
    handler = _BLOCK_HANDLERS.get(token_type)
    if handler is not None:
        return handler(token, ctx)
    # Unknown block type
    if token_type:
        ctx.add_warning(
            "UNKNOWN_TOKEN",
            f"Unknown token type '{token_type}' was skipped.",
        )
    return []


# ---------------------------------------------------------------------------
# Block builders
# ---------------------------------------------------------------------------

def _build_heading(token: dict, ctx: _BuildContext) -> list[dict]:
    """Build a Notion heading block."""
    level = token.get("attrs", {}).get("level", 1)
    children = token.get("children", [])
    rich_text = build_rich_text(children, ctx.config, warnings=ctx.warnings)
    rich_text = split_rich_text(rich_text)

    if level <= 3:
        heading_type = f"heading_{level}"
        block = {
            "object": "block",
            "type": heading_type,
            heading_type: {
                "rich_text": rich_text,
                "color": "default",
                "is_toggleable": False,
            },
        }
    elif ctx.config.heading_overflow == "downgrade":
        # Clamp to heading_3
        block = {
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": rich_text,
                "color": "default",
                "is_toggleable": False,
            },
        }
    else:
        # heading_overflow == "paragraph": render as bold paragraph
        for seg in rich_text:
            if seg.get("type") == "text":
                annots = seg.setdefault("annotations", {
                    "bold": False, "italic": False, "strikethrough": False,
                    "underline": False, "code": False, "color": "default",
                })
                annots["bold"] = True
        block = {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": rich_text,
                "color": "default",
            },
        }

    ctx.add_block(block)
    return [block]


def _build_paragraph(token: dict, ctx: _BuildContext) -> list[dict]:
    """Build a Notion paragraph block, or an image block if the paragraph
    contains only a single image token.
    """
    children = token.get("children", [])

    # Special case: paragraph with single image child becomes an image block
    if len(children) == 1 and children[0].get("type") == "image":
        return _build_image_block(children[0], ctx)

    rich_text = build_rich_text(children, ctx.config, warnings=ctx.warnings)
    rich_text = split_rich_text(rich_text)

    # Don't create empty paragraphs
    if not rich_text:
        return []

    block = {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": rich_text,
            "color": "default",
        },
    }
    ctx.add_block(block)
    return [block]


def _build_block_quote(token: dict, ctx: _BuildContext) -> list[dict]:
    """Build a Notion quote block.

    Notion quote blocks support children, so we recursively process
    the block_quote's children and attach block-level children as
    nested blocks, while inline content becomes the quote's rich_text.
    """
    children = token.get("children", [])

    # Collect all inline content from paragraph children for the quote's rich_text,
    # and any non-paragraph children as nested blocks.
    all_rich_text: list[dict] = []
    nested_blocks: list[dict] = []

    for child in children:
        child_type = child.get("type", "")
        if child_type == "paragraph":
            inline_children = child.get("children", [])
            if all_rich_text:
                # Separate paragraphs with newlines
                all_rich_text.append({
                    "type": "text",
                    "text": {"content": "\n"},
                })
            rt = build_rich_text(inline_children, ctx.config, warnings=ctx.warnings)
            all_rich_text.extend(rt)
        else:
            # Non-paragraph child: build as nested block
            nested_ctx = _BuildContext(ctx.config)
            child_blocks = _process_token(child, nested_ctx)
            nested_blocks.extend(child_blocks)
            ctx.images.extend(nested_ctx.images)
            ctx.warnings.extend(nested_ctx.warnings)

    all_rich_text = split_rich_text(all_rich_text)

    block: dict = {
        "object": "block",
        "type": "quote",
        "quote": {
            "rich_text": all_rich_text or [],
            "color": "default",
        },
    }

    if nested_blocks:
        block["quote"]["children"] = nested_blocks

    ctx.add_block(block)
    return [block]


_MAX_NESTING_DEPTH = 8
"""Practical nesting limit for Notion blocks (PRD 5.1)."""


def _build_list(token: dict, ctx: _BuildContext, depth: int = 0) -> list[dict]:
    """Build list item blocks from a list token.

    In Notion, there are no "list" wrapper blocks.  Each item is a
    top-level ``bulleted_list_item`` or ``numbered_list_item`` block,
    with child items nested inside via ``children``.
    """
    ordered = token.get("attrs", {}).get("ordered", False)
    items = token.get("children", [])
    blocks: list[dict] = []

    for item in items:
        item_type = item.get("type", "")

        if item_type == "task_list_item":
            block = _build_task_list_item(item, ctx, depth)
            blocks.append(block)
        elif item_type == "list_item":
            block = _build_list_item(item, ordered, ctx, depth)
            blocks.append(block)

    return blocks


def _build_list_item(
    token: dict,
    ordered: bool,
    ctx: _BuildContext,
    depth: int = 0,
) -> dict:
    """Build a single bulleted/numbered list item block."""
    children = token.get("children", [])
    block_type = "numbered_list_item" if ordered else "bulleted_list_item"

    # Separate inline content (from paragraph/block_text) and nested blocks.
    # Nested blocks must NOT be added to the top-level ctx.blocks — they
    # belong only as children of this list item.
    rich_text: list[dict] = []
    nested_blocks: list[dict] = []

    for child in children:
        child_type = child.get("type", "")
        if child_type in ("paragraph", "block_text"):
            rt = build_rich_text(child.get("children", []), ctx.config, warnings=ctx.warnings)
            rich_text.extend(rt)
        elif child_type == "list":
            if depth + 1 >= _MAX_NESTING_DEPTH:
                ctx.add_warning(
                    "NESTING_DEPTH_EXCEEDED",
                    f"Nesting depth exceeds {_MAX_NESTING_DEPTH} levels; "
                    "nested items flattened.",
                    depth=depth + 1,
                )
            else:
                # Nested list: build child items in a separate context so
                # they are not appended to the parent's flat block list
                nested_ctx = _BuildContext(ctx.config)
                nested = _build_list(child, nested_ctx, depth + 1)
                nested_blocks.extend(nested)
                ctx.images.extend(nested_ctx.images)
                ctx.warnings.extend(nested_ctx.warnings)
        else:
            # Other nested block
            nested_ctx = _BuildContext(ctx.config)
            child_blocks = _process_token(child, nested_ctx)
            nested_blocks.extend(child_blocks)
            ctx.images.extend(nested_ctx.images)
            ctx.warnings.extend(nested_ctx.warnings)

    rich_text = split_rich_text(rich_text)

    block: dict = {
        "object": "block",
        "type": block_type,
        block_type: {
            "rich_text": rich_text,
            "color": "default",
        },
    }

    if nested_blocks:
        block[block_type]["children"] = nested_blocks

    ctx.add_block(block)
    return block


def _build_task_list_item(
    token: dict, ctx: _BuildContext, depth: int = 0,
) -> dict:
    """Build a Notion to_do block from a task list item."""
    children = token.get("children", [])
    checked = token.get("attrs", {}).get("checked", False)

    rich_text: list[dict] = []
    nested_blocks: list[dict] = []

    for child in children:
        child_type = child.get("type", "")
        if child_type in ("paragraph", "block_text"):
            rt = build_rich_text(child.get("children", []), ctx.config, warnings=ctx.warnings)
            rich_text.extend(rt)
        elif child_type == "list":
            if depth + 1 >= _MAX_NESTING_DEPTH:
                ctx.add_warning(
                    "NESTING_DEPTH_EXCEEDED",
                    f"Nesting depth exceeds {_MAX_NESTING_DEPTH} levels; "
                    "nested items flattened.",
                    depth=depth + 1,
                )
            else:
                nested_ctx = _BuildContext(ctx.config)
                nested = _build_list(child, nested_ctx, depth + 1)
                nested_blocks.extend(nested)
                ctx.images.extend(nested_ctx.images)
                ctx.warnings.extend(nested_ctx.warnings)
        else:
            nested_ctx = _BuildContext(ctx.config)
            child_blocks = _process_token(child, nested_ctx)
            nested_blocks.extend(child_blocks)
            ctx.images.extend(nested_ctx.images)
            ctx.warnings.extend(nested_ctx.warnings)

    rich_text = split_rich_text(rich_text)

    block: dict = {
        "object": "block",
        "type": "to_do",
        "to_do": {
            "rich_text": rich_text,
            "checked": checked,
            "color": "default",
        },
    }

    if nested_blocks:
        block["to_do"]["children"] = nested_blocks

    ctx.add_block(block)
    return block


def _build_code_block(token: dict, ctx: _BuildContext) -> list[dict]:
    """Build a Notion code block."""
    raw = token.get("raw", "")
    info = token.get("attrs", {}).get("info")
    language = _normalize_language(info)

    # Split code content for the 2000-char rich_text limit
    rich_text = [{"type": "text", "text": {"content": raw}}]
    rich_text = split_rich_text(rich_text)

    block = {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": rich_text,
            "language": language,
            "caption": [],
        },
    }

    ctx.add_block(block)
    return [block]


def _build_divider(token: dict, ctx: _BuildContext) -> list[dict]:
    """Build a Notion divider block."""
    block = {
        "object": "block",
        "type": "divider",
        "divider": {},
    }
    ctx.add_block(block)
    return [block]


def _build_table(token: dict, ctx: _BuildContext) -> list[dict]:
    """Build a Notion table block by delegating to tables.py."""
    block, warnings = build_table(token, ctx.config)
    ctx.warnings.extend(warnings)
    if block is not None:
        ctx.add_block(block)
        return [block]
    return []


def _build_block_math(token: dict, ctx: _BuildContext) -> list[dict]:
    """Build Notion block(s) for block-level math."""
    expression = token.get("raw", "")
    blocks, warnings = build_block_math(expression, ctx.config)
    ctx.warnings.extend(warnings)
    for block in blocks:
        ctx.add_block(block)
    return blocks


def _build_image_block(token: dict, ctx: _BuildContext) -> list[dict]:
    """Build a Notion image block or register a PendingImage.

    For external URLs, creates an immediate image block.
    For local files and data URIs (when upload is enabled), creates a
    placeholder and registers a PendingImage.
    For unsupported sources, applies the image_fallback config.
    """
    url = token.get("attrs", {}).get("url", "")
    alt_children = token.get("children", [])
    alt_text = _extract_text(alt_children)
    source_type = _classify_image_source(url)

    if source_type == ImageSourceType.EXTERNAL_URL:
        image_data: dict = {
            "type": "external",
            "external": {"url": url},
        }
        if alt_text:
            image_data["caption"] = [
                {"type": "text", "text": {"content": alt_text}},
            ]
        block: dict = {"object": "block", "type": "image", "image": image_data}
        ctx.add_block(block)
        return [block]

    if source_type in (ImageSourceType.LOCAL_FILE, ImageSourceType.DATA_URI):
        if ctx.config.image_upload:
            # Create a placeholder block that will be patched by the upload pipeline
            placeholder_data: dict = {
                "type": "external",
                "external": {"url": "https://placeholder.notionify.invalid"},
            }
            if alt_text:
                placeholder_data["caption"] = [
                    {"type": "text", "text": {"content": alt_text}},
                ]
            block = {"object": "block", "type": "image", "image": placeholder_data}
            idx = ctx.add_block(block)
            ctx.add_image(url, source_type, idx)
            return [block]
        # Upload disabled: fall through to fallback
        return _apply_image_fallback(url, alt_text, ctx)

    # Unknown source type: apply fallback
    return _apply_image_fallback(url, alt_text, ctx)


def _apply_image_fallback(
    url: str,
    alt_text: str,
    ctx: _BuildContext,
) -> list[dict]:
    """Apply the image_fallback config when an image cannot be processed."""
    fallback = ctx.config.image_fallback

    if fallback == "skip":
        ctx.add_warning(
            "IMAGE_SKIPPED",
            f"Image was skipped: {url}",
            src=url,
        )
        return []

    if fallback == "placeholder":
        display = alt_text or url or "image"
        block = {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": f"[image: {display}]"}},
                ],
                "color": "default",
            },
        }
        ctx.add_block(block)
        ctx.add_warning(
            "IMAGE_PLACEHOLDER",
            f"Image replaced with placeholder: {url}",
            src=url,
        )
        return [block]

    # fallback == "raise": we add a warning but don't raise here.
    # The actual raising is done at the client level when processing images.
    ctx.add_warning(
        "IMAGE_ERROR",
        f"Image source could not be processed: {url}",
        src=url,
        fallback="raise",
    )
    return []


def _extract_text(children: list[dict]) -> str:
    """Recursively extract plain text from inline tokens."""
    parts: list[str] = []
    for token in children:
        token_type = token.get("type", "")
        if token_type == "text":
            parts.append(token.get("raw", ""))
        elif "children" in token:
            parts.append(_extract_text(token["children"]))
        elif "raw" in token:
            parts.append(token["raw"])
    return "".join(parts)


# ---------------------------------------------------------------------------
# Block handler dispatch table
# ---------------------------------------------------------------------------

def _handle_html_block(token: dict, ctx: _BuildContext) -> list[dict]:
    """Handle HTML blocks by emitting a warning and skipping."""
    ctx.add_warning(
        "HTML_BLOCK_SKIPPED",
        "HTML block was skipped (not supported by Notion).",
        raw=token.get("raw", "")[:200],
    )
    return []


_BlockHandler = _Callable[[dict, _BuildContext], list[dict]]

_BLOCK_HANDLERS: dict[str, _BlockHandler] = {
    "heading": _build_heading,
    "paragraph": _build_paragraph,
    "block_quote": _build_block_quote,
    "list": _build_list,  # type: ignore[dict-item]
    "block_code": _build_code_block,
    "thematic_break": _build_divider,
    "table": _build_table,
    "block_math": _build_block_math,
    "html_block": _handle_html_block,
}
