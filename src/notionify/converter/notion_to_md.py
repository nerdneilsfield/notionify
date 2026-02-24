"""Notion block tree to Markdown renderer.

Converts a list of Notion API block objects (dicts) into a Markdown
string.  Handles all block types from PRD section 11.1, inline
annotation rendering (section 11.2), table rendering (section 11.3),
and unsupported block fallback (section 11.6).

Usage::

    from notionify.config import NotionifyConfig
    from notionify.converter.notion_to_md import NotionToMarkdownRenderer

    renderer = NotionToMarkdownRenderer(NotionifyConfig())
    md = renderer.render_blocks(blocks)
"""

from __future__ import annotations

from collections.abc import Callable as _Callable

from notionify.config import NotionifyConfig
from notionify.errors import NotionifyUnsupportedBlockError
from notionify.models import ConversionWarning

from .inline_renderer import markdown_escape, render_rich_text

# Block types that are silently omitted (no Markdown equivalent).
_OMITTED_TYPES: frozenset[str] = frozenset({
    "breadcrumb",
    "table_of_contents",
})

# Block types whose children are simply concatenated (layout wrappers).
_PASSTHROUGH_TYPES: frozenset[str] = frozenset({
    "column_list",
    "column",
    "synced_block",
    "template",
})

# Media block types rendered as ``[Label](url)``.
_MEDIA_TYPES: dict[str, str] = {
    "video": "Video",
    "audio": "Audio",
    "pdf": "PDF",
}


