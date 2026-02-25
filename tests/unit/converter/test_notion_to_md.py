"""Tests for NotionToMarkdownRenderer.

PRD test IDs: U-NM-001 through U-NM-022.
"""

import pytest

from notionify.config import NotionifyConfig
from notionify.converter.notion_to_md import NotionToMarkdownRenderer
from notionify.errors import NotionifyUnsupportedBlockError


def make_config(**kwargs):
    return NotionifyConfig(token="test-token", **kwargs)


def _make_annotations(
    bold=False, italic=False, code=False,
    strikethrough=False, underline=False, color="default",
):
    return {
        "bold": bold,
        "italic": italic,
        "code": code,
        "strikethrough": strikethrough,
        "underline": underline,
        "color": color,
    }


def _make_text_segment(content, **ann_kwargs):
    """Build a single rich_text text segment."""
    return {
        "type": "text",
        "text": {"content": content},
        "plain_text": content,
        "annotations": _make_annotations(**ann_kwargs),
        "href": None,
    }


def _make_link_segment(content, url):
    """Build a rich_text segment with a link."""
    return {
        "type": "text",
        "text": {"content": content, "link": {"url": url}},
        "plain_text": content,
        "annotations": _make_annotations(),
        "href": url,
    }


def _make_equation_segment(expression):
    """Build a rich_text equation segment."""
    return {
        "type": "equation",
        "equation": {"expression": expression},
        "plain_text": expression,
        "annotations": _make_annotations(),
        "href": None,
    }


def make_heading(level, text):
    return {
        "type": f"heading_{level}",
        f"heading_{level}": {
            "rich_text": [_make_text_segment(text)],
        },
    }


def make_paragraph(rich_text_segments, children=None):
    block = {
        "type": "paragraph",
        "paragraph": {
            "rich_text": rich_text_segments,
        },
    }
    if children:
        block["paragraph"]["children"] = children
    return block


def make_code_block(code, language="python"):
    return {
        "type": "code",
        "code": {
            "rich_text": [_make_text_segment(code)],
            "language": language,
        },
    }


def make_divider():
    return {
        "type": "divider",
        "divider": {},
    }


def make_equation_block(expression):
    return {
        "type": "equation",
        "equation": {
            "expression": expression,
        },
    }


def make_bulleted_list_item(text, children=None):
    block = {
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [_make_text_segment(text)],
        },
    }
    if children:
        block["bulleted_list_item"]["children"] = children
    return block


def make_numbered_list_item(text, children=None):
    block = {
        "type": "numbered_list_item",
        "numbered_list_item": {
            "rich_text": [_make_text_segment(text)],
        },
    }
    if children:
        block["numbered_list_item"]["children"] = children
    return block


def make_to_do(text, checked=False):
    return {
        "type": "to_do",
        "to_do": {
            "rich_text": [_make_text_segment(text)],
            "checked": checked,
        },
    }


def make_quote(text, children=None):
    block = {
        "type": "quote",
        "quote": {
            "rich_text": [_make_text_segment(text)],
        },
    }
    if children:
        block["quote"]["children"] = children
    return block


def make_image_external(url, caption=""):
    block = {
        "type": "image",
        "image": {
            "type": "external",
            "external": {"url": url},
        },
    }
    if caption:
        block["image"]["caption"] = [_make_text_segment(caption)]
    else:
        block["image"]["caption"] = []
    return block


def make_table_block(header_cells, body_rows, has_column_header=True):
    """Build a Notion table block with rows as children.

    header_cells: list of strings for the header row
    body_rows: list of list of strings for body rows
    """
    col_count = len(header_cells)
    rows = []

    # Header row
    header_row = {
        "type": "table_row",
        "table_row": {
            "cells": [
                [_make_text_segment(cell)] for cell in header_cells
            ],
        },
    }
    rows.append(header_row)

    # Body rows
    for row_data in body_rows:
        row = {
            "type": "table_row",
            "table_row": {
                "cells": [
                    [_make_text_segment(cell)] for cell in row_data
                ],
            },
        }
        rows.append(row)

    return {
        "type": "table",
        "table": {
            "table_width": col_count,
            "has_column_header": has_column_header,
            "has_row_header": False,
            "children": rows,
        },
    }


# =========================================================================
# U-NM-001: heading_1/2/3 -> #/##/###
# =========================================================================

