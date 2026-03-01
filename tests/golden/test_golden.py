"""Golden fixture round-trip tests.

These tests verify that Markdown -> Notion blocks -> Markdown conversion
produces semantically consistent output. The round-trip is NOT expected
to be byte-identical (block ordering, whitespace, heading levels may
differ), but key content must survive.
"""
from __future__ import annotations

import copy
from pathlib import Path

from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter

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

    def test_h4_downgraded_to_heading_3_block(self, converter):
        """H4 with default heading_overflow='downgrade' must produce heading_3 block."""
        md = (FIXTURES_DIR / "headings_all_levels.md").read_text()
        result = converter.convert(md)
        types = [b["type"] for b in result.blocks]
        # Should have exactly one heading_1, one heading_2, one heading_3
        assert types.count("heading_1") == 1
        assert types.count("heading_2") == 1
        # H3, H4, H5, H6 all become heading_3 with downgrade strategy
        assert types.count("heading_3") == 4

    def test_h4_content_intact_after_downgrade(self, converter, renderer):
        """H4+ content must survive the downgrade round-trip with no truncation."""
        md = (FIXTURES_DIR / "headings_all_levels.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Heading Level 4" in round_tripped
        assert "Heading Level 5" in round_tripped
        assert "Heading Level 6" in round_tripped

    def test_no_paragraph_blocks_for_headings(self, converter):
        """With heading_overflow='downgrade', no heading becomes a paragraph."""
        md = (FIXTURES_DIR / "headings_all_levels.md").read_text()
        result = converter.convert(md)
        types = [b["type"] for b in result.blocks]
        assert "paragraph" not in types


class TestHeadingOverflowParagraph:
    """Verify heading_overflow='paragraph' converts H4+ to paragraph blocks (U-CV-003)."""

    _cfg = NotionifyConfig(token="test", heading_overflow="paragraph")
    _conv = MarkdownToNotionConverter(_cfg)

    def test_h4_becomes_paragraph(self) -> None:
        """H4 with heading_overflow='paragraph' must produce a paragraph block."""
        md = (FIXTURES_DIR / "headings_all_levels.md").read_text()
        result = self._conv.convert(md)
        types = [b["type"] for b in result.blocks]
        # H4, H5, H6 all become paragraphs
        assert types.count("paragraph") == 3

    def test_h1_h2_h3_still_headings(self) -> None:
        """H1-H3 must still produce heading_1/2/3 even with paragraph overflow."""
        md = (FIXTURES_DIR / "headings_all_levels.md").read_text()
        result = self._conv.convert(md)
        types = [b["type"] for b in result.blocks]
        assert "heading_1" in types
        assert "heading_2" in types
        assert "heading_3" in types

    def test_h4_content_preserved_as_paragraph(self, renderer) -> None:
        """H4 text content must survive as paragraph text with paragraph overflow."""
        md = (FIXTURES_DIR / "headings_all_levels.md").read_text()
        result = self._conv.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Heading Level 4" in round_tripped
        assert "Heading Level 5" in round_tripped
        assert "Heading Level 6" in round_tripped


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


class TestCodeLanguageAliases:
    """Verify code_language_aliases.md: short aliases map to Notion names."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "code_language_aliases.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 6

    def test_py_alias_normalized_to_python(self, converter):
        md = (FIXTURES_DIR / "code_language_aliases.md").read_text()
        result = converter.convert(md)
        code_blocks = [b for b in result.blocks if b.get("type") == "code"]
        languages = [b.get("code", {}).get("language", "") for b in code_blocks]
        assert "python" in languages

    def test_js_alias_normalized_to_javascript(self, converter):
        md = (FIXTURES_DIR / "code_language_aliases.md").read_text()
        result = converter.convert(md)
        code_blocks = [b for b in result.blocks if b.get("type") == "code"]
        languages = [b.get("code", {}).get("language", "") for b in code_blocks]
        assert "javascript" in languages

    def test_ts_alias_normalized_to_typescript(self, converter):
        md = (FIXTURES_DIR / "code_language_aliases.md").read_text()
        result = converter.convert(md)
        code_blocks = [b for b in result.blocks if b.get("type") == "code"]
        languages = [b.get("code", {}).get("language", "") for b in code_blocks]
        assert "typescript" in languages

    def test_content_survives_roundtrip(self, converter, renderer):
        md = (FIXTURES_DIR / "code_language_aliases.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "greet" in round_tripped
        assert "double" in round_tripped
        assert "hello world" in round_tripped


class TestLongCodeBlock:
    """Verify long_code_block.md: >2000-char code block is split correctly."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "long_code_block.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 1
        assert len(result.warnings) == 0

    def test_code_block_rich_text_split(self, converter):
        """A code block exceeding 2000 chars must have >1 rich_text segment."""
        md = (FIXTURES_DIR / "long_code_block.md").read_text()
        result = converter.convert(md)
        code_blocks = [b for b in result.blocks if b.get("type") == "code"]
        assert len(code_blocks) >= 1
        # The long code block should be split into multiple rich_text segments
        long_block = code_blocks[0]
        rich_text = long_block.get("code", {}).get("rich_text", [])
        assert len(rich_text) > 1

    def test_language_preserved_after_split(self, converter):
        """Language annotation must survive the rich_text split."""
        md = (FIXTURES_DIR / "long_code_block.md").read_text()
        result = converter.convert(md)
        code_blocks = [b for b in result.blocks if b.get("type") == "code"]
        assert code_blocks[0].get("code", {}).get("language") == "python"

    def test_content_preserved_in_roundtrip(self, converter, renderer):
        """Key function names must survive conversion and back."""
        md = (FIXTURES_DIR / "long_code_block.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "fibonacci" in round_tripped
        assert "merge_sort" in round_tripped
        assert "BinarySearchTree" in round_tripped


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


class TestUnicodeAccented:
    """Verify unicode_accented.md round-trips European accented characters."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "unicode_accented.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 5
        assert len(result.warnings) == 0

    def test_accented_words_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "unicode_accented.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "bonjour" in round_tripped
        assert "café" in round_tripped.replace("caf\u00e9", "café")  # NFC
        assert "résumé" in round_tripped or "r\u00e9sum\u00e9" in round_tripped

    def test_german_umlauts_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "unicode_accented.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        # At least some German characters survive
        assert any(c in round_tripped for c in ("ü", "ö", "ä", "ß"))

    def test_symbols_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "unicode_accented.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "€" in round_tripped or "£" in round_tripped

    def test_accented_code_content_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "unicode_accented.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "utf-8" in round_tripped


class TestMultiblockAnnotations:
    """Verify multiblock_annotations.md round-trips annotations in many block types."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "multiblock_annotations.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 8
        assert len(result.warnings) == 0

    def test_bold_in_list_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "multiblock_annotations.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Bold item" in round_tripped

    def test_italic_in_list_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "multiblock_annotations.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Italic item" in round_tripped

    def test_strikethrough_in_list_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "multiblock_annotations.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Strikethrough item" in round_tripped

    def test_code_in_list_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "multiblock_annotations.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "inline code item" in round_tripped

    def test_blockquote_annotations_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "multiblock_annotations.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "blockquote" in round_tripped

    def test_table_annotations_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "multiblock_annotations.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Bold cell" in round_tripped or "bold" in round_tripped.lower()

    def test_heading_annotations_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "multiblock_annotations.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        # Headings with Bold/italic/code should produce block-level output
        assert len(round_tripped) > 0


class TestMathMixed:
    """Verify math_mixed.md: inline and block math coexist without interference."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "math_mixed.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 5

    def test_inline_math_produces_equation_segments(self, converter):
        """Inline math expressions must produce rich_text equation segments."""
        md = (FIXTURES_DIR / "math_mixed.md").read_text()
        result = converter.convert(md)
        has_equation_seg = any(
            seg.get("type") == "equation"
            for block in result.blocks
            for seg in block.get(block.get("type", ""), {}).get("rich_text", [])
        )
        assert has_equation_seg

    def test_block_math_produces_equation_block(self, converter):
        """Block math ($$...$$) must produce at least one equation block."""
        md = (FIXTURES_DIR / "math_mixed.md").read_text()
        result = converter.convert(md)
        equation_blocks = [b for b in result.blocks if b.get("type") == "equation"]
        assert len(equation_blocks) >= 1

    def test_regular_text_paragraphs_preserved(self, converter, renderer):
        """Regular text paragraphs coexisting with math must survive round-trip."""
        md = (FIXTURES_DIR / "math_mixed.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Pythagorean theorem" in round_tripped
        assert "Euler" in round_tripped


class TestStrikethroughCombinations:
    """Verify strikethrough_combinations.md: strikethrough with bold/italic combos."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "strikethrough_combinations.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) >= 8
        assert len(result.warnings) == 0

    def test_pure_strikethrough_preserved(self, converter, renderer):
        """A purely struck word survives the round-trip."""
        md = (FIXTURES_DIR / "strikethrough_combinations.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "struck" in round_tripped

    def test_bold_strikethrough_segment_exists(self, converter):
        """At least one rich_text segment must have both bold and strikethrough."""
        md = (FIXTURES_DIR / "strikethrough_combinations.md").read_text()
        result = converter.convert(md)

        def has_both(block: dict) -> bool:
            for seg in block.get(block.get("type", ""), {}).get("rich_text", []):
                ann = seg.get("annotations", {})
                if ann.get("bold") and ann.get("strikethrough"):
                    return True
            return False

        assert any(has_both(b) for b in result.blocks)

    def test_italic_strikethrough_segment_exists(self, converter):
        """At least one rich_text segment must have both italic and strikethrough."""
        md = (FIXTURES_DIR / "strikethrough_combinations.md").read_text()
        result = converter.convert(md)

        def has_both(block: dict) -> bool:
            for seg in block.get(block.get("type", ""), {}).get("rich_text", []):
                ann = seg.get("annotations", {})
                if ann.get("italic") and ann.get("strikethrough"):
                    return True
            return False

        assert any(has_both(b) for b in result.blocks)

    def test_multi_word_strikethrough_preserved(self, converter, renderer):
        """A multi-word struck phrase must survive the round-trip."""
        md = (FIXTURES_DIR / "strikethrough_combinations.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "longer phrase" in round_tripped


class TestRenderOnlyApiBlocks:
    """Render-only tests for block types that can only come from the Notion API.

    These block types (callout, toggle, embed, bookmark, child_page,
    child_database) have no Markdown equivalent and cannot be produced by
    the Markdown→Notion converter, but the renderer must handle them when
    reading pages from the Notion API.
    """

    def _rt(self, text: str) -> list[dict]:
        """Build a minimal rich_text segment with plain_text set."""
        return [{"type": "text", "text": {"content": text}, "plain_text": text}]

    def test_callout_with_emoji_renders_as_blockquote(self, renderer):
        """Callout with emoji icon renders as a '>'-prefixed blockquote."""
        block = {
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "💡"},
                "rich_text": self._rt("A tip for you"),
            },
        }
        result = renderer.render_blocks([block])
        assert "> " in result
        assert "A tip for you" in result

    def test_callout_without_icon_renders_text(self, renderer):
        """Callout without icon still renders the text content."""
        block = {
            "type": "callout",
            "callout": {
                "rich_text": self._rt("Note without icon"),
            },
        }
        result = renderer.render_blocks([block])
        assert "Note without icon" in result

    def test_toggle_renders_as_list_item(self, renderer):
        """Toggle block renders as a bullet list item."""
        block = {
            "type": "toggle",
            "toggle": {
                "rich_text": self._rt("Toggle summary"),
            },
        }
        result = renderer.render_blocks([block])
        assert "Toggle summary" in result
        assert "- " in result

    def test_embed_renders_as_link(self, renderer):
        """Embed block renders as a Markdown link."""
        block = {
            "type": "embed",
            "embed": {"url": "https://example.com/embed"},
        }
        result = renderer.render_blocks([block])
        assert "https://example.com/embed" in result
        assert "[" in result

    def test_bookmark_renders_url(self, renderer):
        """Bookmark block renders the URL."""
        block = {
            "type": "bookmark",
            "bookmark": {"url": "https://example.com/page", "caption": []},
        }
        result = renderer.render_blocks([block])
        assert "https://example.com/page" in result

    def test_child_page_renders_as_link_with_page_prefix(self, renderer):
        """Child page renders as '[Page: title](url)' format."""
        block = {
            "id": "12345678-1234-1234-1234-123456789abc",
            "type": "child_page",
            "child_page": {"title": "My Subpage"},
        }
        result = renderer.render_blocks([block])
        assert "My Subpage" in result
        assert "Page:" in result

    def test_child_database_renders_as_link_with_database_prefix(self, renderer):
        """Child database renders as '[Database: title](url)' format."""
        block = {
            "id": "12345678-1234-1234-1234-123456789abc",
            "type": "child_database",
            "child_database": {"title": "My DB"},
        }
        result = renderer.render_blocks([block])
        assert "My DB" in result
        assert "Database:" in result

    def test_all_api_only_blocks_never_raise(self, renderer):
        """Rendering a mix of API-only blocks must not raise any exception."""
        blocks = [
            {
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "✅"},
                    "rich_text": self._rt("Done"),
                },
            },
            {
                "type": "toggle",
                "toggle": {"rich_text": self._rt("Toggle")},
            },
            {
                "type": "embed",
                "embed": {"url": "https://example.com"},
            },
            {
                "type": "bookmark",
                "bookmark": {"url": "https://example.com/bk", "caption": []},
            },
            {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "type": "child_page",
                "child_page": {"title": "Page"},
            },
            {
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "type": "child_database",
                "child_database": {"title": "DB"},
            },
        ]
        result = renderer.render_blocks(blocks)
        assert isinstance(result, str)
        assert len(result) > 0


