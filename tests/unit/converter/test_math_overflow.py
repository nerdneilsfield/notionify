"""Tests for math overflow handling and table fallback paths.

Covers: block/inline math overflow with split/code/text strategies,
table disabled with paragraph/comment/raise fallbacks, and table
conversion error fallback.
"""
import pytest

from notionify.config import NotionifyConfig
from notionify.converter.math import (
    EQUATION_CHAR_LIMIT,
    build_block_math,
    build_inline_math,
)
from notionify.converter.tables import build_table
from notionify.errors import NotionifyConversionError

# ── Block math overflow ───────────────────────────────────────────────


class TestBlockMathOverflow:
    def _long_expr(self, n: int = EQUATION_CHAR_LIMIT + 100) -> str:
        return "x" * n

    def test_overflow_split_produces_multiple_blocks(self):
        config = NotionifyConfig(
            token="test", math_strategy="equation", math_overflow_block="split"
        )
        expr = self._long_expr()
        blocks, warnings = build_block_math(expr, config)
        assert len(blocks) >= 2
        assert all(b["type"] == "equation" for b in blocks)
        assert any(w.code == "MATH_OVERFLOW" for w in warnings)

    def test_overflow_code_produces_code_block(self):
        config = NotionifyConfig(
            token="test", math_strategy="equation", math_overflow_block="code"
        )
        expr = self._long_expr()
        blocks, warnings = build_block_math(expr, config)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "code"
        assert blocks[0]["code"]["language"] == "latex"
        assert any(w.code == "MATH_OVERFLOW" for w in warnings)

    def test_overflow_text_produces_paragraph(self):
        config = NotionifyConfig(
            token="test", math_strategy="equation", math_overflow_block="text"
        )
        expr = self._long_expr()
        blocks, warnings = build_block_math(expr, config)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "paragraph"
        content = blocks[0]["paragraph"]["rich_text"][0]["text"]["content"]
        assert content.startswith("$$")
        assert content.endswith("$$")
        assert any(w.code == "MATH_OVERFLOW" for w in warnings)

    def test_within_limit_no_overflow(self):
        config = NotionifyConfig(token="test", math_strategy="equation")
        expr = "x^2 + y^2 = z^2"
        blocks, warnings = build_block_math(expr, config)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "equation"
        assert len(warnings) == 0

    def test_warning_includes_context(self):
        config = NotionifyConfig(
            token="test", math_strategy="equation", math_overflow_block="code"
        )
        expr = self._long_expr(1500)
        _, warnings = build_block_math(expr, config)
        w = next(w for w in warnings if w.code == "MATH_OVERFLOW")
        assert w.context["expression_length"] == 1500
        assert w.context["limit"] == EQUATION_CHAR_LIMIT


# ── Inline math overflow ──────────────────────────────────────────────


class TestInlineMathOverflow:
    def _long_expr(self, n: int = EQUATION_CHAR_LIMIT + 100) -> str:
        return "y" * n

    def test_overflow_split_produces_multiple_segments(self):
        config = NotionifyConfig(
            token="test", math_strategy="equation", math_overflow_inline="split"
        )
        expr = self._long_expr()
        result, warnings = build_inline_math(expr, config)
        assert isinstance(result, list)
        assert len(result) >= 2
        assert all(s["type"] == "equation" for s in result)
        assert any(w.code == "MATH_OVERFLOW" for w in warnings)

    def test_overflow_code_produces_code_segment(self):
        config = NotionifyConfig(
            token="test", math_strategy="equation", math_overflow_inline="code"
        )
        expr = self._long_expr()
        result, warnings = build_inline_math(expr, config)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["annotations"]["code"] is True
        assert any(w.code == "MATH_OVERFLOW" for w in warnings)

    def test_overflow_text_produces_plain_text(self):
        config = NotionifyConfig(
            token="test", math_strategy="equation", math_overflow_inline="text"
        )
        expr = self._long_expr()
        result, warnings = build_inline_math(expr, config)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"]["content"].startswith("$")
        assert result[0]["text"]["content"].endswith("$")
        assert any(w.code == "MATH_OVERFLOW" for w in warnings)

    def test_within_limit_no_overflow(self):
        config = NotionifyConfig(token="test", math_strategy="equation")
        expr = "E = mc^2"
        result, warnings = build_inline_math(expr, config)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "equation"
        assert len(warnings) == 0