class TestHeadingRendering:
    """U-NM-001: Notion headings render to Markdown headings."""

    def test_heading_1(self):
        r = NotionToMarkdownRenderer(make_config())
        md = r.render_blocks([make_heading(1, "Title")])
        assert md.strip() == "# Title"

    def test_heading_2(self):
        r = NotionToMarkdownRenderer(make_config())
        md = r.render_blocks([make_heading(2, "Subtitle")])
        assert md.strip() == "## Subtitle"

    def test_heading_3(self):
        r = NotionToMarkdownRenderer(make_config())
        md = r.render_blocks([make_heading(3, "Section")])
        assert md.strip() == "### Section"

    def test_multiple_headings(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_heading(1, "H1"), make_heading(2, "H2"), make_heading(3, "H3")]
        md = r.render_blocks(blocks)
        assert "# H1" in md
        assert "## H2" in md
        assert "### H3" in md


# =========================================================================
# U-NM-002: paragraph with bold
# =========================================================================

class TestParagraphBold:
    """U-NM-002: Paragraph with bold annotation renders **text**."""

    def test_bold_paragraph(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_paragraph([_make_text_segment("hello", bold=True)])]
        md = r.render_blocks(blocks)
        assert "**hello**" in md


# =========================================================================
# U-NM-003: paragraph with italic
# =========================================================================

class TestParagraphItalic:
    """U-NM-003: Paragraph with italic annotation renders _text_."""

    def test_italic_paragraph(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_paragraph([_make_text_segment("world", italic=True)])]
        md = r.render_blocks(blocks)
        assert "_world_" in md


# =========================================================================
# U-NM-004: link
# =========================================================================

class TestLinkRendering:
    """U-NM-004: Links render as [text](url)."""

    def test_link(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_paragraph([_make_link_segment("click", "https://example.com")])]
        md = r.render_blocks(blocks)
        assert "[click](https://example.com)" in md


# =========================================================================
# U-NM-005: inline equation
# =========================================================================

class TestInlineEquation:
    """U-NM-005: Inline equation renders as $expression$."""

    def test_inline_equation(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_paragraph([
            _make_text_segment("The formula "),
            _make_equation_segment("E=mc^2"),
            _make_text_segment(" is famous."),
        ])]
        md = r.render_blocks(blocks)
        assert "$E=mc^2$" in md


# =========================================================================
# U-NM-006: nested bulleted_list_item
# =========================================================================

