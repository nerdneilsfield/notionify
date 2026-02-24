"""Parse Markdown and normalize to canonical AST tokens.

This module wraps mistune v3's AST renderer and normalises the raw token
stream into a well-defined set of canonical types used by the rest of the
converter pipeline.

Canonical block tokens:
    heading, paragraph, block_quote, list, list_item, task_list_item,
    block_code, table, thematic_break, block_math, html_block

Canonical inline tokens:
    text, strong, emphasis, codespan, strikethrough, link, image,
    inline_math, softbreak, linebreak, html_inline
"""

from __future__ import annotations

import mistune

# ---------------------------------------------------------------------------
# Mistune-to-canonical type mapping
# ---------------------------------------------------------------------------

_BLOCK_TYPE_MAP: dict[str, str] = {
    "heading": "heading",
    "paragraph": "paragraph",
    "block_quote": "block_quote",
    "list": "list",
    "list_item": "list_item",
    "task_list_item": "task_list_item",
    "block_code": "block_code",
    "table": "table",
    "thematic_break": "thematic_break",
    "block_math": "block_math",
    "block_html": "html_block",
    # Internal mistune types that should be normalized
    "block_text": "paragraph",
}

_INLINE_TYPE_MAP: dict[str, str] = {
    "text": "text",
    "strong": "strong",
    "emphasis": "emphasis",
    "codespan": "codespan",
    "strikethrough": "strikethrough",
    "link": "link",
    "image": "image",
    "inline_math": "inline_math",
    "softbreak": "softbreak",
    "linebreak": "linebreak",
    "inline_html": "html_inline",
}

# Types that should be silently skipped during normalization
_SKIP_TYPES: frozenset[str] = frozenset({
    "blank_line",
})


class ASTNormalizer:
    """Parse Markdown and normalize to canonical AST tokens."""

    def __init__(self) -> None:
        self._parser = mistune.create_markdown(
            renderer="ast",
            plugins=[
                "strikethrough",
                "table",
                "task_lists",
                "url",
                "math",
                "footnotes",
            ],
        )

    def parse(self, markdown: str) -> list[dict]:
        """Parse markdown and return normalized AST token list."""
        raw_tokens = self._parser(markdown)
        if isinstance(raw_tokens, str):
            return []
        return self._normalize_tokens(raw_tokens)

    def _normalize_tokens(self, tokens: list[dict]) -> list[dict]:
        """Walk the token tree and normalize every node."""
        result: list[dict] = []
        for token in tokens:
            normalized = self._normalize_token(token)
            if normalized is not None:
                result.append(normalized)
        return result

    def _normalize_token(self, token: dict) -> dict | None:
        """Normalize a single token, returning None if it should be skipped."""
        raw_type = token.get("type", "")

        # Skip blank lines and other noise tokens
        if raw_type in _SKIP_TYPES:
            return None

        # Handle footnotes: expand footnote items inline as paragraphs
        if raw_type == "footnotes":
            return None

        # Handle footnote references: render as text "[^key]"
        if raw_type == "footnote_ref":
            key = token.get("raw", token.get("attrs", {}).get("index", "?"))
            return {"type": "text", "raw": f"[^{key}]"}

        # Map block types
        if raw_type in _BLOCK_TYPE_MAP:
            return self._normalize_block(token, _BLOCK_TYPE_MAP[raw_type])

        # Map inline types
        if raw_type in _INLINE_TYPE_MAP:
            return self._normalize_inline(token, _INLINE_TYPE_MAP[raw_type])

        # Table sub-types pass through for the table builder
        if raw_type in ("table_head", "table_body", "table_row", "table_cell"):
            return self._normalize_table_part(token)

        # "raw" type used inside codespan children, block_code etc.
        if raw_type == "raw":
            return {"type": "text", "raw": token.get("raw", "")}

        # Unknown token: skip silently
        return None

    def _normalize_block(self, token: dict, canonical_type: str) -> dict:
        """Normalize a block-level token."""
        result: dict = {"type": canonical_type}

        # Copy attrs
        attrs = token.get("attrs")
        if attrs:
            result["attrs"] = dict(attrs)

        # Handle block_code: mistune v3 stores code in "raw" directly
        if canonical_type == "block_code":
            raw_code = token.get("raw", "")
            # Strip trailing newline added by mistune
            if raw_code.endswith("\n"):
                raw_code = raw_code[:-1]
            result["raw"] = raw_code
            # Extract language info
            if attrs and attrs.get("info"):
                result.setdefault("attrs", {})["info"] = attrs["info"]
            return result

        # Handle block_math: mistune v3 stores expression in "raw"
        if canonical_type == "block_math":
            result["raw"] = token.get("raw", "")
            return result

        # Handle html_block: raw HTML content
        if canonical_type == "html_block":
            result["raw"] = token.get("raw", "")
            return result

        # Handle thematic_break: no children/attrs needed
        if canonical_type == "thematic_break":
            return result

        # Recursively normalize children
        children = token.get("children")
        if children:
            result["children"] = self._normalize_tokens(children)

        return result

    def _normalize_inline(self, token: dict, canonical_type: str) -> dict:
        """Normalize an inline-level token."""
        result: dict = {"type": canonical_type}

        # text, softbreak, linebreak have no children
        if canonical_type in ("text", "softbreak", "linebreak"):
            if "raw" in token:
                result["raw"] = token["raw"]
            return result

        # html_inline: just carries raw HTML
        if canonical_type == "html_inline":
            result["raw"] = token.get("raw", "")
            return result

        # codespan: mistune v3 stores code in "raw" directly
        if canonical_type == "codespan":
            result["raw"] = token.get("raw", "")
            return result

        # inline_math: mistune v3 stores expression in "raw"
        if canonical_type == "inline_math":
            result["raw"] = token.get("raw", "")
            return result

        # Copy attrs (url, title, alt for link/image)
        attrs = token.get("attrs")
        if attrs:
            result["attrs"] = dict(attrs)

        # Recursively normalize children
        children = token.get("children")
        if children:
            result["children"] = self._normalize_tokens(children)

        return result

    def _normalize_table_part(self, token: dict) -> dict:
        """Normalize table sub-structure tokens (head, body, row, cell)."""
        result: dict = {"type": token["type"]}

        attrs = token.get("attrs")
        if attrs:
            result["attrs"] = dict(attrs)

        children = token.get("children")
        if children:
            result["children"] = self._normalize_tokens(children)

        return result
