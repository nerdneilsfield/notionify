"""Advanced tests for callout and toggle block rendering.

Tests cover: emoji icons, external URL icons, no-icon callouts, rich text
within callouts, callout children (paragraphs, code, lists), toggle nesting,
toggle with multiple child types, and depth-based indentation.
"""
from __future__ import annotations

from notionify.config import NotionifyConfig
from notionify.converter.notion_to_md import NotionToMarkdownRenderer


def _cfg(**kw) -> NotionifyConfig:
    return NotionifyConfig(token="test-token", **kw)


def _txt(content: str, *, bold: bool = False, italic: bool = False, code: bool = False) -> dict:
    return {
        "type": "text",
        "text": {"content": content},
        "plain_text": content,
        "annotations": {
            "bold": bold,
            "italic": italic,
            "code": code,
            "strikethrough": False,
            "underline": False,
            "color": "default",
        },
        "href": None,
    }


def _para(text: str) -> dict:
    return {"type": "paragraph", "paragraph": {"rich_text": [_txt(text)]}}


def _code_block(content: str, lang: str = "python") -> dict:
    return {
        "type": "code",
        "code": {
            "rich_text": [_txt(content)],
            "language": lang,
        },
    }


def _bullet(text: str, children: list | None = None) -> dict:
    data: dict = {"rich_text": [_txt(text)]}
    if children:
        data["children"] = children
    return {"type": "bulleted_list_item", "bulleted_list_item": data}


def _callout(
    text: str,
    icon: dict | None = None,
    children: list | None = None,
    rich_text: list | None = None,
) -> dict:
    data: dict = {"rich_text": rich_text if rich_text is not None else [_txt(text)]}
    if icon is not None:
        data["icon"] = icon
    if children:
        data["children"] = children
    return {"type": "callout", "callout": data}


def _toggle(
    text: str,
    children: list | None = None,
    rich_text: list | None = None,
) -> dict:
    data: dict = {"rich_text": rich_text if rich_text is not None else [_txt(text)]}
    if children:
        data["children"] = children
    return {"type": "toggle", "toggle": data}


# ---------------------------------------------------------------------------
# Callout rendering
# ---------------------------------------------------------------------------


