"""Tests for MarkdownToNotionConverter end-to-end.

PRD test IDs: U-CV-001 through U-CV-040.
"""

import pytest

from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter


def make_config(**kwargs):
    return NotionifyConfig(token="test-token", **kwargs)


def _get_rich_text_content(block):
    """Extract the concatenated text content from a block's rich_text."""
    block_type = block["type"]
    rich_text = block[block_type].get("rich_text", [])
    return "".join(
        seg.get("text", {}).get("content", "")
        if seg.get("type") == "text"
        else seg.get("equation", {}).get("expression", "")
        for seg in rich_text
    )


def _get_rich_text(block):
    """Extract rich_text list from a block."""
    block_type = block["type"]
    return block[block_type].get("rich_text", [])


# =========================================================================
# U-CV-001: H1/H2/H3 mapping
# =========================================================================

class TestHeadingMapping:
    """U-CV-001: Markdown headings map to Notion heading blocks."""

    def test_h1_to_heading_1(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("# Hello")
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "heading_1"
        assert _get_rich_text_content(result.blocks[0]) == "Hello"

    def test_h2_to_heading_2(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("## World")
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "heading_2"
        assert _get_rich_text_content(result.blocks[0]) == "World"

    def test_h3_to_heading_3(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("### Subtitle")
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "heading_3"
        assert _get_rich_text_content(result.blocks[0]) == "Subtitle"


# =========================================================================
# U-CV-002: H4 with heading_overflow="downgrade" -> heading_3
# =========================================================================

class TestHeadingOverflowDowngrade:
    """U-CV-002: H4+ headings with downgrade strategy clamp to heading_3."""

    def test_h4_downgrade_to_heading_3(self):
        c = MarkdownToNotionConverter(make_config(heading_overflow="downgrade"))
        result = c.convert("#### Deep Heading")
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "heading_3"
        assert _get_rich_text_content(result.blocks[0]) == "Deep Heading"

    def test_h5_downgrade_to_heading_3(self):
        c = MarkdownToNotionConverter(make_config(heading_overflow="downgrade"))
        result = c.convert("##### Very Deep")
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "heading_3"

    def test_h6_downgrade_to_heading_3(self):
        c = MarkdownToNotionConverter(make_config(heading_overflow="downgrade"))
        result = c.convert("###### Deepest")
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "heading_3"


# =========================================================================
# U-CV-003: H4 with heading_overflow="paragraph" -> bold paragraph
# =========================================================================

class TestHeadingOverflowParagraph:
    """U-CV-003: H4+ with paragraph strategy become bold paragraphs."""

    def test_h4_paragraph_becomes_bold_paragraph(self):
        c = MarkdownToNotionConverter(make_config(heading_overflow="paragraph"))
        result = c.convert("#### Sub-heading")
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b["type"] == "paragraph"
        rt = _get_rich_text(b)
        assert len(rt) >= 1
        # The text segment should have bold=True
        assert rt[0].get("annotations", {}).get("bold") is True

    def test_h5_paragraph_becomes_bold_paragraph(self):
        c = MarkdownToNotionConverter(make_config(heading_overflow="paragraph"))
        result = c.convert("##### Another")
        assert result.blocks[0]["type"] == "paragraph"
        rt = _get_rich_text(result.blocks[0])
        assert rt[0].get("annotations", {}).get("bold") is True


# =========================================================================
# U-CV-004: Plain paragraph
# =========================================================================

class TestPlainParagraph:
    """U-CV-004: Plain text becomes a paragraph block."""

    def test_simple_paragraph(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("Hello world")
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "paragraph"
        assert _get_rich_text_content(result.blocks[0]) == "Hello world"

    def test_multi_paragraph(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("First paragraph\n\nSecond paragraph")
        assert len(result.blocks) == 2
        assert all(b["type"] == "paragraph" for b in result.blocks)
        assert _get_rich_text_content(result.blocks[0]) == "First paragraph"
        assert _get_rich_text_content(result.blocks[1]) == "Second paragraph"


# =========================================================================
# U-CV-005: Paragraph > 2000 chars -> split segments
# =========================================================================

class TestParagraphOverflow:
    """U-CV-005: Long paragraphs are split into multiple rich_text segments."""

    def test_long_paragraph_splits_rich_text(self):
        c = MarkdownToNotionConverter(make_config())
        long_text = "a" * 3000
        result = c.convert(long_text)
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b["type"] == "paragraph"
        rt = _get_rich_text(b)
        # Should have been split: 2000 + 1000
        assert len(rt) >= 2
        total = "".join(seg["text"]["content"] for seg in rt)
        assert total == long_text
        # Each segment must be <= 2000 chars
        for seg in rt:
            assert len(seg["text"]["content"]) <= 2000


# =========================================================================
# U-CV-006: Bold + italic combined
# =========================================================================

class TestBoldItalic:
    """U-CV-006: Bold and italic annotations are set correctly."""

    def test_bold_text(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("**bold text**")
        rt = _get_rich_text(result.blocks[0])
        assert any(
            seg.get("annotations", {}).get("bold") is True
            for seg in rt
        )

    def test_italic_text(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("*italic text*")
        rt = _get_rich_text(result.blocks[0])
        assert any(
            seg.get("annotations", {}).get("italic") is True
            for seg in rt
        )

    def test_bold_and_italic(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("***bold and italic***")
        rt = _get_rich_text(result.blocks[0])
        found = False
        for seg in rt:
            ann = seg.get("annotations", {})
            if ann.get("bold") and ann.get("italic"):
                found = True
                break
        assert found, "Expected a segment with both bold and italic annotations"


# =========================================================================
# U-CV-007: Nested bold inside italic
# =========================================================================

class TestNestedAnnotations:
    """U-CV-007: Nested inline formatting is handled."""

    def test_bold_inside_italic(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("*italic and **bold** text*")
        rt = _get_rich_text(result.blocks[0])
        # We should find a segment that has bold=True and italic=True
        has_bold_italic = any(
            seg.get("annotations", {}).get("bold") and seg.get("annotations", {}).get("italic")
            for seg in rt
        )
        assert has_bold_italic


# =========================================================================
# U-CV-008: Inline code
# =========================================================================

class TestInlineCode:
    """U-CV-008: Inline code spans get code annotation."""

    def test_inline_code(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("Use `code` here")
        rt = _get_rich_text(result.blocks[0])
        code_segs = [seg for seg in rt if seg.get("annotations", {}).get("code")]
        assert len(code_segs) >= 1
        assert code_segs[0]["text"]["content"] == "code"


# =========================================================================
# U-CV-009: Strikethrough
# =========================================================================

class TestStrikethrough:
    """U-CV-009: Strikethrough text gets strikethrough annotation."""

    def test_strikethrough(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("~~deleted~~")
        rt = _get_rich_text(result.blocks[0])
        strike_segs = [seg for seg in rt if seg.get("annotations", {}).get("strikethrough")]
        assert len(strike_segs) >= 1
        assert strike_segs[0]["text"]["content"] == "deleted"


# =========================================================================
# U-CV-010: Link
# =========================================================================

class TestLink:
    """U-CV-010: Links produce rich_text segments with href."""

    def test_link(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("[click here](https://example.com)")
        rt = _get_rich_text(result.blocks[0])
        link_segs = [seg for seg in rt if seg.get("href")]
        assert len(link_segs) >= 1
        assert link_segs[0]["href"] == "https://example.com"
        assert link_segs[0]["text"]["content"] == "click here"


# =========================================================================
# U-CV-011: Bullet list
# =========================================================================

class TestBulletList:
    """U-CV-011: Bullet lists produce bulleted_list_item blocks."""

    def test_bullet_list(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("- item 1\n- item 2\n- item 3")
        assert len(result.blocks) == 3
        for b in result.blocks:
            assert b["type"] == "bulleted_list_item"
        assert _get_rich_text_content(result.blocks[0]) == "item 1"
        assert _get_rich_text_content(result.blocks[1]) == "item 2"
        assert _get_rich_text_content(result.blocks[2]) == "item 3"


# =========================================================================
# U-CV-012: Ordered list
# =========================================================================

class TestOrderedList:
    """U-CV-012: Ordered lists produce numbered_list_item blocks."""

    def test_ordered_list(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("1. first\n2. second\n3. third")
        assert len(result.blocks) == 3
        for b in result.blocks:
            assert b["type"] == "numbered_list_item"
        assert _get_rich_text_content(result.blocks[0]) == "first"
        assert _get_rich_text_content(result.blocks[1]) == "second"
        assert _get_rich_text_content(result.blocks[2]) == "third"


# =========================================================================
# U-CV-013: Nested list 3 levels
# =========================================================================

class TestNestedList:
    """U-CV-013: Nested lists produce children on list items."""

    def test_nested_list_3_levels(self):
        c = MarkdownToNotionConverter(make_config())
        md = "- level 1\n  - level 2\n    - level 3"
        result = c.convert(md)
        # Top level should have 1 block
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b["type"] == "bulleted_list_item"
        assert _get_rich_text_content(b) == "level 1"
        # Level 2
        children = b["bulleted_list_item"].get("children", [])
        assert len(children) >= 1
        assert children[0]["type"] == "bulleted_list_item"
        # Level 3
        l2_children = children[0]["bulleted_list_item"].get("children", [])
        assert len(l2_children) >= 1
        assert l2_children[0]["type"] == "bulleted_list_item"


# =========================================================================
# U-CV-014: Task list checked
# =========================================================================

class TestTaskListChecked:
    """U-CV-014: Checked task list items produce to_do with checked=True."""

    def test_task_list_checked(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("- [x] done task")
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b["type"] == "to_do"
        assert b["to_do"]["checked"] is True
        assert _get_rich_text_content(b) == "done task"


# =========================================================================
# U-CV-015: Task list unchecked
# =========================================================================

class TestTaskListUnchecked:
    """U-CV-015: Unchecked task list items produce to_do with checked=False."""

    def test_task_list_unchecked(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("- [ ] pending task")
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b["type"] == "to_do"
        assert b["to_do"]["checked"] is False
        assert _get_rich_text_content(b) == "pending task"


# =========================================================================
# U-CV-016: Block quote
# =========================================================================

class TestBlockQuote:
    """U-CV-016: Block quotes become quote blocks."""

    def test_block_quote(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("> quoted text")
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b["type"] == "quote"
        assert _get_rich_text_content(b) == "quoted text"


# =========================================================================
# U-CV-017: Nested block quote
# =========================================================================

class TestNestedBlockQuote:
    """U-CV-017: Nested blockquotes produce quote with children."""

    def test_nested_block_quote(self):
        c = MarkdownToNotionConverter(make_config())
        md = "> outer\n>> inner"
        result = c.convert(md)
        # Should produce at least one quote block
        assert len(result.blocks) >= 1
        assert result.blocks[0]["type"] == "quote"


# =========================================================================
# U-CV-018: Code block with language
# =========================================================================

class TestCodeBlockWithLanguage:
    """U-CV-018: Fenced code blocks with language info."""

    def test_code_block_python(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```python\nprint('hello')\n```")
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b["type"] == "code"
        assert b["code"]["language"] == "python"
        code_text = "".join(seg["text"]["content"] for seg in b["code"]["rich_text"])
        assert "print('hello')" in code_text

    def test_code_block_javascript(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```js\nconsole.log('hi')\n```")
        b = result.blocks[0]
        assert b["code"]["language"] == "javascript"

    def test_code_block_language_alias(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```py\nx = 1\n```")
        assert result.blocks[0]["code"]["language"] == "python"


# =========================================================================
# U-CV-019: Code block without language
# =========================================================================

class TestCodeBlockWithoutLanguage:
    """U-CV-019: Fenced code blocks without language default to plain text."""

    def test_code_block_no_language(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```\nsome code\n```")
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b["type"] == "code"
        assert b["code"]["language"] == "plain text"


# =========================================================================
# U-CV-020: Code > 2000 chars
# =========================================================================

class TestCodeBlockOverflow:
    """U-CV-020: Code blocks > 2000 chars split rich_text segments."""

    def test_long_code_block_splits(self):
        c = MarkdownToNotionConverter(make_config())
        long_code = "x" * 3500
        result = c.convert(f"```python\n{long_code}\n```")
        assert len(result.blocks) == 1
        b = result.blocks[0]
        rt = b["code"]["rich_text"]
        assert len(rt) >= 2
        total = "".join(seg["text"]["content"] for seg in rt)
        assert total == long_code
        for seg in rt:
            assert len(seg["text"]["content"]) <= 2000


# =========================================================================
# U-CV-021: Thematic break
# =========================================================================

class TestThematicBreak:
    """U-CV-021: Horizontal rule becomes a divider block."""

    def test_thematic_break(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("---")
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "divider"

    def test_thematic_break_asterisks(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("***")
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "divider"


# =========================================================================
# U-CV-022: Table 3x3
# =========================================================================

class TestTable:
    """U-CV-022: GFM table produces Notion table block."""

    def test_table_3x3(self):
        c = MarkdownToNotionConverter(make_config())
        md = (
            "| A | B | C |\n"
            "|---|---|---|\n"
            "| 1 | 2 | 3 |\n"
            "| 4 | 5 | 6 |"
        )
        result = c.convert(md)
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b["type"] == "table"
        table_data = b["table"]
        assert table_data["table_width"] == 3
        assert table_data["has_column_header"] is True
        # Should have 3 rows: header + 2 body rows
        rows = table_data["children"]
        assert len(rows) == 3
        for row in rows:
            assert row["type"] == "table_row"
            assert len(row["table_row"]["cells"]) == 3

    def test_table_disabled_fallback_comment(self):
        c = MarkdownToNotionConverter(make_config(enable_tables=False, table_fallback="comment"))
        md = "| A |\n|---|\n| 1 |"
        result = c.convert(md)
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b["type"] == "paragraph"
        content = _get_rich_text_content(b)
        assert "table omitted" in content


# =========================================================================
# U-CV-023: Table with inline formatting in cells
# =========================================================================

class TestTableInlineFormatting:
    """U-CV-023: Table cells can contain inline formatting."""

    def test_table_bold_and_italic_in_cells(self):
        c = MarkdownToNotionConverter(make_config())
        md = (
            "| **Bold** | *Italic* | `Code` |\n"
            "|----------|----------|--------|\n"
            "| normal   | ~~del~~  | text   |"
        )
        result = c.convert(md)
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b["type"] == "table"
        rows = b["table"]["children"]
        assert len(rows) == 2
        # Header row cells should have rich_text segments
        header_cells = rows[0]["table_row"]["cells"]
        assert len(header_cells) == 3
        # Each cell is a list of rich_text segments
        for cell in header_cells:
            assert isinstance(cell, list)
            assert len(cell) >= 1

    def test_table_link_in_cell(self):
        c = MarkdownToNotionConverter(make_config())
        md = (
            "| Name |\n"
            "|------|\n"
            "| [Link](https://example.com) |"
        )
        result = c.convert(md)
        b = result.blocks[0]
        assert b["type"] == "table"
        body_cell = b["table"]["children"][1]["table_row"]["cells"][0]
        # At least one segment should have a href or link
        assert len(body_cell) >= 1


# =========================================================================
# U-CV-024: Table with enable_tables=False
# =========================================================================

class TestTableDisabled:
    """U-CV-024: Table with enable_tables=False follows configured fallback."""

    def test_disabled_paragraph_fallback(self):
        c = MarkdownToNotionConverter(make_config(enable_tables=False, table_fallback="paragraph"))
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = c.convert(md)
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b["type"] == "paragraph"
        content = _get_rich_text_content(b)
        assert "A" in content
        assert "B" in content

    def test_disabled_raise_fallback(self):
        from notionify.errors import NotionifyConversionError
        c = MarkdownToNotionConverter(make_config(enable_tables=False, table_fallback="raise"))
        md = "| X |\n|---|\n| Y |"
        with pytest.raises(NotionifyConversionError):
            c.convert(md)

    def test_disabled_comment_fallback(self):
        c = MarkdownToNotionConverter(make_config(enable_tables=False, table_fallback="comment"))
        md = "| X |\n|---|\n| Y |"
        result = c.convert(md)
        b = result.blocks[0]
        assert b["type"] == "paragraph"
        assert "table omitted" in _get_rich_text_content(b)


# =========================================================================
# U-CV-025: Math strategy "equation"
# =========================================================================

class TestMathEquationStrategy:
    """U-CV-025: Block math with equation strategy produces equation block."""

    def test_block_math_equation(self):
        c = MarkdownToNotionConverter(make_config(math_strategy="equation"))
        result = c.convert("$$\nE=mc^2\n$$")
        assert len(result.blocks) >= 1
        eq_blocks = [b for b in result.blocks if b["type"] == "equation"]
        assert len(eq_blocks) >= 1
        assert eq_blocks[0]["equation"]["expression"] == "E=mc^2"

    def test_inline_math_equation(self):
        c = MarkdownToNotionConverter(make_config(math_strategy="equation"))
        result = c.convert("The formula $E=mc^2$ is famous.")
        assert len(result.blocks) >= 1
        rt = _get_rich_text(result.blocks[0])
        eq_segs = [seg for seg in rt if seg.get("type") == "equation"]
        assert len(eq_segs) >= 1
        assert eq_segs[0]["equation"]["expression"] == "E=mc^2"


# =========================================================================
# U-CV-026: Math strategy "code"
# =========================================================================

class TestMathCodeStrategy:
    """U-CV-026: Math with code strategy produces code block."""

    def test_block_math_code(self):
        c = MarkdownToNotionConverter(make_config(math_strategy="code"))
        result = c.convert("$$\nE=mc^2\n$$")
        code_blocks = [b for b in result.blocks if b["type"] == "code"]
        assert len(code_blocks) >= 1
        assert code_blocks[0]["code"]["language"] == "latex"

    def test_inline_math_code(self):
        c = MarkdownToNotionConverter(make_config(math_strategy="code"))
        result = c.convert("The formula $E=mc^2$ is famous.")
        rt = _get_rich_text(result.blocks[0])
        code_segs = [seg for seg in rt if seg.get("annotations", {}).get("code")]
        assert len(code_segs) >= 1
        assert code_segs[0]["text"]["content"] == "E=mc^2"


# =========================================================================
# U-CV-027: Math strategy "latex_text"
# =========================================================================

class TestMathLatexTextStrategy:
    """U-CV-027: Math with latex_text strategy keeps as plain text."""

    def test_block_math_latex_text(self):
        c = MarkdownToNotionConverter(make_config(math_strategy="latex_text"))
        result = c.convert("$$\nE=mc^2\n$$")
        para_blocks = [b for b in result.blocks if b["type"] == "paragraph"]
        assert len(para_blocks) >= 1
        content = _get_rich_text_content(para_blocks[0])
        assert "$$" in content
        assert "E=mc^2" in content

    def test_inline_math_latex_text(self):
        c = MarkdownToNotionConverter(make_config(math_strategy="latex_text"))
        result = c.convert("The formula $E=mc^2$ is famous.")
        rt = _get_rich_text(result.blocks[0])
        all_content = "".join(seg.get("text", {}).get("content", "") for seg in rt)
        assert "$E=mc^2$" in all_content


# =========================================================================
# U-CV-028: Block math > 1000 chars overflow
# =========================================================================

class TestBlockMathOverflowEndToEnd:
    """U-CV-028: Block math > 1000 chars falls back per math_overflow_block."""

    def test_overflow_code_fallback(self):
        c = MarkdownToNotionConverter(make_config(
            math_strategy="equation", math_overflow_block="code"
        ))
        long_expr = "x" * 1100
        result = c.convert(f"$$\n{long_expr}\n$$")
        code_blocks = [b for b in result.blocks if b["type"] == "code"]
        assert len(code_blocks) >= 1
        assert code_blocks[0]["code"]["language"] == "latex"

    def test_overflow_split_fallback(self):
        c = MarkdownToNotionConverter(make_config(
            math_strategy="equation", math_overflow_block="split"
        ))
        long_expr = "y" * 1100
        result = c.convert(f"$$\n{long_expr}\n$$")
        eq_blocks = [b for b in result.blocks if b["type"] == "equation"]
        assert len(eq_blocks) >= 2

    def test_overflow_text_fallback(self):
        c = MarkdownToNotionConverter(make_config(
            math_strategy="equation", math_overflow_block="text"
        ))
        long_expr = "z" * 1100
        result = c.convert(f"$$\n{long_expr}\n$$")
        para_blocks = [b for b in result.blocks if b["type"] == "paragraph"]
        assert len(para_blocks) >= 1
        content = _get_rich_text_content(para_blocks[0])
        assert "$$" in content


# =========================================================================
# U-CV-029: Inline math equation strategy
# =========================================================================

class TestInlineMathEquation:
    """U-CV-029: Inline math with equation strategy produces equation segment."""

    def test_inline_math_equation(self):
        c = MarkdownToNotionConverter(make_config(math_strategy="equation"))
        result = c.convert("Solve $x^2 + 1 = 0$ for x.")
        rt = _get_rich_text(result.blocks[0])
        eq_segs = [seg for seg in rt if seg.get("type") == "equation"]
        assert len(eq_segs) == 1
        assert eq_segs[0]["equation"]["expression"] == "x^2 + 1 = 0"


# =========================================================================
# U-CV-030: Inline math code strategy
# =========================================================================

class TestInlineMathCode:
    """U-CV-030: Inline math with code strategy produces code annotation."""

    def test_inline_math_code(self):
        c = MarkdownToNotionConverter(make_config(math_strategy="code"))
        result = c.convert("Solve $x^2 + 1 = 0$ for x.")
        rt = _get_rich_text(result.blocks[0])
        code_segs = [seg for seg in rt if seg.get("annotations", {}).get("code")]
        assert len(code_segs) >= 1
        assert code_segs[0]["text"]["content"] == "x^2 + 1 = 0"


# =========================================================================
# U-CV-031: Inline math > 1000 chars overflow
# =========================================================================

class TestInlineMathOverflowEndToEnd:
    """U-CV-031: Inline math > 1000 chars falls back per math_overflow_inline."""

    def test_overflow_code_fallback(self):
        c = MarkdownToNotionConverter(make_config(
            math_strategy="equation", math_overflow_inline="code"
        ))
        long_expr = "a" * 1100
        result = c.convert(f"The expression ${long_expr}$ is long.")
        rt = _get_rich_text(result.blocks[0])
        code_segs = [seg for seg in rt if seg.get("annotations", {}).get("code")]
        assert len(code_segs) >= 1

    def test_overflow_text_fallback(self):
        c = MarkdownToNotionConverter(make_config(
            math_strategy="equation", math_overflow_inline="text"
        ))
        long_expr = "b" * 1100
        result = c.convert(f"The expression ${long_expr}$ is long.")
        rt = _get_rich_text(result.blocks[0])
        all_text = "".join(seg.get("text", {}).get("content", "") for seg in rt)
        assert "$" in all_text


# =========================================================================
# U-CV-032: Image external
# =========================================================================

class TestImageExternal:
    """U-CV-032: External image URL produces an image block."""

    def test_external_image(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("![alt text](https://example.com/image.png)")
        img_blocks = [b for b in result.blocks if b["type"] == "image"]
        assert len(img_blocks) == 1
        img = img_blocks[0]["image"]
        assert img["type"] == "external"
        assert img["external"]["url"] == "https://example.com/image.png"

    def test_external_image_with_caption(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("![my caption](https://example.com/pic.jpg)")
        img = result.blocks[0]["image"]
        assert img["type"] == "external"
        caption_text = "".join(
            seg["text"]["content"] for seg in img.get("caption", [])
        )
        assert caption_text == "my caption"


# =========================================================================
# U-CV-033: Image local file with upload enabled
# =========================================================================

class TestImageLocalUpload:
    """U-CV-033: Local image with upload enabled creates PendingImage."""

    def test_local_image_pending(self):
        c = MarkdownToNotionConverter(make_config(image_upload=True))
        result = c.convert("![photo](./photo.png)")
        assert len(result.images) == 1
        assert result.images[0].src == "./photo.png"
        assert result.images[0].source_type.value == "local_file"


# =========================================================================
# U-CV-034: Image fallback skip
# =========================================================================

class TestImageFallbackSkip:
    """U-CV-034: Image with unsupported source and fallback=skip."""

    def test_image_skip_fallback(self):
        c = MarkdownToNotionConverter(make_config(image_upload=False, image_fallback="skip"))
        result = c.convert("![photo](./photo.png)")
        img_blocks = [b for b in result.blocks if b["type"] == "image"]
        assert len(img_blocks) == 0
        assert any(w.code == "IMAGE_SKIPPED" for w in result.warnings)


# =========================================================================
# U-CV-035: Image fallback placeholder
# =========================================================================

class TestImageFallbackPlaceholder:
    """U-CV-035: Image with fallback=placeholder inserts text block."""

    def test_image_placeholder_fallback(self):
        c = MarkdownToNotionConverter(make_config(image_upload=False, image_fallback="placeholder"))
        result = c.convert("![photo](./photo.png)")
        assert len(result.blocks) >= 1
        assert result.blocks[0]["type"] == "paragraph"
        content = _get_rich_text_content(result.blocks[0])
        assert "[image:" in content


# =========================================================================
# U-CV-036: Empty markdown
# =========================================================================

class TestEmptyMarkdown:
    """U-CV-036: Empty markdown produces no blocks."""

    def test_empty_string(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("")
        assert len(result.blocks) == 0
        assert len(result.images) == 0
        assert len(result.warnings) == 0


# =========================================================================
# U-CV-037: Whitespace-only markdown
# =========================================================================

class TestWhitespaceMarkdown:
    """U-CV-037: Whitespace-only markdown produces no blocks."""

    def test_whitespace_only(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("   \n\n   \n")
        assert len(result.blocks) == 0

    def test_newlines_only(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("\n\n\n")
        assert len(result.blocks) == 0


# =========================================================================
# U-CV-038: Mixed content document
# =========================================================================

class TestMixedContent:
    """U-CV-038: A document with mixed block types converts correctly."""

    def test_mixed_document(self):
        c = MarkdownToNotionConverter(make_config())
        md = (
            "# Title\n\n"
            "Some paragraph.\n\n"
            "- item 1\n"
            "- item 2\n\n"
            "---\n\n"
            "```python\nprint('hi')\n```\n"
        )
        result = c.convert(md)
        types = [b["type"] for b in result.blocks]
        assert "heading_1" in types
        assert "paragraph" in types
        assert "bulleted_list_item" in types
        assert "divider" in types
        assert "code" in types


# =========================================================================
# U-CV-039: Heading with inline formatting
# =========================================================================

class TestHeadingWithInlineFormatting:
    """U-CV-039: Headings can contain inline formatting."""

    def test_heading_with_bold(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("# Hello **World**")
        assert result.blocks[0]["type"] == "heading_1"
        rt = _get_rich_text(result.blocks[0])
        bold_segs = [seg for seg in rt if seg.get("annotations", {}).get("bold")]
        assert len(bold_segs) >= 1


# =========================================================================
# U-CV-040: ConversionResult structure
# =========================================================================

class TestConversionResult:
    """U-CV-040: ConversionResult has correct structure."""

    def test_result_has_blocks_images_warnings(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("# Hello")
        assert hasattr(result, "blocks")
        assert hasattr(result, "images")
        assert hasattr(result, "warnings")
        assert isinstance(result.blocks, list)
        assert isinstance(result.images, list)
        assert isinstance(result.warnings, list)

    def test_blocks_are_dicts(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("# Hello\n\nWorld")
        for b in result.blocks:
            assert isinstance(b, dict)
            assert "type" in b
            assert "object" in b
            assert b["object"] == "block"

    def test_html_block_produces_warning(self):
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("<div>html content</div>")
        # HTML blocks should be skipped with a warning
        assert any(w.code == "HTML_BLOCK_SKIPPED" for w in result.warnings)


class TestDebugDumpAst:
    def test_debug_dump_ast_prints_to_stderr(self, capsys):
        from notionify.config import NotionifyConfig
        from notionify.converter.md_to_notion import MarkdownToNotionConverter
        config = NotionifyConfig(token="test", debug_dump_ast=True)
        conv = MarkdownToNotionConverter(config)
        conv.convert("# Hello")
        captured = capsys.readouterr()
        assert "[notionify] Normalized AST:" in captured.err

class TestDebugDumpPayload:
    def test_debug_dump_payload_prints_to_stderr(self, capsys):
        from notionify.config import NotionifyConfig
        from notionify.converter.md_to_notion import MarkdownToNotionConverter
        config = NotionifyConfig(token="test", debug_dump_payload=True)
        conv = MarkdownToNotionConverter(config)
        conv.convert("Hello world")
        captured = capsys.readouterr()
        assert "[notionify] Notion blocks payload:" in captured.err

class TestASTNormalizerEdgeCases:
    def test_footnotes_token_skipped(self):
        """Footnotes block token is silently dropped."""
        from unittest.mock import patch

        from notionify.converter.ast_normalizer import ASTNormalizer
        normalizer = ASTNormalizer()
        # Inject a footnotes token
        with patch.object(normalizer, '_parser', return_value=[{"type": "footnotes"}]):
            tokens = normalizer.parse("anything")
        assert tokens == []

    def test_footnote_ref_becomes_text(self):
        """footnote_ref token becomes a text token with [^key] content."""
        from unittest.mock import patch

        from notionify.converter.ast_normalizer import ASTNormalizer
        normalizer = ASTNormalizer()
        with patch.object(normalizer, '_parser', return_value=[
            {"type": "footnote_ref", "raw": "1"}
        ]):
            tokens = normalizer.parse("anything")
        assert len(tokens) == 1
        assert tokens[0]["type"] == "text"
        assert tokens[0]["raw"] == "[^1]"

    def test_footnote_ref_uses_attrs_index_fallback(self):
        """footnote_ref without raw uses attrs.index."""
        from unittest.mock import patch

        from notionify.converter.ast_normalizer import ASTNormalizer
        normalizer = ASTNormalizer()
        with patch.object(normalizer, '_parser', return_value=[
            {"type": "footnote_ref", "attrs": {"index": "note1"}}
        ]):
            tokens = normalizer.parse("anything")
        assert tokens[0]["raw"] == "[^note1]"

    def test_raw_type_becomes_text(self):
        """'raw' type token becomes text token."""
        from unittest.mock import patch

        from notionify.converter.ast_normalizer import ASTNormalizer
        normalizer = ASTNormalizer()
        with patch.object(normalizer, '_parser', return_value=[
            {"type": "raw", "raw": "some raw content"}
        ]):
            tokens = normalizer.parse("anything")
        assert len(tokens) == 1
        assert tokens[0]["type"] == "text"
        assert tokens[0]["raw"] == "some raw content"

    def test_unknown_token_type_skipped(self):
        """Unknown token types are silently dropped."""
        from unittest.mock import patch

        from notionify.converter.ast_normalizer import ASTNormalizer
        normalizer = ASTNormalizer()
        with patch.object(normalizer, '_parser', return_value=[
            {"type": "completely_unknown_type", "raw": "???"}
        ]):
            tokens = normalizer.parse("anything")
        # Unknown tokens are passed through (not dropped) so block_builder
        # can emit an UNKNOWN_TOKEN warning.
        assert len(tokens) == 1
        assert tokens[0]["type"] == "completely_unknown_type"

    def test_unknown_token_produces_warning_in_converter(self):
        """Unknown AST tokens produce UNKNOWN_TOKEN warnings via block_builder."""
        from unittest.mock import patch

        c = MarkdownToNotionConverter(make_config())
        with patch.object(
            c._normalizer, '_parser',
            return_value=[{"type": "alien_type", "raw": "???"}],
        ):
            result = c.convert("anything")
        assert any(w.code == "UNKNOWN_TOKEN" for w in result.warnings)

    def test_parser_returning_string_gives_empty(self):
        """If the parser returns a string instead of list, parse() returns []."""
        from unittest.mock import patch

        from notionify.converter.ast_normalizer import ASTNormalizer
        normalizer = ASTNormalizer()
        with patch.object(normalizer, '_parser', return_value="not a list"):
            tokens = normalizer.parse("anything")
        assert tokens == []

    def test_html_inline_token_normalized(self):
        """inline_html mistune token becomes html_inline canonical type (lines 182-183)."""
        from unittest.mock import patch

        from notionify.converter.ast_normalizer import ASTNormalizer
        normalizer = ASTNormalizer()
        with patch.object(normalizer, '_parser', return_value=[
            {"type": "inline_html", "raw": "<br>"}
        ]):
            tokens = normalizer.parse("anything")
        assert len(tokens) == 1
        assert tokens[0]["type"] == "html_inline"
        assert tokens[0]["raw"] == "<br>"


# =========================================================================
# Coverage gap tests: block_builder.py lines 97-102, 112, 114, 121,
# 233-238, 311, 345, 431-435, 468-479, 494, 542, 600, 641-647, 657-660
# =========================================================================

class TestNormalizeLanguageCoverageGaps:
    """Cover _normalize_language alias lookup and stripped-digits fallback
    (lines 97-102).
    """

    def test_alias_py_maps_to_python(self):
        """'py' is an alias that must map to 'python' (line 97-98)."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```py\nprint('hi')\n```")
        assert result.blocks[0]["code"]["language"] == "python"

    def test_alias_js_maps_to_javascript(self):
        """'js' alias must map to 'javascript'."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```js\nconsole.log(1)\n```")
        assert result.blocks[0]["code"]["language"] == "javascript"

    def test_alias_ts_maps_to_typescript(self):
        """'ts' alias must map to 'typescript'."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```ts\nlet x: number = 1\n```")
        assert result.blocks[0]["code"]["language"] == "typescript"

    def test_alias_sh_maps_to_shell(self):
        """'sh' alias must map to 'shell'."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```sh\necho hi\n```")
        assert result.blocks[0]["code"]["language"] == "shell"

    def test_alias_rb_maps_to_ruby(self):
        """'rb' alias must map to 'ruby'."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```rb\nputs 'hi'\n```")
        assert result.blocks[0]["code"]["language"] == "ruby"

    def test_stripped_digits_python3_maps_to_python(self):
        """'python3' strips trailing digit to 'python' (line 99-102)."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```python3\nprint(1)\n```")
        assert result.blocks[0]["code"]["language"] == "python"

    def test_stripped_digits_ruby2_maps_to_ruby(self):
        """'ruby2' strips trailing digit to 'ruby'."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```ruby2\nputs 1\n```")
        assert result.blocks[0]["code"]["language"] == "ruby"

    def test_stripped_digits_alias_py3_maps_to_python(self):
        """'py3' strips digit -> 'py' -> alias lookup -> 'python'."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```py3\nprint(1)\n```")
        assert result.blocks[0]["code"]["language"] == "python"

    def test_completely_unknown_lang_becomes_plain_text(self):
        """A language not in any list falls back to 'plain text'."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("```totallyfakelang\ncode\n```")
        assert result.blocks[0]["code"]["language"] == "plain text"

    def test_alias_first_word_maps_correctly(self):
        """'js extra' -> first='js' -> alias -> 'javascript' (line 104-105)."""
        from notionify.converter.block_builder import _normalize_language
        # Full string is not in NOTION_LANGUAGES or LANGUAGE_ALIASES,
        # but the first word 'js' is an alias. This covers line 105.
        assert _normalize_language("js extra") == "javascript"


class TestClassifyImageSourceCoverageGaps:
    """Cover _classify_image_source file:// scheme and UNKNOWN fallback
    (lines 112, 114, 121).
    """

    def test_file_uri_absolute_path_is_local_file(self):
        """file:/// URI must be classified as LOCAL_FILE (line 114)."""
        c = MarkdownToNotionConverter(make_config(image_upload=True))
        result = c.convert("![pic](file:///home/user/image.png)")
        # With upload enabled, a LOCAL_FILE produces a placeholder block
        # and a PendingImage entry.
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "image"
        assert len(result.images) == 1
        assert result.images[0].src == "file:///home/user/image.png"

    def test_file_uri_localhost_is_local_file(self):
        """file://localhost/ URI must be classified as LOCAL_FILE (line 114)."""
        c = MarkdownToNotionConverter(make_config(image_upload=True))
        result = c.convert("![pic](file://localhost/tmp/img.png)")
        assert len(result.images) == 1
        assert result.images[0].src == "file://localhost/tmp/img.png"

    def test_ftp_url_is_unknown_source(self):
        """ftp:// URL hits the UNKNOWN fallback branch (line 121)."""
        c = MarkdownToNotionConverter(make_config(image_fallback="skip"))
        result = c.convert("![pic](ftp://files.example.com/img.png)")
        # UNKNOWN with fallback=skip -> no block, one warning
        assert result.blocks == []
        assert any(w.code == "IMAGE_SKIPPED" for w in result.warnings)

    def test_sftp_url_is_unknown_source(self):
        """sftp:// URL hits the UNKNOWN fallback branch (line 121)."""
        c = MarkdownToNotionConverter(make_config(image_fallback="skip"))
        result = c.convert("![pic](sftp://server.example.com/img.png)")
        assert result.blocks == []
        assert any(w.code == "IMAGE_SKIPPED" for w in result.warnings)

    def test_empty_url_is_unknown_source(self):
        """An empty URL is classified UNKNOWN (line 112)."""
        # Build a paragraph with a bare image token using an empty URL via
        # direct block_builder call so we bypass markdown parsing.
        from notionify.converter.block_builder import build_blocks
        tokens = [{
            "type": "paragraph",
            "children": [{
                "type": "image",
                "attrs": {"url": ""},
                "children": [],
            }],
        }]
        blocks, images, warnings = build_blocks(tokens, make_config(image_fallback="skip"))
        assert blocks == []
        assert any(w.code == "IMAGE_SKIPPED" for w in warnings)

    def test_malformed_ipv6_url_is_unknown_source(self):
        """Malformed IPv6 URL 'http://[' triggers ValueError and returns UNKNOWN (line 127-129)."""
        from notionify.converter.block_builder import _classify_image_source
        from notionify.models import ImageSourceType
        # urlparse raises ValueError for invalid IPv6 addresses.
        result = _classify_image_source("http://[")
        assert result == ImageSourceType.UNKNOWN


class TestHeadingOverflowParagraphContent:
    """Cover the heading_overflow='paragraph' path (lines 233-238)."""

    def test_h4_with_paragraph_overflow_produces_bold_paragraph(self):
        """H4 with heading_overflow='paragraph' renders as bold paragraph."""
        c = MarkdownToNotionConverter(make_config(heading_overflow="paragraph"))
        result = c.convert("#### Heading Four")
        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block["type"] == "paragraph"
        rich_text = block["paragraph"]["rich_text"]
        assert len(rich_text) >= 1
        assert rich_text[0]["annotations"]["bold"] is True
        assert rich_text[0]["text"]["content"] == "Heading Four"

    def test_h5_with_paragraph_overflow_produces_bold_paragraph(self):
        """H5 with heading_overflow='paragraph' renders as bold paragraph."""
        c = MarkdownToNotionConverter(make_config(heading_overflow="paragraph"))
        result = c.convert("##### Heading Five")
        block = result.blocks[0]
        assert block["type"] == "paragraph"
        assert block["paragraph"]["rich_text"][0]["annotations"]["bold"] is True

    def test_h6_with_paragraph_overflow_produces_bold_paragraph(self):
        """H6 with heading_overflow='paragraph' renders as bold paragraph."""
        c = MarkdownToNotionConverter(make_config(heading_overflow="paragraph"))
        result = c.convert("###### Heading Six")
        block = result.blocks[0]
        assert block["type"] == "paragraph"
        assert block["paragraph"]["rich_text"][0]["annotations"]["bold"] is True


class TestBlockQuoteWithNonParagraphChild:
    """Cover the block_quote non-paragraph nested block path (line 311)."""

    def test_blockquote_with_nested_code_block(self):
        """A blockquote containing a fenced code block uses nested children."""
        c = MarkdownToNotionConverter(make_config())
        md = "> intro text\n>\n> ```python\n> x = 1\n> ```"
        result = c.convert(md)
        # The quote block is the first block produced
        quote_block = result.blocks[0]
        assert quote_block["type"] == "quote"
        # The code block should appear as a child of the quote
        children = quote_block["quote"].get("children", [])
        assert len(children) >= 1
        assert children[0]["type"] == "code"
        assert children[0]["code"]["language"] == "python"

    def test_blockquote_with_nested_heading_produces_children(self):
        """A blockquote may contain non-paragraph tokens as nested children."""
        from notionify.converter.block_builder import build_blocks
        tokens = [{
            "type": "block_quote",
            "children": [
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "raw": "intro"}],
                },
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "children": [{"type": "text", "raw": "section"}],
                },
            ],
        }]
        blocks, images, warnings = build_blocks(tokens, make_config())
        assert blocks[0]["type"] == "quote"
        children = blocks[0]["quote"].get("children", [])
        assert len(children) == 1
        assert children[0]["type"] == "heading_2"


class TestTaskListItemInList:
    """Cover task_list_item dispatch inside _build_list (line 345)."""

    def test_task_list_checked_item(self):
        """A checked task list item produces a to_do block with checked=True."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("- [x] Done")
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "to_do"
        assert result.blocks[0]["to_do"]["checked"] is True

    def test_task_list_unchecked_item(self):
        """An unchecked task list item produces a to_do block with checked=False."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("- [ ] Not done")
        assert result.blocks[0]["type"] == "to_do"
        assert result.blocks[0]["to_do"]["checked"] is False

    def test_mixed_task_and_regular_items(self):
        """A list mixing task and regular items produces correct block types."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("- [x] Task A\n- [ ] Task B\n- Regular item")
        types = [b["type"] for b in result.blocks]
        assert types[0] == "to_do"
        assert types[1] == "to_do"
        assert types[2] == "bulleted_list_item"


class TestListItemOtherNestedBlock:
    """Cover the 'other nested block' path inside _build_list_item (lines 431-435)."""

    def test_list_item_with_nested_code_block(self):
        """A list item containing a code block has the code as a child."""
        c = MarkdownToNotionConverter(make_config())
        md = "- item text\n\n  ```python\n  x = 1\n  ```"
        result = c.convert(md)
        assert result.blocks[0]["type"] == "bulleted_list_item"
        children = result.blocks[0]["bulleted_list_item"].get("children", [])
        assert len(children) >= 1
        assert children[0]["type"] == "code"

    def test_ordered_list_item_with_nested_code_block(self):
        """An ordered list item containing a code block has the code as a child."""
        c = MarkdownToNotionConverter(make_config())
        md = "1. first item\n\n   ```python\n   y = 2\n   ```"
        result = c.convert(md)
        assert result.blocks[0]["type"] == "numbered_list_item"
        children = result.blocks[0]["numbered_list_item"].get("children", [])
        assert len(children) >= 1
        assert children[0]["type"] == "code"

    def test_list_item_other_nested_block_via_direct_call(self):
        """Direct token: list_item with a non-list, non-paragraph child."""
        from notionify.converter.block_builder import build_blocks
        tokens = [{
            "type": "list",
            "attrs": {"ordered": False},
            "children": [{
                "type": "list_item",
                "children": [
                    {
                        "type": "paragraph",
                        "children": [{"type": "text", "raw": "item"}],
                    },
                    {
                        "type": "thematic_break",
                    },
                ],
            }],
        }]
        blocks, images, warnings = build_blocks(tokens, make_config())
        assert blocks[0]["type"] == "bulleted_list_item"
        children = blocks[0]["bulleted_list_item"].get("children", [])
        assert any(c["type"] == "divider" for c in children)


class TestTaskListItemNestedBlockPaths:
    """Cover nested list and other-block paths in _build_task_list_item
    (lines 468-479, 494).
    """

    def test_task_item_with_nested_list_produces_children(self):
        """A task list item followed by an indented sub-list has children."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("- [x] Parent task\n  - sub item")
        assert result.blocks[0]["type"] == "to_do"
        children = result.blocks[0]["to_do"].get("children", [])
        assert len(children) >= 1
        assert children[0]["type"] == "bulleted_list_item"

    def test_task_item_with_nested_unordered_list(self):
        """Nested unordered list items appear as bulleted_list_item children."""
        c = MarkdownToNotionConverter(make_config())
        result = c.convert("- [ ] Todo\n  - first\n  - second")
        children = result.blocks[0]["to_do"].get("children", [])
        assert len(children) == 2
        assert all(c["type"] == "bulleted_list_item" for c in children)

    def test_task_item_with_nested_code_block(self):
        """A task list item with a nested code block has the code as a child."""
        c = MarkdownToNotionConverter(make_config())
        md = "- [x] Task\n\n  ```python\n  code\n  ```"
        result = c.convert(md)
        assert result.blocks[0]["type"] == "to_do"
        children = result.blocks[0]["to_do"].get("children", [])
        assert len(children) >= 1
        assert children[0]["type"] == "code"

    def test_task_item_nested_blocks_via_direct_token(self):
        """Direct token: task_list_item with both list child and other child."""
        from notionify.converter.block_builder import build_blocks
        tokens = [{
            "type": "list",
            "attrs": {"ordered": False},
            "children": [{
                "type": "task_list_item",
                "attrs": {"checked": True},
                "children": [
                    {
                        "type": "paragraph",
                        "children": [{"type": "text", "raw": "main text"}],
                    },
                    {
                        "type": "list",
                        "attrs": {"ordered": False},
                        "children": [{
                            "type": "list_item",
                            "children": [
                                {"type": "block_text",
                                 "children": [{"type": "text", "raw": "sub"}]},
                            ],
                        }],
                    },
                    {
                        "type": "thematic_break",
                    },
                ],
            }],
        }]
        blocks, images, warnings = build_blocks(tokens, make_config())
        assert blocks[0]["type"] == "to_do"
        assert blocks[0]["to_do"]["checked"] is True
        children = blocks[0]["to_do"].get("children", [])
        child_types = [ch["type"] for ch in children]
        assert "bulleted_list_item" in child_types
        assert "divider" in child_types


class TestDataUriImageWithUpload:
    """Cover the DATA_URI + image_upload=True path (line 542)."""

    def test_data_uri_image_with_upload_creates_placeholder(self):
        """A data: URI image with upload enabled creates a placeholder block."""
        c = MarkdownToNotionConverter(make_config(image_upload=True))
        data_uri = "data:image/png;base64,iVBORw0KGgo="
        result = c.convert(f"![logo]({data_uri})")
        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block["type"] == "image"
        assert block["image"]["external"]["url"] == "https://placeholder.notionify.invalid"
        assert len(result.images) == 1
        assert result.images[0].src == data_uri

    def test_data_uri_image_caption_included(self):
        """A data: URI image with alt text has caption in the placeholder block."""
        c = MarkdownToNotionConverter(make_config(image_upload=True))
        data_uri = "data:image/jpeg;base64,/9j/4AAQ=="
        result = c.convert(f"![my caption]({data_uri})")
        block = result.blocks[0]
        caption = block["image"].get("caption", [])
        assert len(caption) == 1
        assert caption[0]["text"]["content"] == "my caption"

    def test_data_uri_image_without_upload_hits_fallback(self):
        """A data: URI image with upload disabled triggers fallback."""
        c = MarkdownToNotionConverter(make_config(image_upload=False, image_fallback="skip"))
        data_uri = "data:image/png;base64,iVBORw0KGgo="
        result = c.convert(f"![logo]({data_uri})")
        assert result.blocks == []
        assert any(w.code == "IMAGE_SKIPPED" for w in result.warnings)


class TestImageFallbackRaise:
    """Cover the image_fallback='raise' path (line 600)."""

    def test_local_file_fallback_raise_produces_warning(self):
        """A local file image with fallback='raise' emits IMAGE_ERROR warning."""
        c = MarkdownToNotionConverter(make_config(image_upload=False, image_fallback="raise"))
        result = c.convert("![alt](/tmp/image.png)")
        assert result.blocks == []
        assert len(result.warnings) == 1
        w = result.warnings[0]
        assert w.code == "IMAGE_ERROR"
        assert w.context.get("fallback") == "raise"

    def test_unknown_scheme_fallback_raise_produces_warning(self):
        """An ftp:// image with fallback='raise' emits IMAGE_ERROR warning."""
        c = MarkdownToNotionConverter(make_config(image_fallback="raise"))
        result = c.convert("![alt](ftp://example.com/img.png)")
        assert result.blocks == []
        assert any(w.code == "IMAGE_ERROR" for w in result.warnings)

    def test_data_uri_without_upload_fallback_raise(self):
        """A data: URI with upload disabled and fallback='raise' emits IMAGE_ERROR."""
        c = MarkdownToNotionConverter(make_config(image_upload=False, image_fallback="raise"))
        result = c.convert("![x](data:image/png;base64,abc=)")
        assert result.blocks == []
        assert any(w.code == "IMAGE_ERROR" for w in result.warnings)


class TestLocalFileImageFallbackPaths:
    """Cover LOCAL_FILE with upload=False and UNKNOWN source fallback paths
    (lines 641-647, 657-660).
    """

    def test_local_file_upload_disabled_fallback_skip(self):
        """Local file image with upload=False and fallback='skip' produces warning."""
        c = MarkdownToNotionConverter(make_config(image_upload=False, image_fallback="skip"))
        result = c.convert("![alt](/path/to/image.png)")
        assert result.blocks == []
        assert any(w.code == "IMAGE_SKIPPED" for w in result.warnings)

    def test_local_file_upload_disabled_fallback_placeholder(self):
        """Local file image with upload=False and fallback='placeholder'
        produces a paragraph block with placeholder text (lines 657-660).
        """
        c = MarkdownToNotionConverter(make_config(image_upload=False, image_fallback="placeholder"))
        result = c.convert("![my alt text](/images/photo.jpg)")
        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block["type"] == "paragraph"
        content = block["paragraph"]["rich_text"][0]["text"]["content"]
        assert content == "[image: my alt text]"
        assert any(w.code == "IMAGE_PLACEHOLDER" for w in result.warnings)

    def test_local_file_without_alt_placeholder_uses_url(self):
        """Local file image with no alt text uses URL in placeholder text."""
        c = MarkdownToNotionConverter(make_config(image_upload=False, image_fallback="placeholder"))
        result = c.convert("![](/images/photo.jpg)")
        block = result.blocks[0]
        content = block["paragraph"]["rich_text"][0]["text"]["content"]
        # alt_text is empty, so display falls back to url
        assert "/images/photo.jpg" in content

    def test_unknown_source_upload_disabled_fallback_placeholder(self):
        """UNKNOWN source type (ftp://) with fallback='placeholder' gives
        a paragraph block (lines 641-647 + 657-660).
        """
        c = MarkdownToNotionConverter(make_config(image_fallback="placeholder"))
        result = c.convert("![fig](ftp://server/img.png)")
        assert len(result.blocks) == 1
        assert result.blocks[0]["type"] == "paragraph"
        assert any(w.code == "IMAGE_PLACEHOLDER" for w in result.warnings)

    def test_file_uri_upload_disabled_fallback_placeholder(self):
        """file:// URI with upload=False and fallback='placeholder' gives
        a placeholder paragraph.
        """
        c = MarkdownToNotionConverter(make_config(image_upload=False, image_fallback="placeholder"))
        result = c.convert("![diagram](file:///home/user/diagram.svg)")
        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block["type"] == "paragraph"
        assert any(w.code == "IMAGE_PLACEHOLDER" for w in result.warnings)


class TestRemainingCoverageGaps:
    """Cover remaining uncovered lines:
    233-238 (UNKNOWN_TOKEN), 311 (empty paragraph), 345 (blockquote newline sep),
    542 (table returns None), 657-660 (_extract_text children/raw branches).
    """

    # --- Line 233-238: UNKNOWN_TOKEN warning ---

    def test_unknown_token_type_produces_warning(self):
        """A token with an unrecognized type emits UNKNOWN_TOKEN warning
        and produces no block (lines 233-238).
        """
        from notionify.converter.block_builder import build_blocks
        tokens = [{"type": "some_completely_unknown_type", "raw": "data"}]
        blocks, images, warnings = build_blocks(tokens, make_config())
        assert blocks == []
        assert len(warnings) == 1
        assert warnings[0].code == "UNKNOWN_TOKEN"
        assert "some_completely_unknown_type" in warnings[0].message

    def test_token_with_empty_type_produces_no_warning(self):
        """A token with empty type string is silently skipped (line 238 else branch)."""
        from notionify.converter.block_builder import build_blocks
        tokens = [{"type": "", "raw": "data"}]
        blocks, images, warnings = build_blocks(tokens, make_config())
        assert blocks == []
        assert warnings == []

    def test_multiple_unknown_tokens_each_produce_warning(self):
        """Each unknown token type emits its own UNKNOWN_TOKEN warning."""
        from notionify.converter.block_builder import build_blocks
        tokens = [
            {"type": "foo_block", "raw": "a"},
            {"type": "bar_block", "raw": "b"},
        ]
        blocks, images, warnings = build_blocks(tokens, make_config())
        assert blocks == []
        assert len(warnings) == 2
        codes = [w.code for w in warnings]
        assert all(c == "UNKNOWN_TOKEN" for c in codes)

    # --- Line 311: empty paragraph returns [] ---

    def test_empty_paragraph_token_produces_no_block(self):
        """A paragraph token with no children produces no block (line 311)."""
        from notionify.converter.block_builder import build_blocks
        tokens = [{"type": "paragraph", "children": []}]
        blocks, images, warnings = build_blocks(tokens, make_config())
        assert blocks == []

    def test_paragraph_with_unrecognized_inline_produces_no_block(self):
        """A paragraph whose children produce no rich_text emits no block (line 311).

        An unrecognized inline token type returns [] from build_rich_text,
        making rich_text falsy, which triggers the early return on line 311.
        """
        from notionify.converter.block_builder import build_blocks
        tokens = [{"type": "paragraph", "children": [{"type": "_unrecognized_inline_"}]}]
        blocks, images, warnings = build_blocks(tokens, make_config())
        assert blocks == []

    # --- Line 345: blockquote with multiple paragraphs inserts newline ---

    def test_blockquote_two_paragraphs_joined_with_newline(self):
        """A blockquote with two paragraph children joins them with a newline
        separator in the rich_text (line 345).
        """
        from notionify.converter.block_builder import build_blocks
        tokens = [{
            "type": "block_quote",
            "children": [
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "raw": "First line"}],
                },
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "raw": "Second line"}],
                },
            ],
        }]
        blocks, images, warnings = build_blocks(tokens, make_config())
        assert blocks[0]["type"] == "quote"
        rich_text = blocks[0]["quote"]["rich_text"]
        # There should be a newline segment between the two paragraphs
        contents = [seg["text"]["content"] for seg in rich_text if seg["type"] == "text"]
        assert "First line" in contents
        assert "\n" in contents
        assert "Second line" in contents

    def test_blockquote_three_paragraphs_has_two_newline_separators(self):
        """Three paragraph children in a blockquote produce two newline separators."""
        from notionify.converter.block_builder import build_blocks
        tokens = [{
            "type": "block_quote",
            "children": [
                {"type": "paragraph", "children": [{"type": "text", "raw": "A"}]},
                {"type": "paragraph", "children": [{"type": "text", "raw": "B"}]},
                {"type": "paragraph", "children": [{"type": "text", "raw": "C"}]},
            ],
        }]
        blocks, images, warnings = build_blocks(tokens, make_config())
        rich_text = blocks[0]["quote"]["rich_text"]
        newline_count = sum(
            1 for seg in rich_text
            if seg["type"] == "text" and seg["text"]["content"] == "\n"
        )
        assert newline_count == 2

    # --- Line 542: _build_table returns [] when build_table returns None ---

    def test_build_table_returning_none_produces_no_block(self):
        """When the underlying build_table returns (None, warnings), no block
        is added (line 542).
        """
        from unittest.mock import patch

        from notionify.converter.block_builder import build_blocks
        from notionify.models import ConversionWarning

        sentinel_warning = ConversionWarning(
            code="TABLE_DISABLED", message="patched out", context={}
        )
        tokens = [{"type": "table", "children": []}]
        with patch(
            "notionify.converter.block_builder.build_table",
            return_value=(None, [sentinel_warning]),
        ):
            blocks, images, warnings = build_blocks(tokens, make_config())

        assert blocks == []
        assert any(w.code == "TABLE_DISABLED" for w in warnings)

    # --- Lines 657-660: _extract_text children and raw fallback branches ---

    def test_extract_text_from_nested_inline_token_with_children(self):
        """_extract_text recurses into tokens that have a 'children' key
        (line 657-658), e.g. a strong/em inline node.
        """
        from notionify.converter.rich_text import extract_text
        children = [
            {
                "type": "strong",
                "children": [{"type": "text", "raw": "bold text"}],
            }
        ]
        result = extract_text(children)
        assert result == "bold text"

    def test_extract_text_from_token_with_raw_no_children(self):
        """_extract_text uses the 'raw' field for tokens that have no
        'children' key and are not type='text' (lines 659-660), e.g. code_inline.
        """
        from notionify.converter.rich_text import extract_text
        children = [
            {"type": "code_inline", "raw": "inline_code()"},
        ]
        result = extract_text(children)
        assert result == "inline_code()"

    def test_extract_text_mixed_token_types(self):
        """_extract_text handles a mix of text, children, and raw tokens."""
        from notionify.converter.rich_text import extract_text
        children = [
            {"type": "text", "raw": "Hello "},
            {"type": "strong", "children": [{"type": "text", "raw": "world"}]},
            {"type": "code_inline", "raw": "!"},
        ]
        result = extract_text(children)
        assert result == "Hello world!"

    def test_image_alt_text_uses_extract_text_children_branch(self):
        """An image with a nested inline token in alt uses _extract_text
        children branch (line 657-658) to extract the caption.
        """
        from notionify.converter.block_builder import build_blocks
        tokens = [{
            "type": "paragraph",
            "children": [{
                "type": "image",
                "attrs": {"url": "https://example.com/img.png"},
                "children": [
                    {
                        "type": "strong",
                        "children": [{"type": "text", "raw": "bold alt"}],
                    }
                ],
            }],
        }]
        blocks, images, warnings = build_blocks(tokens, make_config())
        assert blocks[0]["type"] == "image"
        caption = blocks[0]["image"].get("caption", [])
        assert len(caption) == 1
        assert caption[0]["text"]["content"] == "bold alt"

    def test_image_alt_text_uses_extract_text_raw_branch(self):
        """An image with a code_inline alt token uses _extract_text raw
        branch (lines 659-660) to extract the caption.
        """
        from notionify.converter.block_builder import build_blocks
        tokens = [{
            "type": "paragraph",
            "children": [{
                "type": "image",
                "attrs": {"url": "https://example.com/img.png"},
                "children": [
                    {"type": "code_inline", "raw": "myCode"},
                ],
            }],
        }]
        blocks, images, warnings = build_blocks(tokens, make_config())
        assert blocks[0]["type"] == "image"
        caption = blocks[0]["image"].get("caption", [])
        assert len(caption) == 1
        assert caption[0]["text"]["content"] == "myCode"