class NotionToMarkdownRenderer:
    """Stateful renderer that converts Notion blocks to Markdown.

    The renderer accumulates :class:`ConversionWarning` instances in
    :attr:`warnings` during a :meth:`render_blocks` call so that callers
    can inspect non-fatal issues after rendering completes.

    Parameters
    ----------
    config:
        SDK configuration controlling export behaviour such as
        ``unsupported_block_policy``, ``detect_latex_code``, and
        ``image_expiry_warnings``.
    """

    def __init__(self, config: NotionifyConfig) -> None:
        self._config = config
        self.warnings: list[ConversionWarning] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render_blocks(self, blocks: list[dict], depth: int = 0) -> str:
        """Render a list of Notion blocks to a Markdown string.

        Parameters
        ----------
        blocks:
            Notion block objects (dicts as returned by the API).
        depth:
            Current nesting depth.  Top-level blocks are depth 0.

        Returns
        -------
        str
            The rendered Markdown text.
        """
        self.warnings = []
        return self._render_block_list(blocks, depth)

    def render_block(self, block: dict, depth: int = 0) -> str:
        """Render a single Notion block to Markdown.

        Parameters
        ----------
        block:
            A Notion block object (dict).
        depth:
            Current nesting depth.

        Returns
        -------
        str
            The rendered Markdown fragment.
        """
        return self._dispatch(block, depth)

    # ------------------------------------------------------------------
    # Internal: dispatch and list iteration
    # ------------------------------------------------------------------

    def _render_block_list(self, blocks: list[dict], depth: int) -> str:
        """Render an ordered list of blocks, handling numbered-list numbering."""
        parts: list[str] = []
        numbered_counter = 0

        for block in blocks:
            block_type = block.get("type", "")

            if block_type == "numbered_list_item":
                numbered_counter += 1
                parts.append(self._render_numbered_list_item(block, depth, numbered_counter))
            else:
                numbered_counter = 0
                parts.append(self._dispatch(block, depth))

        return "".join(parts)

    def _dispatch(self, block: dict, depth: int) -> str:
        """Route a block to the appropriate type-specific renderer."""
        block_type = block.get("type", "")

        # --- Omitted types (no Markdown equivalent) ----------------------
        if block_type in _OMITTED_TYPES:
            return ""

        # --- Pass-through layout wrappers --------------------------------
        if block_type in _PASSTHROUGH_TYPES:
            return self._render_passthrough(block, depth)

        # --- Type-specific renderers -------------------------------------
        renderer = _BLOCK_RENDERERS.get(block_type)
        if renderer is not None:
            return renderer(self, block, depth)

        # --- Media types -------------------------------------------------
        if block_type in _MEDIA_TYPES:
            return self._render_media(block, depth, block_type)

        # --- Unsupported -------------------------------------------------
        return self._render_unsupported(block)

    # ------------------------------------------------------------------
    # Block type renderers
    # ------------------------------------------------------------------

    def _render_heading(self, block: dict, depth: int, level: int) -> str:
        block_data = block.get(f"heading_{level}", {})
        text = render_rich_text(block_data.get("rich_text", []))
        prefix = "#" * level
        return f"{prefix} {text}\n\n"

    def _render_heading_1(self, block: dict, depth: int) -> str:
        return self._render_heading(block, depth, 1)

    def _render_heading_2(self, block: dict, depth: int) -> str:
        return self._render_heading(block, depth, 2)

    def _render_heading_3(self, block: dict, depth: int) -> str:
        return self._render_heading(block, depth, 3)

    def _render_paragraph(self, block: dict, depth: int) -> str:
        block_data = block.get("paragraph", {})
        text = render_rich_text(block_data.get("rich_text", []))
        indent = "  " * depth
        result = f"{indent}{text}\n\n"

        # Render children if present
        children = block_data.get("children") or block.get("children")
        if children:
            result += self._render_block_list(children, depth + 1)
        return result

    def _render_quote(self, block: dict, depth: int) -> str:
        block_data = block.get("quote", {})
        text = render_rich_text(block_data.get("rich_text", []))
        prefix = "> " * (depth + 1) if depth > 0 else "> "
        lines = text.split("\n")
        result = "\n".join(f"{prefix}{line}" for line in lines) + "\n\n"

        # Render nested children as deeper quotes
        children = block_data.get("children") or block.get("children")
        if children:
            child_md = self._render_block_list(children, depth + 1)
            # Prefix each line of child content with >
            child_lines = child_md.rstrip("\n").split("\n")
            result = result.rstrip("\n") + "\n"
            for line in child_lines:
                if line.strip():
                    result += f"{prefix}{line}\n"
                else:
                    result += f"{prefix.rstrip()}\n"
            result += "\n"
        return result

    def _render_bulleted_list_item(self, block: dict, depth: int) -> str:
        block_data = block.get("bulleted_list_item", {})
        text = render_rich_text(block_data.get("rich_text", []))
        indent = "  " * depth
        result = f"{indent}- {text}\n"

        # Nested children
        children = block_data.get("children") or block.get("children")
        if children:
            result += self._render_block_list(children, depth + 1)
        return result

    def _render_numbered_list_item(
        self, block: dict, depth: int, number: int
    ) -> str:
        block_data = block.get("numbered_list_item", {})
        text = render_rich_text(block_data.get("rich_text", []))
        indent = "  " * depth
        result = f"{indent}{number}. {text}\n"

        # Nested children
        children = block_data.get("children") or block.get("children")
        if children:
            result += self._render_block_list(children, depth + 1)
        return result

    def _render_to_do(self, block: dict, depth: int) -> str:
        block_data = block.get("to_do", {})
        text = render_rich_text(block_data.get("rich_text", []))
        checked = block_data.get("checked", False)
        checkbox = "[x]" if checked else "[ ]"
        indent = "  " * depth
        result = f"{indent}- {checkbox} {text}\n"

        children = block_data.get("children") or block.get("children")
        if children:
            result += self._render_block_list(children, depth + 1)
        return result

    def _render_code(self, block: dict, depth: int) -> str:
        block_data = block.get("code", {})
        language = block_data.get("language", "")
        # Notion uses "plain text" for unspecified language
        if language == "plain text":
            language = ""

        # Get code content from rich_text
        rich_text = block_data.get("rich_text", [])
        code_text = "".join(seg.get("plain_text", "") for seg in rich_text)

        # detect_latex_code: treat code blocks with language="latex" as math
        if self._config.detect_latex_code and language == "latex":
            return f"$$\n{code_text}\n$$\n\n"

        return f"```{language}\n{code_text}\n```\n\n"

    def _render_divider(self, block: dict, depth: int) -> str:
        return "---\n\n"

    def _render_equation(self, block: dict, depth: int) -> str:
        block_data = block.get("equation", {})
        expression = block_data.get("expression", "")
        return f"$$\n{expression}\n$$\n\n"

    def _render_table(self, block: dict, depth: int) -> str:
        """Render a Notion table block to GFM table syntax.

        Implements the algorithm from PRD section 11.3.

        The table block itself contains ``table_width`` and
        ``has_column_header``.  Row data lives in child ``table_row``
        blocks, each with a ``cells`` array of rich_text arrays.
        """
        block_data = block.get("table", {})
        col_count = block_data.get("table_width", 0)

        # Rows are provided as children
        children = block_data.get("children") or block.get("children")
        if not children:
            return ""

        # Filter to table_row children
        rows = [c for c in children if c.get("type") == "table_row"]
        if not rows:
            return ""

        lines: list[str] = []

        for i, row in enumerate(rows):
            row_data = row.get("table_row", {})
            cells = row_data.get("cells", [])

            # Render each cell's rich_text
            rendered_cells: list[str] = []
            for cell in cells:
                rendered_cells.append(render_rich_text(cell))

            # Pad to col_count if needed
            while len(rendered_cells) < col_count:
                rendered_cells.append("")

            line = "| " + " | ".join(rendered_cells) + " |"
            lines.append(line)

            # GFM requires a separator row after the first row for table recognition.
            if i == 0:
                separator = "|" + "|".join(["---"] * col_count) + "|"
                lines.append(separator)

        return "\n".join(lines) + "\n\n"

    def _render_image(self, block: dict, depth: int) -> str:
        block_data = block.get("image", {})
        image_type = block_data.get("type", "")

        # Get URL from the appropriate sub-object
        url = ""
        if image_type == "external":
            url = block_data.get("external", {}).get("url", "")
        elif image_type == "file":
            url = block_data.get("file", {}).get("url", "")

        # Caption from rich_text in the image's caption field
        caption_segments = block_data.get("caption", [])
        caption = render_rich_text(caption_segments) if caption_segments else ""

        escaped_url = markdown_escape(url, "url")
        result = f"![{caption}]({escaped_url})"

        # Optional expiry warning for Notion-hosted (file) images
        if image_type == "file" and self._config.image_expiry_warnings:
            expiry_time = block_data.get("file", {}).get("expiry_time", "")
            if expiry_time:
                result += f"\n<!-- notion-image-expiry: {expiry_time} -->"
                self.warnings.append(
                    ConversionWarning(
                        code="IMAGE_EXPIRY",
                        message=(
                            f"Notion-hosted image URL will expire at {expiry_time}"
                        ),
                        context={
                            "url": url,
                            "expiry_time": expiry_time,
                            "block_id": block.get("id", ""),
                        },
                    )
                )

        return result + "\n\n"

    def _render_callout(self, block: dict, depth: int) -> str:
        block_data = block.get("callout", {})
        text = render_rich_text(block_data.get("rich_text", []))

        # Extract icon (emoji or external URL)
        icon = block_data.get("icon", {})
        icon_str = ""
        if icon:
            icon_type = icon.get("type", "")
            if icon_type == "emoji":
                icon_str = icon.get("emoji", "")
            elif icon_type == "external":
                icon_str = icon.get("external", {}).get("url", "")

        if icon_str:
            content = f"{icon_str} {text}"
        else:
            content = text

        lines = content.split("\n")
        result = "\n".join(f"> {line}" for line in lines) + "\n\n"

        # Render children inside the callout blockquote
        children = block_data.get("children") or block.get("children")
        if children:
            child_md = self._render_block_list(children, depth + 1)
            child_lines = child_md.rstrip("\n").split("\n")
            result = result.rstrip("\n") + "\n"
            for line in child_lines:
                if line.strip():
                    result += f"> {line}\n"
                else:
                    result += ">\n"
            result += "\n"

        return result

    def _render_toggle(self, block: dict, depth: int) -> str:
        block_data = block.get("toggle", {})
        text = render_rich_text(block_data.get("rich_text", []))
        indent = "  " * depth
        result = f"{indent}- {text}\n"

        children = block_data.get("children") or block.get("children")
        if children:
            result += self._render_block_list(children, depth + 1)
        return result

    def _render_child_page(self, block: dict, depth: int) -> str:
        block_data = block.get("child_page", {})
        title = block_data.get("title", "Untitled")
        block_id = block.get("id", "")
        # Build a notion URL from the block ID
        url = _notion_url(block_id)
        escaped_title = markdown_escape(title)
        return f"[Page: {escaped_title}]({url})\n\n"

    def _render_child_database(self, block: dict, depth: int) -> str:
        block_data = block.get("child_database", {})
        title = block_data.get("title", "Untitled")
        block_id = block.get("id", "")
        url = _notion_url(block_id)
        escaped_title = markdown_escape(title)
        return f"[Database: {escaped_title}]({url})\n\n"

    def _render_embed(self, block: dict, depth: int) -> str:
        block_data = block.get("embed", {})
        url = block_data.get("url", "")
        escaped_url = markdown_escape(url, "url")
        return f"[Embed]({escaped_url})\n\n"

    def _render_bookmark(self, block: dict, depth: int) -> str:
        block_data = block.get("bookmark", {})
        url = block_data.get("url", "")
        escaped_url = markdown_escape(url, "url")

        # Caption serves as a description
        caption_segments = block_data.get("caption", [])
        caption = render_rich_text(caption_segments) if caption_segments else ""

        # Title: use the URL as the display text (Notion bookmarks
        # don't store a separate title in the block data).
        result = f"[{url}]({escaped_url})"

        if caption:
            result += f"\n> {caption}"

        return result + "\n\n"

    def _render_link_preview(self, block: dict, depth: int) -> str:
        block_data = block.get("link_preview", {})
        url = block_data.get("url", "")
        escaped_url = markdown_escape(url, "url")
        return f"[{url}]({escaped_url})\n\n"

    def _render_file(self, block: dict, depth: int) -> str:
        block_data = block.get("file", {})
        file_type = block_data.get("type", "")

        url = ""
        if file_type == "external":
            url = block_data.get("external", {}).get("url", "")
        elif file_type == "file":
            url = block_data.get("file", {}).get("url", "")

        # Try to get a filename from the caption or name field
        name = block_data.get("name", "")
        caption_segments = block_data.get("caption", [])
        if caption_segments:
            name = render_rich_text(caption_segments)
        if not name:
            name = url.rsplit("/", 1)[-1].split("?")[0] if url else "File"

        escaped_url = markdown_escape(url, "url")
        return f"[{name}]({escaped_url})\n\n"

    def _render_media(self, block: dict, depth: int, block_type: str) -> str:
        """Render video/audio/pdf blocks as [Label](url)."""
        block_data = block.get(block_type, {})
        media_type = block_data.get("type", "")

        url = ""
        if media_type == "external":
            url = block_data.get("external", {}).get("url", "")
        elif media_type == "file":
            url = block_data.get("file", {}).get("url", "")

        label = _MEDIA_TYPES.get(block_type, block_type.capitalize())
        escaped_url = markdown_escape(url, "url")
        return f"[{label}]({escaped_url})\n\n"

    def _render_passthrough(self, block: dict, depth: int) -> str:
        """Render layout wrappers by concatenating their children."""
        block_type = block.get("type", "")
        block_data = block.get(block_type, {})
        children = block_data.get("children") or block.get("children")
        if children:
            return self._render_block_list(children, depth)
        return ""

    # ------------------------------------------------------------------
    # Unsupported block fallback (PRD section 11.6)
    # ------------------------------------------------------------------

    def _render_unsupported(self, block: dict) -> str:
        """Handle block types with no Markdown equivalent.

        Behaviour is governed by ``config.unsupported_block_policy``:

        * ``"comment"`` -- emit an HTML comment with best-effort text.
        * ``"skip"`` -- silently omit.
        * ``"raise"`` -- raise :class:`NotionifyUnsupportedBlockError`.
        """
        policy = self._config.unsupported_block_policy
        block_type = block.get("type", "unknown")
        block_id = block.get("id", "")

        if policy == "skip":
            return ""

        if policy == "raise":
            raise NotionifyUnsupportedBlockError(
                message=f"Cannot render block type: {block_type}",
                context={"block_id": block_id, "block_type": block_type},
            )

        # Default: "comment"
        text = _extract_plain_text(block)
        if text:
            return f"<!-- notion:{block_type} -->\n{text}\n\n"
        return f"<!-- notion:{block_type} -->\n\n"