class TestCalloutEmojiIcon:
    """Callout blocks with emoji icons render icon prefix."""

    def test_emoji_icon_prepended_to_text(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _callout("Important note", icon={"type": "emoji", "emoji": "⚠️"})
        md = r.render_blocks([block])
        assert "> ⚠️ Important note" in md

    def test_emoji_icon_with_bold_text(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _callout(
            "",
            icon={"type": "emoji", "emoji": "💡"},
            rich_text=[_txt("Tip: ", bold=False), _txt("remember this", bold=True)],
        )
        md = r.render_blocks([block])
        assert "💡" in md
        assert "remember this" in md
        assert ">" in md

    def test_emoji_icon_fire(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _callout("Hot topic", icon={"type": "emoji", "emoji": "🔥"})
        md = r.render_blocks([block])
        assert "🔥 Hot topic" in md

    def test_callout_output_uses_blockquote_format(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _callout("Content here", icon={"type": "emoji", "emoji": "\u2139\ufe0f"})
        md = r.render_blocks([block])
        lines = [ln for ln in md.strip().splitlines() if ln.strip()]
        assert all(ln.startswith(">") for ln in lines)


class TestCalloutExternalIcon:
    """Callout blocks with external URL icons use URL as prefix."""

    def test_external_url_icon_prefix(self):
        r = NotionToMarkdownRenderer(_cfg())
        icon = {"type": "external", "external": {"url": "https://example.com/icon.png"}}
        block = _callout("See also", icon=icon)
        md = r.render_blocks([block])
        assert "https://example.com/icon.png" in md
        assert "See also" in md

    def test_external_icon_with_null_external_key(self):
        """external key is None — no icon prefix, no crash."""
        r = NotionToMarkdownRenderer(_cfg())
        icon = {"type": "external", "external": None}
        block = _callout("Graceful fallback", icon=icon)
        md = r.render_blocks([block])
        assert "Graceful fallback" in md


class TestCalloutNoIcon:
    """Callout without icon renders content only."""

    def test_no_icon_no_prefix(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _callout("Plain callout content")
        md = r.render_blocks([block])
        assert "> Plain callout content" in md

    def test_no_icon_still_blockquote(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _callout("Note without icon")
        md = r.render_blocks([block])
        assert md.strip().startswith(">")

    def test_empty_callout_text_no_crash(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = {"type": "callout", "callout": {"rich_text": []}}
        md = r.render_blocks([block])
        # Must not raise; output is a blockquote line
        assert "> " in md or md.strip() == ">"


class TestCalloutWithChildren:
    """Callout blocks with nested children include child content."""

    def test_paragraph_child_included(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _callout(
            "Header",
            icon={"type": "emoji", "emoji": "📌"},
            children=[_para("Child paragraph text")],
        )
        md = r.render_blocks([block])
        assert "Header" in md
        assert "Child paragraph text" in md

    def test_code_block_child_included(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _callout(
            "Code example",
            children=[_code_block("x = 42", lang="python")],
        )
        md = r.render_blocks([block])
        assert "Code example" in md
        assert "x = 42" in md

    def test_bullet_list_child_included(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _callout(
            "Steps",
            icon={"type": "emoji", "emoji": "📋"},
            children=[_bullet("Step one"), _bullet("Step two")],
        )
        md = r.render_blocks([block])
        assert "Step one" in md
        assert "Step two" in md

    def test_multiple_children_all_rendered(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _callout(
            "Multi-child callout",
            children=[
                _para("First child"),
                _para("Second child"),
                _para("Third child"),
            ],
        )
        md = r.render_blocks([block])
        assert "First child" in md
        assert "Second child" in md
        assert "Third child" in md

    def test_children_via_top_level_key(self):
        """Children stored at block level (not inside callout dict) also rendered."""
        r = NotionToMarkdownRenderer(_cfg())
        block = {
            "type": "callout",
            "callout": {"rich_text": [_txt("Top-level children")]},
            "children": [_para("Nested via block key")],
        }
        md = r.render_blocks([block])
        assert "Nested via block key" in md


class TestCalloutDoubleNewline:
    """Callout rendering ends with double newline for paragraph separation."""

    def test_callout_ends_with_double_newline(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _callout("Content")
        md = r.render_blocks([block])
        assert md.endswith("\n\n")

    def test_two_callouts_separated(self):
        r = NotionToMarkdownRenderer(_cfg())
        blocks = [
            _callout("First callout"),
            _callout("Second callout"),
        ]
        md = r.render_blocks(blocks)
        assert "First callout" in md
        assert "Second callout" in md
        # Two callouts should produce separate blockquote sections
        assert md.index("First callout") < md.index("Second callout")


# ---------------------------------------------------------------------------
# Toggle rendering
# ---------------------------------------------------------------------------


class TestToggleBasic:
    """Toggle blocks render as Markdown list items."""

    def test_toggle_no_children_renders_as_list_item(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _toggle("Click to reveal")
        md = r.render_blocks([block])
        assert "- Click to reveal" in md

    def test_toggle_empty_text_no_crash(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = {"type": "toggle", "toggle": {"rich_text": []}}
        md = r.render_blocks([block])
        assert "-" in md  # list marker present

    def test_toggle_with_bold_header(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _toggle("", rich_text=[_txt("Bold toggle", bold=True)])
        md = r.render_blocks([block])
        assert "**Bold toggle**" in md

    def test_toggle_with_italic_header(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _toggle("", rich_text=[_txt("Italic toggle", italic=True)])
        md = r.render_blocks([block])
        assert "_Italic toggle_" in md or "*Italic toggle*" in md

    def test_toggle_with_code_header(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _toggle("", rich_text=[_txt("func()", code=True)])
        md = r.render_blocks([block])
        assert "`func()`" in md


class TestToggleWithChildren:
    """Toggle blocks with nested children include child content."""

    def test_paragraph_child_rendered(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _toggle("Section header", children=[_para("Hidden text")])
        md = r.render_blocks([block])
        assert "Section header" in md
        assert "Hidden text" in md

    def test_code_block_child_rendered(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _toggle(
            "Code section",
            children=[_code_block("print('hello')", lang="python")],
        )
        md = r.render_blocks([block])
        assert "Code section" in md
        assert "print('hello')" in md
        assert "```python" in md

    def test_multiple_children_all_rendered(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _toggle(
            "Multi content",
            children=[
                _para("First paragraph"),
                _para("Second paragraph"),
                _bullet("A bullet point"),
            ],
        )
        md = r.render_blocks([block])
        assert "First paragraph" in md
        assert "Second paragraph" in md
        assert "A bullet point" in md

    def test_children_order_preserved(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _toggle(
            "Ordered",
            children=[_para("Alpha"), _para("Beta"), _para("Gamma")],
        )
        md = r.render_blocks([block])
        # All content present and in order
        idx_a = md.index("Alpha")
        idx_b = md.index("Beta")
        idx_g = md.index("Gamma")
        assert idx_a < idx_b < idx_g

    def test_children_via_top_level_key(self):
        """Children at block level (not inside toggle dict) are also rendered."""
        r = NotionToMarkdownRenderer(_cfg())
        block = {
            "type": "toggle",
            "toggle": {"rich_text": [_txt("Toggle header")]},
            "children": [_para("Toplevel child text")],
        }
        md = r.render_blocks([block])
        assert "Toplevel child text" in md


class TestToggleDepthIndentation:
    """Toggle nesting depth produces correct indentation."""

    def test_depth_zero_no_indent(self):
        r = NotionToMarkdownRenderer(_cfg())
        block = _toggle("Top level")
        md = r.render_blocks([block])
        # At depth 0, "- text" with no leading spaces
        assert md.startswith("- Top level") or "- Top level" in md.splitlines()[0]

    def test_nested_toggle_indented(self):
        """A toggle whose child is another toggle renders inner toggle indented."""
        r = NotionToMarkdownRenderer(_cfg())
        inner = _toggle("Inner toggle")
        outer = _toggle("Outer toggle", children=[inner])
        md = r.render_blocks([outer])
        assert "Outer toggle" in md
        assert "Inner toggle" in md
        lines = md.splitlines()
        outer_line = next(ln for ln in lines if "Outer toggle" in ln)
        inner_line = next(ln for ln in lines if "Inner toggle" in ln)
        # Inner should be indented more than outer
        outer_indent = len(outer_line) - len(outer_line.lstrip())
        inner_indent = len(inner_line) - len(inner_line.lstrip())
        assert inner_indent > outer_indent