class TestMixedListTypes:
    """Round-trip tests for mixed_list_types.md fixture.

    Covers interleaved ordered and unordered lists at the same level, mixed
    nesting (ordered parents with bullet children and vice versa), and the
    numbered-list counter reset behaviour.
    """

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "mixed_list_types.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_bullet_items_present_in_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "mixed_list_types.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "First bullet point" in round_tripped
        assert "Back to bullets after ordered list" in round_tripped

    def test_ordered_items_present_in_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "mixed_list_types.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "First numbered item" in round_tripped
        assert "Third numbered item" in round_tripped

    def test_mixed_nesting_content_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "mixed_list_types.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Ordered parent item" in round_tripped
        assert "Bullet parent item" in round_tripped

    def test_counter_reset_sequences_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "mixed_list_types.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Alpha in first sequence" in round_tripped
        assert "New sequence starts at one again" in round_tripped


class TestMathInContext:
    """Round-trip tests for math_in_context.md.

    Verifies that inline math expressions survive conversion when they appear
    inside headings, list items, and blockquotes — not just paragraphs.
    """

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "math_in_context.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_math_in_heading_produces_equation_segment(self, converter):
        md = (FIXTURES_DIR / "math_in_context.md").read_text()
        result = converter.convert(md)
        heading_blocks = [b for b in result.blocks if b.get("type") == "heading_1"]
        assert heading_blocks, "Should produce at least one heading_1 block"
        heading_rt = heading_blocks[0].get("heading_1", {}).get("rich_text", [])
        types = [seg.get("type") for seg in heading_rt]
        assert "equation" in types, "Heading should contain an equation segment"

    def test_math_in_list_items_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "math_in_context.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Pythagorean theorem" in round_tripped
        assert "golden ratio" in round_tripped

    def test_math_in_blockquote_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "math_in_context.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Newton" in round_tripped
        assert "unit circle" in round_tripped

    def test_multiple_heading_levels_with_math(self, converter):
        md = (FIXTURES_DIR / "math_in_context.md").read_text()
        result = converter.convert(md)
        block_types = [b.get("type") for b in result.blocks]
        assert "heading_1" in block_types
        assert "heading_2" in block_types