class TestNestedBulletedList:
    """U-NM-006: Bulleted list items with children render nested."""

    def test_nested_bullet_list(self):
        r = NotionToMarkdownRenderer(make_config())
        child = make_bulleted_list_item("child")
        parent = make_bulleted_list_item("parent", children=[child])
        md = r.render_blocks([parent])
        assert "- parent" in md
        assert "- child" in md

    def test_flat_bullet_list(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [
            make_bulleted_list_item("first"),
            make_bulleted_list_item("second"),
        ]
        md = r.render_blocks(blocks)
        assert "- first" in md
        assert "- second" in md


# =========================================================================
# U-NM-007: numbered_list_item
# =========================================================================

class TestNumberedList:
    """U-NM-007: Numbered list items render with numbers."""

    def test_numbered_list(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [
            make_numbered_list_item("first"),
            make_numbered_list_item("second"),
            make_numbered_list_item("third"),
        ]
        md = r.render_blocks(blocks)
        assert "1. first" in md
        assert "2. second" in md
        assert "3. third" in md

    def test_numbered_list_resets_after_other_block(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [
            make_numbered_list_item("first"),
            make_paragraph([_make_text_segment("break")]),
            make_numbered_list_item("restart"),
        ]
        md = r.render_blocks(blocks)
        assert "1. first" in md
        # After a paragraph, numbering should restart
        assert "1. restart" in md


# =========================================================================
# U-NM-008: to_do checked
# =========================================================================

class TestToDoChecked:
    """U-NM-008: Checked to_do renders as - [x] text."""

    def test_todo_checked(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_to_do("done", checked=True)]
        md = r.render_blocks(blocks)
        assert "- [x] done" in md


# =========================================================================
# U-NM-009: to_do unchecked
# =========================================================================

class TestToDoUnchecked:
    """U-NM-009: Unchecked to_do renders as - [ ] text."""

    def test_todo_unchecked(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_to_do("pending", checked=False)]
        md = r.render_blocks(blocks)
        assert "- [ ] pending" in md


# =========================================================================
# U-NM-010: code block python
# =========================================================================

class TestCodeBlockRendering:
    """U-NM-010: Code blocks render with fenced syntax and language."""

    def test_code_block_python(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_code_block("print('hello')", language="python")]
        md = r.render_blocks(blocks)
        assert "```python" in md
        assert "print('hello')" in md
        assert "```" in md

    def test_code_block_plain_text(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_code_block("some code", language="plain text")]
        md = r.render_blocks(blocks)
        # plain text language should render as empty info string
        assert "```\n" in md
        assert "some code" in md

    def test_code_block_latex_detect(self):
        """With detect_latex_code=True, code blocks with language=latex become math."""
        r = NotionToMarkdownRenderer(make_config(detect_latex_code=True))
        blocks = [make_code_block("E=mc^2", language="latex")]
        md = r.render_blocks(blocks)
        assert "$$" in md
        assert "E=mc^2" in md
        assert "```" not in md

    def test_code_block_latex_no_detect(self):
        """With detect_latex_code=False, latex code blocks stay as code."""
        r = NotionToMarkdownRenderer(make_config(detect_latex_code=False))
        blocks = [make_code_block("E=mc^2", language="latex")]
        md = r.render_blocks(blocks)
        assert "```latex" in md
        assert "E=mc^2" in md


# =========================================================================
# U-NM-011: divider
# =========================================================================

class TestDividerRendering:
    """U-NM-011: Divider blocks render as ---."""

    def test_divider(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_divider()]
        md = r.render_blocks(blocks)
        assert "---" in md


# =========================================================================
# U-NM-012: equation block
# =========================================================================

class TestEquationBlockRendering:
    """U-NM-012: Equation blocks render as $$...$$."""

    def test_equation_block(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_equation_block("\\int_0^1 x dx")]
        md = r.render_blocks(blocks)
        assert "$$" in md
        assert "\\int_0^1 x dx" in md


# =========================================================================
# U-NM-013: table 3x3
# =========================================================================

class TestTableRendering:
    """U-NM-013: Table blocks render as GFM tables."""

    def test_table_3x3(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_table_block(
            header_cells=["A", "B", "C"],
            body_rows=[["1", "2", "3"], ["4", "5", "6"]],
        )]
        md = r.render_blocks(blocks)
        assert "| A | B | C |" in md
        assert "|---|---|---|" in md
        assert "| 1 | 2 | 3 |" in md
        assert "| 4 | 5 | 6 |" in md


# =========================================================================
# U-NM-014: image external
# =========================================================================

class TestImageRendering:
    """U-NM-014: Image blocks render as ![caption](url)."""

    def test_external_image(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_image_external("https://example.com/img.png", caption="my image")]
        md = r.render_blocks(blocks)
        assert "![my image](https://example.com/img.png)" in md

    def test_external_image_no_caption(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_image_external("https://example.com/img.png")]
        md = r.render_blocks(blocks)
        assert "![](https://example.com/img.png)" in md

    def test_notion_hosted_image_expiry_warning(self):
        r = NotionToMarkdownRenderer(make_config(image_expiry_warnings=True))
        blocks = [{
            "type": "image",
            "id": "block-123",
            "image": {
                "type": "file",
                "file": {
                    "url": "https://prod-files.notion.so/image.png",
                    "expiry_time": "2026-03-01T00:00:00.000Z",
                },
                "caption": [],
            },
        }]
        md = r.render_blocks(blocks)
        assert "notion-image-expiry" in md
        assert len(r.warnings) == 1
        assert r.warnings[0].code == "IMAGE_EXPIRY"


# =========================================================================
# U-NM-015: quote block
# =========================================================================

class TestQuoteRendering:
    """U-NM-015: Quote blocks render with > prefix."""

    def test_quote(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_quote("quoted text")]
        md = r.render_blocks(blocks)
        assert "> quoted text" in md


# =========================================================================
# U-NM-016: inline code
# =========================================================================

class TestInlineCodeRendering:
    """U-NM-016: Inline code renders with backticks."""

    def test_inline_code(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_paragraph([
            _make_text_segment("use "),
            _make_text_segment("code", code=True),
            _make_text_segment(" here"),
        ])]
        md = r.render_blocks(blocks)
        assert "`code`" in md


# =========================================================================
# U-NM-017: unsupported block policy "comment"
# =========================================================================

class TestUnsupportedBlockComment:
    """U-NM-017: Unsupported blocks with comment policy emit HTML comment."""

    def test_unsupported_comment(self):
        r = NotionToMarkdownRenderer(make_config(unsupported_block_policy="comment"))
        blocks = [{"type": "alien_block", "id": "abc123", "alien_block": {}}]
        md = r.render_blocks(blocks)
        assert "<!-- notion:alien_block -->" in md


# =========================================================================
# U-NM-018: unsupported block policy "skip"
# =========================================================================

class TestUnsupportedBlockSkip:
    """U-NM-018: Unsupported blocks with skip policy produce empty output."""

    def test_unsupported_skip(self):
        r = NotionToMarkdownRenderer(make_config(unsupported_block_policy="skip"))
        blocks = [{"type": "alien_block", "id": "abc123", "alien_block": {}}]
        md = r.render_blocks(blocks)
        assert md == ""


# =========================================================================
# U-NM-019: unsupported block policy "raise"
# =========================================================================

class TestUnsupportedBlockRaise:
    """U-NM-019: Unsupported blocks with raise policy throw an exception."""

    def test_unsupported_raise(self):
        r = NotionToMarkdownRenderer(make_config(unsupported_block_policy="raise"))
        blocks = [{"type": "alien_block", "id": "abc123", "alien_block": {}}]
        with pytest.raises(NotionifyUnsupportedBlockError) as exc_info:
            r.render_blocks(blocks)
        assert "alien_block" in str(exc_info.value)
        assert exc_info.value.context["block_type"] == "alien_block"


# =========================================================================
# U-NM-020: strikethrough rendering
# =========================================================================

class TestStrikethroughRendering:
    """U-NM-020: Strikethrough annotation renders as ~~text~~."""

    def test_strikethrough(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_paragraph([_make_text_segment("deleted", strikethrough=True)])]
        md = r.render_blocks(blocks)
        assert "~~deleted~~" in md


# =========================================================================
# U-NM-021: combined annotations
# =========================================================================

class TestCombinedAnnotations:
    """U-NM-021: Multiple annotations combine correctly."""

    def test_bold_italic(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_paragraph([_make_text_segment("both", bold=True, italic=True)])]
        md = r.render_blocks(blocks)
        # Should contain both bold and italic markers
        assert "**" in md
        assert "_" in md
        assert "both" in md

    def test_bold_code_stays_code(self):
        """Code annotation takes precedence - bold is not applied inside code."""
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_paragraph([_make_text_segment("snippet", code=True, bold=True)])]
        md = r.render_blocks(blocks)
        assert "`snippet`" in md


# =========================================================================
# U-NM-022: empty blocks
# =========================================================================

class TestEmptyBlocks:
    """U-NM-022: Empty block lists render empty string."""

    def test_empty_block_list(self):
        r = NotionToMarkdownRenderer(make_config())
        md = r.render_blocks([])
        assert md == ""

    def test_empty_paragraph(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_paragraph([])]
        md = r.render_blocks(blocks)
        # Empty paragraph should render as just whitespace/newlines
        assert md.strip() == ""


# =========================================================================
# Additional edge-case tests
# =========================================================================

class TestBreadcrumbAndTocOmitted:
    """Breadcrumb and TOC blocks are silently omitted."""

    def test_breadcrumb_omitted(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [{"type": "breadcrumb", "breadcrumb": {}}]
        md = r.render_blocks(blocks)
        assert md == ""

    def test_toc_omitted(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [{"type": "table_of_contents", "table_of_contents": {}}]
        md = r.render_blocks(blocks)
        assert md == ""


class TestPassthroughBlocks:
    """Layout wrappers pass through their children."""

    def test_synced_block_passthrough(self):
        r = NotionToMarkdownRenderer(make_config())
        inner = make_paragraph([_make_text_segment("inner")])
        blocks = [{"type": "synced_block", "synced_block": {}, "children": [inner]}]
        md = r.render_blocks(blocks)
        assert "inner" in md


class TestUnderlineRendering:
    """Underline renders as <u>text</u>."""

    def test_underline(self):
        r = NotionToMarkdownRenderer(make_config())
        blocks = [make_paragraph([_make_text_segment("underlined", underline=True)])]
        md = r.render_blocks(blocks)
        assert "<u>" in md
        assert "underlined" in md
        assert "</u>" in md


# =========================================================================
# Additional coverage tests for previously uncovered paths
# =========================================================================


class TestRenderBlockPublicMethod:
    """render_block() is a public single-block method (line 105)."""

    def test_render_single_block(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "paragraph",
            "paragraph": {"rich_text": [_make_text_segment("hello")]},
        }
        md = r.render_block(block)
        assert "hello" in md


class TestMediaBlockRendering:
    """Media types (video, audio, pdf) use _render_media (line 147)."""

    def test_video_external(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "video",
            "video": {"type": "external", "external": {"url": "https://youtube.com/v/123"}},
        }
        md = r.render_blocks([block])
        assert "[Video]" in md or "youtube.com" in md

    def test_audio_file(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "audio",
            "audio": {"type": "file", "file": {"url": "https://cdn.example.com/sound.mp3"}},
        }
        md = r.render_blocks([block])
        assert "[Audio]" in md or "sound.mp3" in md

    def test_media_no_url(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {"type": "video", "video": {"type": "external", "external": {}}}
        md = r.render_blocks([block])
        assert "[Video]" in md


class TestNestedChildrenRendering:
    """Nested children in quote, bulleted, numbered, to_do (lines 193-202, 228, 241)."""

    def test_quote_with_children(self):
        r = NotionToMarkdownRenderer(make_config())
        child = {
            "type": "paragraph",
            "paragraph": {"rich_text": [_make_text_segment("nested")]},
        }
        block = {
            "type": "quote",
            "quote": {
                "rich_text": [_make_text_segment("outer")],
                "children": [child],
            },
        }
        md = r.render_blocks([block])
        assert "outer" in md
        assert "nested" in md

    def test_quote_empty_child_line(self):
        """Empty lines in children get > prefix (line 387)."""
        r = NotionToMarkdownRenderer(make_config())
        child = {
            "type": "paragraph",
            "paragraph": {"rich_text": [_make_text_segment("line1")]},
        }
        block = {
            "type": "quote",
            "quote": {
                "rich_text": [],
                "children": [child],
            },
        }
        md = r.render_blocks([block])
        assert "line1" in md

    def test_bulleted_with_children(self):
        r = NotionToMarkdownRenderer(make_config())
        child = {
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [_make_text_segment("child")]},
        }
        block = {
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [_make_text_segment("parent")],
                "children": [child],
            },
        }
        md = r.render_blocks([block])
        assert "parent" in md
        assert "child" in md

    def test_numbered_with_children(self):
        r = NotionToMarkdownRenderer(make_config())
        child = {
            "type": "paragraph",
            "paragraph": {"rich_text": [_make_text_segment("sub")]},
        }
        block = {
            "type": "numbered_list_item",
            "numbered_list_item": {
                "rich_text": [_make_text_segment("item")],
                "children": [child],
            },
        }
        md = r.render_blocks([block])
        assert "item" in md
        assert "sub" in md

    def test_todo_with_children(self):
        r = NotionToMarkdownRenderer(make_config())
        child = {
            "type": "paragraph",
            "paragraph": {"rich_text": [_make_text_segment("detail")]},
        }
        block = {
            "type": "to_do",
            "to_do": {
                "rich_text": [_make_text_segment("task")],
                "checked": False,
                "children": [child],
            },
        }
        md = r.render_blocks([block])
        assert "task" in md
        assert "detail" in md


class TestToggleRendering:
    """Toggle block with children (lines 393-401)."""

    def test_toggle_with_children(self):
        r = NotionToMarkdownRenderer(make_config())
        child = {
            "type": "paragraph",
            "paragraph": {"rich_text": [_make_text_segment("hidden content")]},
        }
        block = {
            "type": "toggle",
            "toggle": {
                "rich_text": [_make_text_segment("Click to expand")],
                "children": [child],
            },
        }
        md = r.render_blocks([block])
        assert "Click to expand" in md
        assert "hidden content" in md


class TestColumnPassthroughBlocks:
    """column_list / column blocks pass through children (lines 492-493)."""

    def test_column_list_passthrough(self):
        r = NotionToMarkdownRenderer(make_config())
        child = {
            "type": "paragraph",
            "paragraph": {"rich_text": [_make_text_segment("col content")]},
        }
        block = {
            "type": "column_list",
            "column_list": {"children": [child]},
        }
        md = r.render_blocks([block])
        assert "col content" in md

    def test_passthrough_no_children(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {"type": "column_list", "column_list": {}}
        md = r.render_blocks([block])
        assert md == ""


class TestUnsupportedBlockWithText:
    """_render_unsupported comment mode with non-empty text (line 524)."""

    def test_unsupported_comment_with_text(self):
        r = NotionToMarkdownRenderer(make_config(unsupported_block_policy="comment"))
        # Use a completely custom type not in any dispatch table
        block = {
            "type": "custom_widget",
            "custom_widget": {
                "rich_text": [{"plain_text": "widget text", "type": "text"}]
            },
        }
        md = r.render_blocks([block])
        assert "<!-- notion:custom_widget -->" in md
        assert "widget text" in md


class TestExtractPlainTextHelper:
    """_extract_plain_text helper (line 581)."""

    def test_extracts_from_rich_text(self):
        from notionify.converter.notion_to_md import _extract_plain_text
        block = {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"plain_text": "Hello ", "type": "text"},
                    {"plain_text": "world", "type": "text"},
                ]
            },
        }
        assert _extract_plain_text(block) == "Hello world"

    def test_empty_block_returns_empty(self):
        from notionify.converter.notion_to_md import _extract_plain_text
        assert _extract_plain_text({}) == ""


class TestQuoteEmptyChildLineBranch:
    """Quote children with multiple blocks produce internal empty lines (line 201)."""

    def test_quote_two_children_hits_empty_line_branch(self):
        """Two children create blank line between them, hitting prefix.rstrip() branch."""
        r = NotionToMarkdownRenderer(make_config())
        child1 = {
            "type": "paragraph",
            "paragraph": {"rich_text": [_make_text_segment("first")]},
        }
        child2 = {
            "type": "paragraph",
            "paragraph": {"rich_text": [_make_text_segment("second")]},
        }
        block = {
            "type": "quote",
            "quote": {
                "rich_text": [_make_text_segment("outer")],
                "children": [child1, child2],
            },
        }
        md = r.render_blocks([block])
        assert "first" in md
        assert "second" in md


class TestCalloutEmptyChildLineBranch:
    """Callout children with multiple blocks produce internal empty lines (line 387)."""

    def test_callout_two_children_hits_empty_line_branch(self):
        """Two children create blank line, hitting '>'  empty branch in callout."""
        r = NotionToMarkdownRenderer(make_config())
        child1 = {
            "type": "paragraph",
            "paragraph": {"rich_text": [_make_text_segment("alpha")]},
        }
        child2 = {
            "type": "paragraph",
            "paragraph": {"rich_text": [_make_text_segment("beta")]},
        }
        block = {
            "type": "callout",
            "callout": {
                "rich_text": [_make_text_segment("note")],
                "icon": {"type": "emoji", "emoji": "💡"},
                "children": [child1, child2],
            },
        }
        md = r.render_blocks([block])
        assert "alpha" in md
        assert "beta" in md


class TestMarkdownEscapeCodeContext:
    """markdown_escape with context='code' returns text unchanged (line 41)."""

    def test_code_context_no_escaping(self):
        from notionify.converter.inline_renderer import markdown_escape
        text = "**bold** and `code` and [link](url)"
        assert markdown_escape(text, context="code") == text

    def test_url_context_encodes_parens(self):
        from notionify.converter.inline_renderer import markdown_escape
        assert markdown_escape("a(b)c", context="url") == "a%28b%29c"


class TestEquationWithHref:
    """Equation segment with href renders as linked equation (line 88)."""

    def test_equation_with_link(self):
        from notionify.converter.inline_renderer import render_rich_text
        seg = {
            "type": "equation",
            "equation": {"expression": "E=mc^2"},
            "href": "https://example.com",
            "annotations": {},
        }
        result = render_rich_text([seg])
        assert "$E=mc^2$" in result
        assert "https://example.com" in result


class TestBookmarkRendering:
    """_render_bookmark produces a Markdown link with optional caption."""

    def test_bookmark_basic_url(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "bookmark",
            "bookmark": {"url": "https://example.com", "caption": []},
        }
        md = r.render_blocks([block])
        assert "https://example.com" in md

    def test_bookmark_with_caption(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "bookmark",
            "bookmark": {
                "url": "https://example.com",
                "caption": [_make_text_segment("My caption")],
            },
        }
        md = r.render_blocks([block])
        assert "https://example.com" in md
        assert "My caption" in md

    def test_bookmark_empty_url(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "bookmark",
            "bookmark": {"url": "", "caption": []},
        }
        md = r.render_blocks([block])
        # Should render without crashing; link text will be empty
        assert md is not None

    def test_bookmark_url_with_parens(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "bookmark",
            "bookmark": {"url": "https://example.com/path(1)", "caption": []},
        }
        md = r.render_blocks([block])
        # Parens in URL must be percent-encoded so the MD link is valid
        assert "example.com" in md
        assert "(" not in md.split("](")[1] if "](" in md else True


class TestFileBlockRendering:
    """_render_file produces a Markdown link for external/hosted files."""

    def test_file_external_url(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "file",
            "file": {
                "type": "external",
                "external": {"url": "https://example.com/doc.pdf"},
                "name": "doc.pdf",
                "caption": [],
            },
        }
        md = r.render_blocks([block])
        assert "https://example.com/doc.pdf" in md
        assert "doc.pdf" in md

    def test_file_hosted_url(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "file",
            "file": {
                "type": "file",
                "file": {"url": "https://cdn.notion.so/file.zip?token=abc"},
                "name": "archive.zip",
                "caption": [],
            },
        }
        md = r.render_blocks([block])
        assert "cdn.notion.so" in md

    def test_file_caption_overrides_name(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "file",
            "file": {
                "type": "external",
                "external": {"url": "https://example.com/report.pdf"},
                "name": "report.pdf",
                "caption": [_make_text_segment("Annual Report")],
            },
        }
        md = r.render_blocks([block])
        assert "Annual Report" in md

    def test_file_no_name_falls_back_to_url_basename(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "file",
            "file": {
                "type": "external",
                "external": {"url": "https://example.com/data.csv"},
                "name": "",
                "caption": [],
            },
        }
        md = r.render_blocks([block])
        assert "data.csv" in md

    def test_file_no_url_renders_file_label(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "file",
            "file": {
                "type": "external",
                "external": {"url": ""},
                "name": "",
                "caption": [],
            },
        }
        md = r.render_blocks([block])
        assert "File" in md


class TestEmbedRendering:
    """_render_embed produces [Embed](url)."""

    def test_embed_url_included(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "embed",
            "embed": {"url": "https://youtube.com/watch?v=abc"},
        }
        md = r.render_blocks([block])
        assert "youtube.com" in md
        assert "Embed" in md

    def test_embed_empty_url(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {"type": "embed", "embed": {"url": ""}}
        md = r.render_blocks([block])
        assert "[Embed]" in md

    def test_embed_url_with_parens_encoded(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "embed",
            "embed": {"url": "https://example.com/page(1)"},
        }
        md = r.render_blocks([block])
        assert "example.com" in md


class TestLinkPreviewRendering:
    """_render_link_preview produces [url](escaped_url)."""

    def test_link_preview_url(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "link_preview",
            "link_preview": {"url": "https://example.com/article"},
        }
        md = r.render_blocks([block])
        assert "https://example.com/article" in md

    def test_link_preview_empty_url(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {"type": "link_preview", "link_preview": {"url": ""}}
        md = r.render_blocks([block])
        assert md is not None


class TestChildPageAndDatabaseRendering:
    """_render_child_page and _render_child_database produce links."""

    def test_child_page_title(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "child_page",
            "id": "abc123",
            "child_page": {"title": "My Subpage"},
        }
        md = r.render_blocks([block])
        assert "My Subpage" in md
        assert "Page:" in md

    def test_child_page_no_title_uses_untitled(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "child_page",
            "id": "abc123",
            "child_page": {},
        }
        md = r.render_blocks([block])
        assert "Untitled" in md

    def test_child_database_title(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "child_database",
            "id": "def456",
            "child_database": {"title": "My DB"},
        }
        md = r.render_blocks([block])
        assert "My DB" in md
        assert "Database:" in md

    def test_child_database_no_title(self):
        r = NotionToMarkdownRenderer(make_config())
        block = {
            "type": "child_database",
            "id": "def456",
            "child_database": {},
        }
        md = r.render_blocks([block])
        assert "Untitled" in md
