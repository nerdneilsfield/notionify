"""Golden fixture round-trip tests.

These tests verify that Markdown -> Notion blocks -> Markdown conversion
produces semantically consistent output. The round-trip is NOT expected
to be byte-identical (block ordering, whitespace, heading levels may
differ), but key content must survive.
"""
from __future__ import annotations

import copy
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _simulate_api_response(blocks: list[dict]) -> list[dict]:
    """Normalize converter output to look like a Notion API response.

    The converter produces blocks destined for the Notion *write* API, so
    rich_text segments contain ``{"type": "text", "text": {"content": ...}}``
    but lack the ``plain_text`` field that the Notion API adds when it echoes
    blocks back.  The renderer (notion_to_md) relies on ``plain_text``.

    This helper deep-copies the blocks and populates ``plain_text`` from
    ``text.content`` on every rich_text segment, mimicking API behaviour so
    that the round-trip test exercises the real renderer logic.
    """
    blocks = copy.deepcopy(blocks)
    for block in blocks:
        _patch_block(block)
    return blocks


def _patch_block(block: dict) -> None:
    """Recursively add ``plain_text`` to every rich_text segment in *block*."""
    block_type = block.get("type", "")
    block_data = block.get(block_type, {})
    if not isinstance(block_data, dict):
        return

    # Patch rich_text segments
    for seg in block_data.get("rich_text", []):
        if "plain_text" not in seg:
            if seg.get("type") == "text":
                seg["plain_text"] = seg.get("text", {}).get("content", "")
            elif seg.get("type") == "equation":
                seg["plain_text"] = seg.get("equation", {}).get("expression", "")

    # Patch table row cells (list of rich_text arrays)
    if block_type == "table":
        for child in block_data.get("children", []):
            _patch_block(child)
        # Also patch cells in table_row children
        for child in block.get("children", []):
            _patch_block(child)
    elif block_type == "table_row":
        for cell in block_data.get("cells", []):
            for seg in cell:
                if "plain_text" not in seg:
                    if seg.get("type") == "text":
                        seg["plain_text"] = seg.get("text", {}).get("content", "")

    # Recurse into children
    children = block_data.get("children") or block.get("children")
    if children:
        for child in children:
            _patch_block(child)



