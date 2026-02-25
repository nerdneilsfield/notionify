"""Dedicated unit tests for inline_renderer.py.

Tests the Notion rich_text → Markdown rendering pipeline, covering
markdown_escape, annotation rendering, equation handling, link wrapping,
and edge cases.
"""

import pytest

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

    def test_equation_with_none_value_does_not_raise(self):
        """equation=None must not raise AttributeError (regression: #fix-null-equation)."""
        seg = {"type": "equation", "equation": None}
        result = render_rich_text([seg])
        assert result == "$$"

    def test_text_content_none_does_not_raise(self):
        """text.content=None must not raise TypeError (regression: #fix-null-content)."""
        seg = {"type": "text", "text": {"content": None}}
        result = render_rich_text([seg])
        assert result == ""

    def test_plain_text_none_does_not_raise(self):
        """plain_text=None (explicit None from API) must not raise TypeError."""
        seg = {"type": "text", "plain_text": None, "text": {"content": "hello"}}
        result = render_rich_text([seg])
        assert result == "hello"

    def test_unknown_segment_type_renders_as_text(self):
        """Unknown types default to 'text' type handling."""
        seg = {"type": "mention", "plain_text": "*user*"}
        result = render_rich_text([seg])
        assert result == r"\*user\*"

    def test_equation_missing_expression_key(self):
        """Equation segment with empty equation dict."""
        seg = {"type": "equation", "equation": {}}
        result = render_rich_text([seg])
        assert result == "$$"

    def test_annotations_with_false_values_no_wrapping(self):
        """All annotations explicitly set to False produces plain text."""
        seg = {
            "type": "text",
            "text": {"content": "plain"},
            "annotations": {
                "bold": False,
                "italic": False,
                "strikethrough": False,
                "underline": False,
                "code": False,
            },
        }
        assert render_rich_text([seg]) == "plain"

    def test_empty_text_with_annotations(self):
        """Empty text with bold annotation produces empty bold wrapper."""
        seg = {
            "type": "text",
            "text": {"content": ""},
            "annotations": {"bold": True},
        }
        assert render_rich_text([seg]) == "****"

    def test_empty_href_does_not_wrap_link(self):
        """Empty href string should not produce a link wrapper."""
        seg = {
            "type": "text",
            "text": {"content": "text"},
            "href": "",
        }
        # Empty string is falsy, so no link wrapping
        assert render_rich_text([seg]) == "text"

    def test_text_fallback_plain_text_over_none_content(self):
        """plain_text is preferred when text.content is None."""
        seg = {"type": "text", "plain_text": "fallback", "text": {"content": None}}
        result = render_rich_text([seg])
        assert result == "fallback"


# =========================================================================
# render_rich_text: annotation combinations (parametrized)
# =========================================================================

class TestAnnotationCombinations:
    """Exhaustive annotation combination tests per PRD section 11.2."""

    @pytest.mark.parametrize(
        ("annots", "expected_markers"),
        [
            ({"bold": True}, ["**"]),
            ({"italic": True}, ["_"]),
            ({"strikethrough": True}, ["~~"]),
            ({"underline": True}, ["<u>", "</u>"]),
            ({"bold": True, "italic": True}, ["**", "_"]),
            ({"bold": True, "strikethrough": True}, ["**", "~~"]),
            ({"bold": True, "underline": True}, ["**", "<u>"]),
            ({"italic": True, "strikethrough": True}, ["_", "~~"]),
            ({"italic": True, "underline": True}, ["_", "<u>"]),
            ({"strikethrough": True, "underline": True}, ["~~", "<u>"]),
            ({"bold": True, "italic": True, "strikethrough": True}, ["**", "_", "~~"]),
            ({"bold": True, "italic": True, "underline": True}, ["**", "_", "<u>"]),
            ({"bold": True, "strikethrough": True, "underline": True}, ["**", "~~", "<u>"]),
            ({"italic": True, "strikethrough": True, "underline": True}, ["_", "~~", "<u>"]),
            (
                {"bold": True, "italic": True, "strikethrough": True, "underline": True},
                ["**", "_", "~~", "<u>"],
            ),
        ],
        ids=[
            "bold-only",
            "italic-only",
            "strike-only",
            "underline-only",
            "bold+italic",
            "bold+strike",
            "bold+underline",
            "italic+strike",
            "italic+underline",
            "strike+underline",
            "bold+italic+strike",
            "bold+italic+underline",
            "bold+strike+underline",
            "italic+strike+underline",
            "all-four",
        ],
    )
    def test_annotation_combination(self, annots, expected_markers):
        seg = {
            "type": "text",
            "text": {"content": "x"},
            "annotations": annots,
        }
        result = render_rich_text([seg])
        for marker in expected_markers:
            assert marker in result, f"Expected {marker!r} in {result!r}"
        # "x" should still be present
        assert "x" in result

    @pytest.mark.parametrize(
        "annots",
        [
            {"code": True, "bold": True},
            {"code": True, "italic": True},
            {"code": True, "strikethrough": True},
            {"code": True, "underline": True},
            {"code": True, "bold": True, "italic": True, "strikethrough": True, "underline": True},
        ],
        ids=[
            "code+bold",
            "code+italic",
            "code+strike",
            "code+underline",
            "code+all",
        ],
    )
    def test_code_suppresses_all_other_annotations(self, annots):
        """Code annotation takes priority; other annotations are not applied."""
        seg = {
            "type": "text",
            "text": {"content": "x"},
            "annotations": annots,
        }
        result = render_rich_text([seg])
        assert result == "`x`"
        # None of the other markers should appear
        assert "**" not in result
        assert "~~" not in result
        assert "<u>" not in result
        # _ could appear in `x` but italic _ wrapping should not be present
        assert not result.startswith("_")

    def test_nesting_order_bold_inside_italic(self):
        """Bold wraps first (innermost), then italic wraps outside."""
        seg = {
            "type": "text",
            "text": {"content": "x"},
            "annotations": {"bold": True, "italic": True},
        }
        result = render_rich_text([seg])
        # Expected: _**x**_
        assert result == "_**x**_"

    def test_full_nesting_order(self):
        """All four annotations nest: underline > strikethrough > italic > bold > text."""
        seg = {
            "type": "text",
            "text": {"content": "x"},
            "annotations": {
                "bold": True,
                "italic": True,
                "strikethrough": True,
                "underline": True,
            },
        }
        result = render_rich_text([seg])
        assert result == "<u>~~_**x**_~~</u>"
