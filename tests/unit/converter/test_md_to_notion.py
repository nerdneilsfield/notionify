"""Tests for MarkdownToNotionConverter end-to-end.

PRD test IDs: U-CV-001 through U-CV-040.
"""

import pytest

from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.config import NotionifyConfig


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
        from notionify.converter.ast_normalizer import ASTNormalizer
        from unittest.mock import patch
        normalizer = ASTNormalizer()
        # Inject a footnotes token
        with patch.object(normalizer, '_parser', return_value=[{"type": "footnotes"}]):
            tokens = normalizer.parse("anything")
        assert tokens == []

    def test_footnote_ref_becomes_text(self):
        """footnote_ref token becomes a text token with [^key] content."""
        from notionify.converter.ast_normalizer import ASTNormalizer
        from unittest.mock import patch
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
        from notionify.converter.ast_normalizer import ASTNormalizer
        from unittest.mock import patch
        normalizer = ASTNormalizer()
        with patch.object(normalizer, '_parser', return_value=[
            {"type": "footnote_ref", "attrs": {"index": "note1"}}
        ]):
            tokens = normalizer.parse("anything")
        assert tokens[0]["raw"] == "[^note1]"

    def test_raw_type_becomes_text(self):
        """'raw' type token becomes text token."""
        from notionify.converter.ast_normalizer import ASTNormalizer
        from unittest.mock import patch
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
        from notionify.converter.ast_normalizer import ASTNormalizer
        from unittest.mock import patch
        normalizer = ASTNormalizer()
        with patch.object(normalizer, '_parser', return_value=[
            {"type": "completely_unknown_type", "raw": "???"}
        ]):
            tokens = normalizer.parse("anything")
        assert tokens == []

    def test_parser_returning_string_gives_empty(self):
        """If the parser returns a string instead of list, parse() returns []."""
        from notionify.converter.ast_normalizer import ASTNormalizer
        from unittest.mock import patch
        normalizer = ASTNormalizer()
        with patch.object(normalizer, '_parser', return_value="not a list"):
            tokens = normalizer.parse("anything")
        assert tokens == []