# ── Table fallback paths ──────────────────────────────────────────────


def _simple_table_token() -> dict:
    """Minimal well-formed table AST token."""
    return {
        "type": "table",
        "children": [
            {
                "type": "table_head",
                "children": [
                    {
                        "type": "table_cell",
                        "attrs": {"align": None, "head": True},
                        "children": [{"type": "text", "raw": "Header"}],
                    }
                ],
            },
            {
                "type": "table_body",
                "children": [
                    {
                        "type": "table_row",
                        "children": [
                            {
                                "type": "table_cell",
                                "attrs": {"align": None, "head": False},
                                "children": [{"type": "text", "raw": "Data"}],
                            }
                        ],
                    }
                ],
            },
        ],
    }


class TestTableFallback:
    def test_tables_enabled_builds_table(self):
        config = NotionifyConfig(token="test", enable_tables=True)
        block, warnings = build_table(_simple_table_token(), config)
        assert block is not None
        assert block["type"] == "table"
        assert block["table"]["table_width"] == 1

    def test_tables_disabled_comment_fallback(self):
        config = NotionifyConfig(token="test", enable_tables=False, table_fallback="comment")
        block, warnings = build_table(_simple_table_token(), config)
        assert block is not None
        assert block["type"] == "paragraph"
        assert "table omitted" in block["paragraph"]["rich_text"][0]["text"]["content"]
        assert any(w.code == "TABLE_DISABLED" for w in warnings)

    def test_tables_disabled_paragraph_fallback(self):
        config = NotionifyConfig(token="test", enable_tables=False, table_fallback="paragraph")
        block, warnings = build_table(_simple_table_token(), config)
        assert block is not None
        assert block["type"] == "paragraph"
        content = block["paragraph"]["rich_text"][0]["text"]["content"]
        assert "Header" in content
        assert any(w.code == "TABLE_DISABLED" for w in warnings)

    def test_tables_disabled_raise_fallback(self):
        config = NotionifyConfig(token="test", enable_tables=False, table_fallback="raise")
        with pytest.raises(NotionifyConversionError, match="disabled"):
            build_table(_simple_table_token(), config)

    def test_conversion_error_falls_back(self):
        """If table conversion fails internally, it falls back to the configured strategy."""
        config = NotionifyConfig(token="test", enable_tables=True, table_fallback="comment")
        # Malformed token that will cause an internal error
        bad_token = {"type": "table", "children": "not-a-list"}
        block, warnings = build_table(bad_token, config)
        assert block is not None
        assert any(w.code == "TABLE_CONVERSION_ERROR" for w in warnings)

    def test_table_plain_text_extraction(self):
        """Paragraph fallback extracts plain text from cells."""
        config = NotionifyConfig(token="test", enable_tables=False, table_fallback="paragraph")
        token = {
            "type": "table",
            "children": [
                {
                    "type": "table_head",
                    "children": [
                        {
                            "type": "table_cell",
                            "attrs": {},
                            "children": [{"type": "text", "raw": "Col1"}],
                        },
                        {
                            "type": "table_cell",
                            "attrs": {},
                            "children": [{"type": "text", "raw": "Col2"}],
                        },
                    ],
                },
                {
                    "type": "table_body",
                    "children": [
                        {
                            "type": "table_row",
                            "children": [
                                {
                                    "type": "table_cell",
                                    "attrs": {},
                                    "children": [{"type": "text", "raw": "A"}],
                                },
                                {
                                    "type": "table_cell",
                                    "attrs": {},
                                    "children": [{"type": "text", "raw": "B"}],
                                },
                            ],
                        }
                    ],
                },
            ],
        }
        block, warnings = build_table(token, config)
        content = block["paragraph"]["rich_text"][0]["text"]["content"]
        assert "Col1" in content
        assert "Col2" in content
        assert "A" in content
        assert "B" in content