# ------------------------------------------------------------------
# Block renderer dispatch table
# ------------------------------------------------------------------

_BlockRenderer = _Callable[["NotionToMarkdownRenderer", dict, int], str]

_BLOCK_RENDERERS: dict[str, _BlockRenderer] = {
    "heading_1": NotionToMarkdownRenderer._render_heading_1,
    "heading_2": NotionToMarkdownRenderer._render_heading_2,
    "heading_3": NotionToMarkdownRenderer._render_heading_3,
    "paragraph": NotionToMarkdownRenderer._render_paragraph,
    "quote": NotionToMarkdownRenderer._render_quote,
    "bulleted_list_item": NotionToMarkdownRenderer._render_bulleted_list_item,
    # numbered_list_item is handled specially in _render_block_list
    "to_do": NotionToMarkdownRenderer._render_to_do,
    "code": NotionToMarkdownRenderer._render_code,
    "divider": NotionToMarkdownRenderer._render_divider,
    "equation": NotionToMarkdownRenderer._render_equation,
    "table": NotionToMarkdownRenderer._render_table,
    "image": NotionToMarkdownRenderer._render_image,
    "callout": NotionToMarkdownRenderer._render_callout,
    "toggle": NotionToMarkdownRenderer._render_toggle,
    "child_page": NotionToMarkdownRenderer._render_child_page,
    "child_database": NotionToMarkdownRenderer._render_child_database,
    "embed": NotionToMarkdownRenderer._render_embed,
    "bookmark": NotionToMarkdownRenderer._render_bookmark,
    "link_preview": NotionToMarkdownRenderer._render_link_preview,
    "file": NotionToMarkdownRenderer._render_file,
}

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _notion_url(block_id: str) -> str:
    """Build a Notion URL from a block/page ID."""
    clean_id = block_id.replace("-", "")
    return f"https://notion.so/{clean_id}"


def _extract_plain_text(block: dict) -> str:
    """Best-effort extraction of plain text from any block.

    Looks for ``rich_text`` in the type-specific data, or falls back
    to checking common sub-keys.
    """
    block_type = block.get("type", "")
    block_data = block.get(block_type, {})

    if isinstance(block_data, dict):
        rich_text = block_data.get("rich_text", [])
        if rich_text:
            return "".join(seg.get("plain_text", "") for seg in rich_text)

    return ""
