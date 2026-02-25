"""Dedicated unit tests for inline_renderer.py.

Tests the Notion rich_text → Markdown rendering pipeline, covering
markdown_escape, annotation rendering, equation handling, link wrapping,
and edge cases.
"""

from notionify.converter.inline_renderer import markdown_escape, render_rich_text

# =========================================================================
# markdown_escape
# =========================================================================

class TestMarkdownEscape:
    """Tests for the markdown_escape function."""

    def test_inline_escapes_special_chars(self):
        assert markdown_escape("*bold*") == r"\*bold\*"
        assert markdown_escape("_italic_") == r"\_italic\_"
        assert markdown_escape("[link](url)") == r"\[link\]\(url\)"

    def test_inline_escapes_backslash(self):
        assert markdown_escape("a\\b") == "a\\\\b"

    def test_code_context_no_escaping(self):
        assert markdown_escape("*bold*", context="code") == "*bold*"

    def test_url_context_encodes_parens(self):
        result = markdown_escape("https://example.com/path(1)", context="url")
        assert result == "https://example.com/path%281%29"

    def test_url_context_preserves_other_chars(self):
        result = markdown_escape("https://example.com/*path*", context="url")
        assert "*" in result  # parens are encoded, other chars untouched

    def test_empty_string(self):
        assert markdown_escape("") == ""

    def test_no_special_chars(self):
        assert markdown_escape("hello world") == "hello world"


# =========================================================================
# render_rich_text: basic text
# =========================================================================

class TestRenderPlainText:
    """Tests for plain text rendering."""

    def test_empty_segments(self):
        assert render_rich_text([]) == ""

    def test_single_text_segment(self):
        seg = {"type": "text", "text": {"content": "hello"}}
        assert render_rich_text([seg]) == "hello"

    def test_multiple_text_segments(self):
        segs = [
            {"type": "text", "text": {"content": "hello "}},
            {"type": "text", "text": {"content": "world"}},
        ]
        assert render_rich_text(segs) == "hello world"

    def test_plain_text_field(self):
        """API responses use 'plain_text' instead of 'text.content'."""
        seg = {"type": "text", "plain_text": "from API"}
        assert render_rich_text([seg]) == "from API"

    def test_special_chars_escaped(self):
        seg = {"type": "text", "text": {"content": "*emphasis*"}}
        result = render_rich_text([seg])
        assert result == r"\*emphasis\*"


# =========================================================================
# render_rich_text: annotations
# =========================================================================

class TestRenderAnnotations:
    """Tests for annotation rendering."""

    def test_bold(self):
        seg = {
            "type": "text",
            "text": {"content": "bold"},
            "annotations": {"bold": True},
        }
        assert render_rich_text([seg]) == "**bold**"

    def test_italic(self):
        seg = {
            "type": "text",
            "text": {"content": "italic"},
            "annotations": {"italic": True},
        }
        assert render_rich_text([seg]) == "_italic_"

    def test_strikethrough(self):
        seg = {
            "type": "text",
            "text": {"content": "deleted"},
            "annotations": {"strikethrough": True},
        }
        assert render_rich_text([seg]) == "~~deleted~~"

    def test_underline(self):
        seg = {
            "type": "text",
            "text": {"content": "underlined"},
            "annotations": {"underline": True},
        }
        assert render_rich_text([seg]) == "<u>underlined</u>"

    def test_code(self):
        seg = {
            "type": "text",
            "text": {"content": "code"},
            "annotations": {"code": True},
        }
        assert render_rich_text([seg]) == "`code`"

    def test_bold_italic_combined(self):
        seg = {
            "type": "text",
            "text": {"content": "both"},
            "annotations": {"bold": True, "italic": True},
        }
        # bold wraps first (innermost), then italic
        assert render_rich_text([seg]) == "_**both**_"

    def test_all_annotations_except_code(self):
        seg = {
            "type": "text",
            "text": {"content": "all"},
            "annotations": {
                "bold": True,
                "italic": True,
                "strikethrough": True,
                "underline": True,
            },
        }
        result = render_rich_text([seg])
        assert "**" in result
        assert "_" in result
        assert "~~" in result
        assert "<u>" in result

    def test_code_suppresses_other_annotations(self):
        """When code is True, other annotations are not applied."""
        seg = {
            "type": "text",
            "text": {"content": "code"},
            "annotations": {"code": True, "bold": True, "italic": True},
        }
        result = render_rich_text([seg])
        assert result == "`code`"
        assert "**" not in result
        assert "_" not in result


# =========================================================================
# render_rich_text: links
# =========================================================================

class TestRenderLinks:
    """Tests for link rendering."""

    def test_text_with_link(self):
        seg = {
            "type": "text",
            "text": {"content": "click here"},
            "href": "https://example.com",
        }
        assert render_rich_text([seg]) == "[click here](https://example.com)"

    def test_bold_text_with_link(self):
        seg = {
            "type": "text",
            "text": {"content": "bold link"},
            "annotations": {"bold": True},
            "href": "https://example.com",
        }
        assert render_rich_text([seg]) == "[**bold link**](https://example.com)"

    def test_link_with_parens_in_url(self):
        seg = {
            "type": "text",
            "text": {"content": "wiki"},
            "href": "https://en.wikipedia.org/wiki/Python_(programming_language)",
        }
        result = render_rich_text([seg])
        assert "%28" in result
        assert "%29" in result


# =========================================================================
# render_rich_text: equations
# =========================================================================

class TestRenderEquations:
    """Tests for equation segment rendering."""

    def test_equation_segment(self):
        seg = {
            "type": "equation",
            "equation": {"expression": "E=mc^2"},
        }
        assert render_rich_text([seg]) == "$E=mc^2$"

    def test_equation_with_link(self):
        seg = {
            "type": "equation",
            "equation": {"expression": "x^2"},
            "href": "https://math.example.com",
        }
        result = render_rich_text([seg])
        assert result == "[$x^2$](https://math.example.com)"

    def test_empty_equation(self):
        seg = {
            "type": "equation",
            "equation": {"expression": ""},
        }
        assert render_rich_text([seg]) == "$$"

    def test_mixed_text_and_equation(self):
        segs = [
            {"type": "text", "text": {"content": "Euler: "}},
            {"type": "equation", "equation": {"expression": "e^{i\\pi}+1=0"}},
        ]
        result = render_rich_text(segs)
        assert result == "Euler: $e^{i\\pi}+1=0$"


# =========================================================================
# render_rich_text: edge cases
# =========================================================================

class TestRenderEdgeCases:
    """Edge case tests for render_rich_text."""

    def test_missing_type_defaults_to_text(self):
        seg = {"text": {"content": "no type"}}
        assert render_rich_text([seg]) == "no type"

    def test_empty_text_content(self):
        seg = {"type": "text", "text": {"content": ""}}
        assert render_rich_text([seg]) == ""

    def test_missing_annotations_key(self):
        seg = {"type": "text", "text": {"content": "plain"}}
        assert render_rich_text([seg]) == "plain"

    def test_segment_with_no_text_or_plain_text(self):
        seg = {"type": "text"}
        assert render_rich_text([seg]) == ""