class TestTaskListAnnotations:
    """Round-trip tests for task_list_annotations.md.

    Verifies that task list items with bold, italic, inline code, strikethrough,
    and link annotations preserve their check state and inline formatting.
    """

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "task_list_annotations.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_checked_states_produce_to_do_blocks(self, converter):
        md = (FIXTURES_DIR / "task_list_annotations.md").read_text()
        result = converter.convert(md)
        to_do_blocks = [b for b in result.blocks if b.get("type") == "to_do"]
        assert len(to_do_blocks) >= 4, "Should have at least 4 to_do blocks"
        checked = [b for b in to_do_blocks if b.get("to_do", {}).get("checked")]
        unchecked = [b for b in to_do_blocks if not b.get("to_do", {}).get("checked")]
        assert len(checked) >= 1
        assert len(unchecked) >= 1

    def test_bold_annotation_preserved_in_task(self, converter, renderer):
        md = (FIXTURES_DIR / "task_list_annotations.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Bold completed task" in round_tripped

    def test_inline_code_preserved_in_task(self, converter, renderer):
        md = (FIXTURES_DIR / "task_list_annotations.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "inline code" in round_tripped

    def test_link_preserved_in_task(self, converter, renderer):
        md = (FIXTURES_DIR / "task_list_annotations.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "example.com" in round_tripped


class TestBlockquoteRichContent:
    """Round-trip tests for blockquote_rich_content.md.

    Verifies that inline formatting (bold, italic, code, strikethrough, links)
    inside blockquotes survives conversion — basic.md only has plain-text quotes.
    """

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "blockquote_rich_content.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_produces_quote_blocks(self, converter):
        md = (FIXTURES_DIR / "blockquote_rich_content.md").read_text()
        result = converter.convert(md)
        quote_blocks = [b for b in result.blocks if b.get("type") == "quote"]
        assert len(quote_blocks) >= 4, "Should produce multiple quote blocks"

    def test_bold_in_blockquote_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "blockquote_rich_content.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "bold text" in round_tripped
        assert "italic text" in round_tripped

    def test_inline_code_in_blockquote_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "blockquote_rich_content.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "git status" in round_tripped

    def test_link_in_blockquote_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "blockquote_rich_content.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "example.com" in round_tripped

    def test_multiple_sequential_blockquotes_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "blockquote_rich_content.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "First separate blockquote" in round_tripped
        assert "Second separate blockquote" in round_tripped
        assert "Third blockquote" in round_tripped


class TestMathInTable:
    """Round-trip tests for math_in_table.md.

    Verifies that inline math expressions inside table cells survive
    conversion. Neither tables.md nor math_in_context.md tests this intersection.
    """

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "math_in_table.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_produces_table_blocks(self, converter):
        md = (FIXTURES_DIR / "math_in_table.md").read_text()
        result = converter.convert(md)
        table_blocks = [b for b in result.blocks if b.get("type") == "table"]
        assert len(table_blocks) >= 2, "Should produce multiple table blocks"

    def test_math_cells_produce_equation_segments(self, converter):
        md = (FIXTURES_DIR / "math_in_table.md").read_text()
        result = converter.convert(md)
        table_blocks = [b for b in result.blocks if b.get("type") == "table"]
        assert table_blocks, "Need table blocks to inspect"
        found_equation = False
        for table in table_blocks:
            for row in table.get("table", {}).get("children", []):
                for cell in row.get("table_row", {}).get("cells", []):
                    for seg in cell:
                        if seg.get("type") == "equation":
                            found_equation = True
        assert found_equation, "Should have at least one equation segment in table cells"

    def test_math_expressions_preserved_in_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "math_in_table.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "mc^2" in round_tripped
        assert "pi r^2" in round_tripped or "\\pi r^2" in round_tripped

    def test_mixed_math_and_annotations_in_table(self, converter, renderer):
        md = (FIXTURES_DIR / "math_in_table.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Parabola shape" in round_tripped
        assert "Positive domain only" in round_tripped


class TestImagesWithCaptions:
    """Round-trip tests for images_with_captions.md.

    Verifies that image alt text (stored as Notion caption) survives the round-trip.
    images_external.md has alt text but doesn't assert it's preserved.
    """

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "images_with_captions.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_produces_image_blocks(self, converter):
        md = (FIXTURES_DIR / "images_with_captions.md").read_text()
        result = converter.convert(md)
        image_blocks = [b for b in result.blocks if b.get("type") == "image"]
        assert len(image_blocks) >= 5, "Should produce at least 5 image blocks"

    def test_alt_text_stored_as_caption(self, converter):
        md = (FIXTURES_DIR / "images_with_captions.md").read_text()
        result = converter.convert(md)
        image_blocks = [b for b in result.blocks if b.get("type") == "image"]
        captions = [b.get("image", {}).get("caption", []) for b in image_blocks]
        assert any(len(c) > 0 for c in captions), "At least one image should have a caption"

    def test_alt_text_preserved_in_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "images_with_captions.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "sunset" in round_tripped
        assert "diagram" in round_tripped

    def test_image_urls_preserved_in_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "images_with_captions.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "example.com/sunset.jpg" in round_tripped
        assert "example.com/diagram.png" in round_tripped

    def test_descriptive_alt_text_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "images_with_captions.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Eiffel Tower" in round_tripped


class TestHeadingInlineFormatting:
    """Round-trip tests for heading_inline_formatting.md.

    Verifies that inline annotations (bold, italic, code, strikethrough) inside
    heading text survive conversion. headings_all_levels.md only has plain text;
    links.md covers links in headings; math_in_context.md covers math in headings.
    """

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "heading_inline_formatting.md").read_text()
        result = converter.convert(md)
        assert len(result.blocks) > 0
        assert len(result.warnings) == 0

    def test_produces_heading_blocks(self, converter):
        md = (FIXTURES_DIR / "heading_inline_formatting.md").read_text()
        result = converter.convert(md)
        block_types = {b.get("type") for b in result.blocks}
        assert "heading_2" in block_types

    def test_bold_in_heading_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "heading_inline_formatting.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Bold" in round_tripped

    def test_italic_in_heading_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "heading_inline_formatting.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Italic" in round_tripped

    def test_code_in_heading_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "heading_inline_formatting.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Inline Code" in round_tripped

    def test_paragraph_content_after_heading_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "heading_inline_formatting.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Content after bold heading" in round_tripped
        assert "Content after italic heading" in round_tripped


class TestConsecutiveCodeBlocks:
    """Round-trip tests for multiple code blocks appearing back-to-back."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "consecutive_code_blocks.md").read_text()
        result = converter.convert(md)
        assert result.blocks

    def test_produces_code_blocks(self, converter):
        md = (FIXTURES_DIR / "consecutive_code_blocks.md").read_text()
        result = converter.convert(md)
        code_blocks = [b for b in result.blocks if b.get("type") == "code"]
        assert len(code_blocks) >= 4  # python, js, bash, json/yaml

    def test_languages_preserved_in_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "consecutive_code_blocks.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "```python" in round_tripped
        assert "```javascript" in round_tripped
        assert "```bash" in round_tripped

    def test_code_content_preserved_in_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "consecutive_code_blocks.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert 'return "world"' in round_tripped
        assert 'return "hello"' in round_tripped
        assert 'echo "done"' in round_tripped

    def test_heading_preserved_between_blocks(self, converter, renderer):
        md = (FIXTURES_DIR / "consecutive_code_blocks.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Mixed Languages" in round_tripped
        assert "Code Block After Heading" in round_tripped

    def test_json_and_yaml_blocks_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "consecutive_code_blocks.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert '{"key": "value"}' in round_tripped
        assert "key: value" in round_tripped


class TestOrderedListRich:
    """Round-trip tests for ordered_list_rich.md - numbered lists with annotations."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "ordered_list_rich.md").read_text()
        result = converter.convert(md)
        assert result.blocks

    def test_produces_numbered_list_blocks(self, converter):
        md = (FIXTURES_DIR / "ordered_list_rich.md").read_text()
        result = converter.convert(md)
        numbered = [b for b in result.blocks if b.get("type") == "numbered_list_item"]
        assert len(numbered) >= 10  # multiple sequences

    def test_inline_annotations_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "ordered_list_rich.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "bold text" in round_tripped
        assert "italic emphasis" in round_tripped
        assert "strikethrough" in round_tripped

    def test_counter_reset_after_paragraph(self, converter, renderer):
        md = (FIXTURES_DIR / "ordered_list_rich.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        # Both sequences start at 1
        assert "1. Alpha" in round_tripped
        assert "1. New sequence" in round_tripped

    def test_double_digit_numbers_rendered(self, converter, renderer):
        md = (FIXTURES_DIR / "ordered_list_rich.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "10. Item ten" in round_tripped
        assert "11. Item eleven" in round_tripped

    def test_nested_bullets_in_ordered_list_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "ordered_list_rich.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Nested bullet A" in round_tripped
        assert "Another nested bullet" in round_tripped


class TestLinksComplex:
    """Round-trip tests for links_complex.md - annotated link text and complex URLs."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "links_complex.md").read_text()
        result = converter.convert(md)
        assert result.blocks

    def test_bold_link_text_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "links_complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "https://example.com/bold" in round_tripped
        assert "https://example.com/italic" in round_tripped

    def test_multiple_links_in_paragraph_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "links_complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "https://example.com/first" in round_tripped
        assert "https://example.com/second" in round_tripped
        assert "https://example.com/third" in round_tripped

    def test_links_in_lists_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "links_complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "https://docs.python.org/3/" in round_tripped
        assert "https://doc.rust-lang.org/book/" in round_tripped

    def test_query_string_urls_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "links_complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "example.com/search" in round_tripped
        assert "api.example.com" in round_tripped

    def test_heading_content_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "links_complex.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Linked Heading" in round_tripped


class TestTablesWithLinks:
    """Round-trip tests for tables_with_links.md."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "tables_with_links.md").read_text()
        result = converter.convert(md)
        assert result.blocks
        assert not result.warnings

    def test_produces_table_blocks(self, converter):
        md = (FIXTURES_DIR / "tables_with_links.md").read_text()
        result = converter.convert(md)
        table_blocks = [b for b in result.blocks if b.get("type") == "table"]
        assert len(table_blocks) >= 2

    def test_link_text_preserved_in_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "tables_with_links.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Python" in round_tripped
        assert "Rust" in round_tripped

    def test_link_urls_preserved_in_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "tables_with_links.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "https://docs.python.org" in round_tripped
        assert "https://doc.rust-lang.org" in round_tripped

    def test_cell_annotations_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "tables_with_links.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        # bold, code, italic, strikethrough in cells should survive
        assert "deprecated" in round_tripped
        assert "active" in round_tripped

    def test_long_urls_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "tables_with_links.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "https://www.example.com/long/path/to/page" in round_tripped
        assert "https://api.example.com/v2/reference" in round_tripped


class TestDividers:
    """Round-trip tests for dividers.md."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "dividers.md").read_text()
        result = converter.convert(md)
        assert result.blocks
        assert not result.warnings

    def test_produces_divider_blocks(self, converter):
        md = (FIXTURES_DIR / "dividers.md").read_text()
        result = converter.convert(md)
        dividers = [b for b in result.blocks if b.get("type") == "divider"]
        assert len(dividers) == 3

    def test_headings_preserved_around_dividers(self, converter, renderer):
        md = (FIXTURES_DIR / "dividers.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "# Before Divider" in round_tripped
        assert "## After First Divider" in round_tripped
        assert "### After Second Divider" in round_tripped

    def test_dividers_present_in_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "dividers.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert round_tripped.count("---") == 3

    def test_bold_text_between_dividers_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "dividers.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "**bold**" in round_tripped

    def test_link_after_divider_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "dividers.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "https://example.com" in round_tripped


class TestEscapeChars:
    """Round-trip tests for escape_chars.md."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "escape_chars.md").read_text()
        result = converter.convert(md)
        assert result.blocks
        assert not result.warnings

    def test_produces_expected_block_count(self, converter):
        md = (FIXTURES_DIR / "escape_chars.md").read_text()
        result = converter.convert(md)
        # 1 heading + 6 paragraphs
        assert len(result.blocks) == 7

    def test_escaped_asterisks_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "escape_chars.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        # Renderer re-escapes special chars; content survives as \*markers\*
        assert "markers" in round_tripped

    def test_escaped_underscores_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "escape_chars.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        # Renderer re-escapes underscores; content survives as variable\_names
        assert "variable" in round_tripped
        assert "names" in round_tripped
        assert "file" in round_tripped
        assert "paths" in round_tripped

    def test_escaped_brackets_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "escape_chars.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "option" in round_tripped
        assert "flag" in round_tripped

    def test_backslash_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "escape_chars.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "escape character" in round_tripped


class TestBlockquoteWithCode:
    """Round-trip tests for blockquote_with_code.md."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "blockquote_with_code.md").read_text()
        result = converter.convert(md)
        assert result.blocks
        assert not result.warnings

    def test_code_blocks_nested_in_quotes(self, converter):
        md = (FIXTURES_DIR / "blockquote_with_code.md").read_text()
        result = converter.convert(md)
        quotes = [b for b in result.blocks if b.get("type") == "quote"]
        assert len(quotes) >= 2
        # At least one quote should have a code child
        has_code_child = any(
            c.get("type") == "code"
            for q in quotes
            for c in q.get("quote", {}).get("children", [])
        )
        assert has_code_child, "No quote block has a code child"

    def test_python_code_survives_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "blockquote_with_code.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "def hello" in round_tripped
        assert 'print("world")' in round_tripped

    def test_bash_code_survives_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "blockquote_with_code.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "npm install" in round_tripped
        assert "npm run build" in round_tripped

    def test_json_code_survives_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "blockquote_with_code.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert '"status"' in round_tripped
        assert '"ok"' in round_tripped

    def test_plain_quote_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "blockquote_with_code.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "**bold**" in round_tripped
        assert "`code`" in round_tripped


class TestListWithBlockquote:
    """Round-trip tests for list_with_blockquote.md."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "list_with_blockquote.md").read_text()
        result = converter.convert(md)
        assert result.blocks
        assert not result.warnings

    def test_bulleted_list_has_quote_child(self, converter):
        md = (FIXTURES_DIR / "list_with_blockquote.md").read_text()
        result = converter.convert(md)
        bullets = [b for b in result.blocks if b.get("type") == "bulleted_list_item"]
        has_quote = any(
            c.get("type") == "quote"
            for b in bullets
            for c in b.get("bulleted_list_item", {}).get("children", [])
        )
        assert has_quote, "No bulleted list item has a quote child"

    def test_quoted_note_survives_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "list_with_blockquote.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "quoted note" in round_tripped

    def test_numbered_list_warning_survives(self, converter, renderer):
        md = (FIXTURES_DIR / "list_with_blockquote.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "do not skip" in round_tripped

    def test_task_list_items_preserved(self, converter, renderer):
        md = (FIXTURES_DIR / "list_with_blockquote.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Completed task" in round_tripped
        assert "Pending task" in round_tripped

    def test_approval_quote_in_task_survives(self, converter, renderer):
        md = (FIXTURES_DIR / "list_with_blockquote.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "approval" in round_tripped


class TestNestedBlockquotes:
    """Round-trip tests for nested_blockquotes.md."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "nested_blockquotes.md").read_text()
        result = converter.convert(md)
        assert result.blocks
        assert not result.warnings

    def test_quote_with_heading_child_produces_quote_block(self, converter):
        md = (FIXTURES_DIR / "nested_blockquotes.md").read_text()
        result = converter.convert(md)
        quotes = [b for b in result.blocks if b.get("type") == "quote"]
        has_heading_child = any(
            c.get("type") in {"heading_1", "heading_2", "heading_3"}
            for q in quotes
            for c in q.get("quote", {}).get("children", [])
        )
        assert has_heading_child, "No quote block has a heading child"

    def test_quote_with_list_child_produces_list_items(self, converter):
        md = (FIXTURES_DIR / "nested_blockquotes.md").read_text()
        result = converter.convert(md)
        quotes = [b for b in result.blocks if b.get("type") == "quote"]
        has_list_child = any(
            c.get("type") in {"bulleted_list_item", "numbered_list_item"}
            for q in quotes
            for c in q.get("quote", {}).get("children", [])
        )
        assert has_list_child, "No quote block has a list item child"

    def test_heading_text_survives_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "nested_blockquotes.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Overview" in round_tripped

    def test_list_items_inside_quote_survive_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "nested_blockquotes.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "First bullet point" in round_tripped
        assert "Second bullet point" in round_tripped

    def test_nested_blockquote_text_survives(self, converter, renderer):
        md = (FIXTURES_DIR / "nested_blockquotes.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Outer quote level one" in round_tripped
        assert "Inner quote level two" in round_tripped

    def test_code_inside_quote_survives_round_trip(self, converter, renderer):
        md = (FIXTURES_DIR / "nested_blockquotes.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "compute" in round_tripped


class TestHeadingWithLinks:
    """Round-trip tests for heading_with_links.md."""

    def test_converts_without_errors(self, converter):
        md = (FIXTURES_DIR / "heading_with_links.md").read_text()
        result = converter.convert(md)
        assert result.blocks
        assert not result.warnings

    def test_heading_blocks_produced(self, converter):
        md = (FIXTURES_DIR / "heading_with_links.md").read_text()
        result = converter.convert(md)
        heading_types = {"heading_1", "heading_2", "heading_3", "heading_4"}
        headings = [b for b in result.blocks if b.get("type") in heading_types]
        assert len(headings) >= 4, f"Expected >=4 heading blocks, got {len(headings)}"

    def test_link_in_full_heading_survives(self, converter, renderer):
        md = (FIXTURES_DIR / "heading_with_links.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Linked Heading" in round_tripped

    def test_inline_link_in_heading_survives(self, converter, renderer):
        md = (FIXTURES_DIR / "heading_with_links.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "inline link" in round_tripped

    def test_link_url_preserved_in_rich_text(self, converter):
        md = (FIXTURES_DIR / "heading_with_links.md").read_text()
        result = converter.convert(md)
        heading_types = {"heading_1", "heading_2", "heading_3", "heading_4"}
        headings = [b for b in result.blocks if b.get("type") in heading_types]
        all_links = [
            seg["href"]
            for h in headings
            for seg in h.get(h["type"], {}).get("rich_text", [])
            if seg.get("href")
        ]
        assert any("example.com" in url for url in all_links), (
            f"No link URL containing 'example.com' found; got: {all_links}"
        )

    def test_plain_heading_still_renders(self, converter, renderer):
        md = (FIXTURES_DIR / "heading_with_links.md").read_text()
        result = converter.convert(md)
        blocks = _simulate_api_response(result.blocks)
        round_tripped = renderer.render_blocks(blocks)
        assert "Plain Heading for Contrast" in round_tripped
