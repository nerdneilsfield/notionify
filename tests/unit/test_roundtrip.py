"""Round-trip tests: Markdown → Notion blocks → Markdown.

Verifies that converting Markdown to Notion blocks and back produces
semantically equivalent output.  PRD section 1.2 requires ≥ 95%
round-trip semantic fidelity for supported block types.

Note: The renderer applies markdown_escape to plain text, which escapes
characters like `.`, `!`, `#`, etc.  Round-trip checks therefore verify
semantic content rather than exact character matches.
"""
import re

import pytest

from notionify.config import NotionifyConfig
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import NotionToMarkdownRenderer


def _config(**kwargs):
    return NotionifyConfig(token="test-token", **kwargs)


def _roundtrip(md: str, **kwargs) -> str:
    """Convert Markdown → Notion blocks → Markdown.

    Works directly without any patching — the renderer and converter both
    handle both ``plain_text`` (API format) and ``text.content`` (converter
    format) transparently.
    """
    config = _config(**kwargs)
    converter = MarkdownToNotionConverter(config)
    result = converter.convert(md)

    renderer = NotionToMarkdownRenderer(config)
    return renderer.render_blocks(result.blocks)


def _strip_escapes(text: str) -> str:
    """Remove markdown escape backslashes for semantic comparison."""
    return re.sub(r"\\(.)", r"\1", text)


def _normalize(text: str) -> str:
    """Normalize whitespace and escapes for comparison."""
    text = _strip_escapes(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class TestRoundTrip:
    """Verify Markdown → Notion → Markdown round-trip fidelity."""

    def test_headings(self):
        md = "# Heading One\n\n## Heading Two\n\n### Heading Three"
        result = _normalize(_roundtrip(md))
        assert "# Heading One" in result
        assert "## Heading Two" in result
        assert "### Heading Three" in result

    def test_paragraph(self):
        md = "This is a simple paragraph"
        result = _normalize(_roundtrip(md))
        assert "This is a simple paragraph" in result

    def test_bold_and_italic(self):
        md = "This has **bold** and *italic* text"
        result = _roundtrip(md)
        assert "**bold**" in result
        assert "italic" in result

    def test_inline_code(self):
        md = "Use the `print()` function"
        result = _roundtrip(md)
        assert "`print()`" in result

    def test_strikethrough(self):
        md = "This is ~~deleted~~ text"
        result = _roundtrip(md)
        assert "~~deleted~~" in result

    def test_link(self):
        md = "Visit [Example](https://example.com) for more"
        result = _roundtrip(md)
        assert "[Example](https://example.com)" in result

    def test_bullet_list(self):
        md = "- Item one\n- Item two\n- Item three"
        result = _normalize(_roundtrip(md))
        assert "Item one" in result
        assert "Item two" in result
        assert "Item three" in result

    def test_ordered_list(self):
        md = "1. First\n2. Second\n3. Third"
        result = _normalize(_roundtrip(md))
        assert "First" in result
        assert "Second" in result
        assert "Third" in result

    def test_task_list(self):
        md = "- [ ] Unchecked\n- [x] Checked"
        result = _roundtrip(md)
        assert "[ ]" in result or "Unchecked" in result
        assert "[x]" in result or "Checked" in result

    def test_blockquote(self):
        md = "> This is a quote"
        result = _normalize(_roundtrip(md))
        assert ">" in result
        assert "This is a quote" in result

    def test_code_block_with_language(self):
        md = "```python\ndef hello():\n    print('hi')\n```"
        result = _roundtrip(md)
        assert "```python" in result
        assert "def hello():" in result
        assert "print('hi')" in result

    def test_code_block_no_language(self):
        md = "```\nplain code\n```"
        result = _roundtrip(md)
        assert "plain code" in result

    def test_horizontal_rule(self):
        md = "Above\n\n---\n\nBelow"
        result = _normalize(_roundtrip(md))
        assert "---" in result
        assert "Above" in result
        assert "Below" in result

    def test_image_external(self):
        md = "![Alt text](https://example.com/image.png)"
        result = _roundtrip(md)
        assert "https://example.com/image.png" in result

    def test_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = _normalize(_roundtrip(md))
        assert "A" in result
        assert "B" in result

    def test_block_math_equation_strategy(self):
        md = "$$\nE = mc^2\n$$"
        result = _normalize(_roundtrip(md, math_strategy="equation"))
        assert "E = mc^2" in result

    def test_mixed_document(self):
        """Full document with multiple block types."""
        md = """# Document Title

This has **bold** and *italic* text

## Section One

- Bullet point one
- Bullet point two

### Code Example

```python
x = 42
```

> A blockquote

---

Final paragraph with a [link](https://example.com)
"""
        result = _normalize(_roundtrip(md))

        # Verify key content survives
        assert "Document Title" in result
        assert "**bold**" in result
        assert "Bullet point one" in result
        assert "x = 42" in result
        assert "A blockquote" in result
        assert "---" in result
        assert "link" in result

    def test_empty_markdown(self):
        result = _roundtrip("")
        assert result.strip() == ""

    def test_unicode_content(self):
        md = "# 标题\n\n日本語テキスト\n\n한국어 텍스트"
        result = _normalize(_roundtrip(md))
        assert "标题" in result
        assert "日本語テキスト" in result
        assert "한국어 텍스트" in result

    def test_double_roundtrip_stability(self):
        """Converting twice should produce the same output."""
        md = "# Title\n\nParagraph with **bold** text\n\n- Item one\n- Item two"
        first = _roundtrip(md)
        second = _roundtrip(first)
        assert _normalize(first) == _normalize(second)