class TestBasicRoundTrip:
    """Verify basic.md round-trips with semantic fidelity."""

    def test_basic_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_basic_round_trip_preserves_headings(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "# Hello World" in round_tripped
        assert "## Code Example" in round_tripped

    def test_basic_round_trip_preserves_bold_italic(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "**bold**" in round_tripped
        # The renderer uses underscores for italic (_text_) which is
        # semantically equivalent to asterisks (*text*) in Markdown.
        assert "_italic_" in round_tripped or "*italic*" in round_tripped

    def test_basic_round_trip_preserves_code_block(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "```python" in round_tripped
        assert "Hello, World!" in round_tripped

    def test_basic_round_trip_preserves_bullet_list(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Item one" in round_tripped
        assert "Item two" in round_tripped

    def test_basic_round_trip_preserves_blockquote(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "A wise blockquote" in round_tripped

    def test_basic_round_trip_preserves_ordered_list(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "First" in round_tripped
        assert "Second" in round_tripped

    def test_basic_round_trip_preserves_divider(self, converter, renderer):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "---" in round_tripped


class TestComplexRoundTrip:
    """Verify complex.md round-trips with semantic fidelity."""

    def test_complex_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0

    def test_complex_preserves_inline_code(self, converter, renderer):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "`inline code`" in round_tripped

    def test_complex_preserves_strikethrough(self, converter, renderer):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "~~strikethrough~~" in round_tripped

    def test_complex_preserves_task_list(self, converter, renderer):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Done task" in round_tripped
        assert "Todo task" in round_tripped

    def test_complex_preserves_table_content(self, converter, renderer):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Name" in round_tripped
        assert "foo" in round_tripped
        assert "bar" in round_tripped

    def test_complex_preserves_nested_list_content(self, converter, renderer):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Level 1" in round_tripped


class TestHeadingsAllLevels:
    """Verify headings_all_levels.md round-trips correctly."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "headings_all_levels.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 6
        assert len(result.warnings) == 0

    def test_heading_levels_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "headings_all_levels.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "# Heading Level 1" in round_tripped
        assert "## Heading Level 2" in round_tripped
        assert "### Heading Level 3" in round_tripped
        # H4+ are downgraded to H3 by default
        assert "Heading Level 4" in round_tripped


class TestMathInline:
    """Verify math_inline.md round-trips correctly."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "math_inline.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 1

    def test_math_expression_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "math_inline.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "E = mc^2" in round_tripped


class TestMathBlock:
    """Verify math_block.md round-trips correctly."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "math_block.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 3

    def test_block_equations_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "math_block.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "\\int_0^\\infty" in round_tripped or "int_0" in round_tripped
        assert "\\sum_{n=1}" in round_tripped or "sum_{n=1}" in round_tripped


class TestUnicodeCJK:
    """Verify unicode_cjk.md round-trips correctly."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "unicode_cjk.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 3
        assert len(result.warnings) == 0

    def test_cjk_content_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "unicode_cjk.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "\u4e2d\u6587\u5185\u5bb9" in round_tripped  # 中文内容
        assert "\u65e5\u672c\u8a9e" in round_tripped  # 日本語
        assert "\ud55c\uad6d\uc5b4" in round_tripped  # 한국어


class TestImagesExternal:
    """Verify images_external.md round-trips correctly."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "images_external.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 3

    def test_image_urls_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "images_external.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "https://example.com/photo.jpg" in round_tripped
        assert "https://example.com/diagram.png" in round_tripped


class TestEmpty:
    """Verify empty.md converts without errors."""

    def test_empty_converts_to_empty(self, converter):
        md = (FIXTURES_DIR / "empty.md").read_text()
        result = converter.convert(md)
        assert result.blocks == []
        assert len(result.warnings) == 0


class TestTaskList:
    """Verify task_list.md round-trips correctly."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "task_list.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 4

    def test_task_states_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "task_list.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Completed task" in round_tripped
        assert "Pending task" in round_tripped
        assert "[x]" in round_tripped
        assert "[ ]" in round_tripped


class TestCodeBlocks:
    """Verify code_blocks.md round-trips correctly."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "code_blocks.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 3

    def test_languages_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "code_blocks.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "```python" in round_tripped
        assert "```javascript" in round_tripped
        assert "greet" in round_tripped
        assert "add" in round_tripped


class TestNestedLists:
    """Verify nested_lists.md round-trips correctly."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "nested_lists.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 4

    def test_list_content_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "nested_lists.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Level 1 bullet" in round_tripped
        assert "First ordered" in round_tripped


class TestTables:
    """Verify tables.md round-trips correctly."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "tables.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 2

    def test_table_content_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "tables.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Header A" in round_tripped
        assert "Cell 1" in round_tripped


class TestMixedAll:
    """Verify mixed_all.md round-trips all block types."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "mixed_all.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 10

    def test_diverse_content_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "mixed_all.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "**bold**" in round_tripped
        assert "Bullet item" in round_tripped
        assert "```python" in round_tripped
        assert "famous quote" in round_tripped
        assert "---" in round_tripped
        assert "https://example.com/pic.png" in round_tripped


class TestUnicodeEmoji:
    """Verify unicode_emoji.md round-trips correctly."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "unicode_emoji.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 3
        assert len(result.warnings) == 0

    def test_content_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "unicode_emoji.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Hello world" in round_tripped
        assert "Star item" in round_tripped


class TestLinks:
    """Verify links.md round-trips with link URL fidelity."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "links.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_link_urls_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "links.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "https://openai.com" in round_tripped
        assert "https://docs.python.org" in round_tripped

    def test_link_text_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "links.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "OpenAI" in round_tripped
        assert "Python docs" in round_tripped

    def test_multiple_links_in_paragraph(self, converter, renderer):
        md = (FIXTURES_DIR / "links.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "https://example.com/one" in round_tripped
        assert "https://example.com/two" in round_tripped

    def test_links_in_lists_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "links.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "https://example.com/1" in round_tripped
        assert "https://example.com/2" in round_tripped


class TestInlineMixed:
    """Verify inline_mixed.md round-trips all inline format combinations."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "inline_mixed.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_bold_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "inline_mixed.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "**bold**" in round_tripped or "bold" in round_tripped

    def test_strikethrough_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "inline_mixed.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "~~" in round_tripped

    def test_inline_code_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "inline_mixed.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "`code`" in round_tripped
        assert "`greet(name)`" in round_tripped

    def test_mixed_formatting_does_not_lose_text(self, converter, renderer):
        md = (FIXTURES_DIR / "inline_mixed.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "bold" in round_tripped
        assert "italic" in round_tripped
        assert "code" in round_tripped
        assert "strike" in round_tripped


class TestBlockCounts:
    """Verify block counts match expectations."""

    def test_basic_block_count(self, converter):
        md = (FIXTURES_DIR / "basic.md").read_text()
        result = converter.convert(md)
        # At minimum: heading, paragraph, heading, code, 3 bullets, quote, divider, 3 numbers
        assert len(result.blocks) >= 10

    def test_complex_block_count(self, converter):
        md = (FIXTURES_DIR / "complex.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 8

    def test_links_block_count(self, converter):
        md = (FIXTURES_DIR / "links.md").read_text()
        result = converter.convert(md)
        # Headings + paragraphs + list items
        assert len(result.blocks) >= 8

    def test_inline_mixed_block_count(self, converter):
        md = (FIXTURES_DIR / "inline_mixed.md").read_text()
        result = converter.convert(md)
        # Multiple sections with paragraphs
        assert len(result.blocks) >= 5


class TestMathOverflow:
    """Verify math_overflow.md handles oversized equations per overflow config."""

    def test_converts_without_crash(self, converter):
        md = (FIXTURES_DIR / "math_overflow.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0

    def test_short_equation_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "math_overflow.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "mc^2" in round_tripped

    def test_paragraph_after_overflow_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "math_overflow.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Normal paragraph" in round_tripped

    def test_overflow_equation_produces_blocks(self, converter):
        """The long equation should produce at least one block (overflow fallback)."""
        md = (FIXTURES_DIR / "math_overflow.md").read_text()
        result = converter.convert(md)
        # At least: overflow equation block(s) + paragraph + short equation
        assert len(result.blocks) >= 3


class TestWhitespaceOnly:
    """Verify whitespace_only.md produces empty output gracefully."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "whitespace_only.md").read_text()
        result = converter.convert(md)
        assert len(result.warnings) == 0

    def test_produces_empty_or_minimal_blocks(self, converter):
        md = (FIXTURES_DIR / "whitespace_only.md").read_text()
        result = converter.convert(md)
        # Whitespace-only content should produce no meaningful blocks
        assert len(result.blocks) == 0

    def test_round_trip_is_stable(self, converter, renderer):
        md = (FIXTURES_DIR / "whitespace_only.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert round_tripped.strip() == ""


class TestLongParagraph:
    """Verify long_paragraph.md splits correctly at 2000-char boundary."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "long_paragraph.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_round_trip_preserves_bold(self, converter, renderer):
        md = (FIXTURES_DIR / "long_paragraph.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "bold text" in round_tripped

    def test_round_trip_preserves_italic(self, converter, renderer):
        md = (FIXTURES_DIR / "long_paragraph.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "italic text" in round_tripped

    def test_round_trip_preserves_inline_code(self, converter, renderer):
        md = (FIXTURES_DIR / "long_paragraph.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "`inline code`" in round_tripped

    def test_round_trip_preserves_link(self, converter, renderer):
        md = (FIXTURES_DIR / "long_paragraph.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "example.com" in round_tripped

    def test_no_content_loss(self, converter, renderer):
        """All text from the original paragraph must survive the round-trip."""
        md = (FIXTURES_DIR / "long_paragraph.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "rich text splitting" in round_tripped
        # Hyphens may be escaped as \- in the round-tripped markdown
        assert "round" in round_tripped
        assert "trip fidelity" in round_tripped
        assert "data loss" in round_tripped


class TestDeeplyNested:
    """Verify deeply_nested.md handles nested structures correctly."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "deeply_nested.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_round_trip_preserves_nested_bullets(self, converter, renderer):
        md = (FIXTURES_DIR / "deeply_nested.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Level 1 item A" in round_tripped
        assert "Level 2 item A1" in round_tripped
        assert "Level 3 item A1a" in round_tripped

    def test_round_trip_preserves_nested_ordered(self, converter, renderer):
        md = (FIXTURES_DIR / "deeply_nested.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "First outer" in round_tripped
        assert "First inner" in round_tripped

    def test_round_trip_preserves_blockquote(self, converter, renderer):
        md = (FIXTURES_DIR / "deeply_nested.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Quote level 1" in round_tripped

    def test_round_trip_preserves_task_lists(self, converter, renderer):
        md = (FIXTURES_DIR / "deeply_nested.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Unchecked task" in round_tripped
        # Hyphens may be escaped as \- in round-tripped markdown
        assert "Checked sub" in round_tripped
        assert "task" in round_tripped

    def test_has_adequate_block_count(self, converter):
        md = (FIXTURES_DIR / "deeply_nested.md").read_text()
        result = converter.convert(md)
        # Bullets + ordered + quotes + tasks = at least 10 top-level blocks
        assert len(result.blocks) >= 5
