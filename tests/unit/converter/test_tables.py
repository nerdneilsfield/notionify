"""Tests for converter/tables.py internal helpers.

Covers lines missed by integration tests:
  148 - row padding with empty cells
  170-171 - non-table_cell in _build_row_cells
  250 - non-table_cell in _cells_to_text
  263-266 - children/raw branches in extract_text
"""


from notionify.config import NotionifyConfig
from notionify.converter.rich_text import extract_text
from notionify.converter.tables import (
    _build_row_cells,
    _cells_to_text,
    build_table,
)


def make_config(**kwargs):
    return NotionifyConfig(token="test-token", **kwargs)


def _text_cell(text):
    """Build a minimal table_cell token with a text inline child."""
    return {
        "type": "table_cell",
        "attrs": {"align": None, "head": False},
        "children": [{"type": "text", "raw": text}],
    }


class TestRowPadding:
    """Line 148: short rows are padded with empty cells to match table_width."""

    def test_narrow_body_row_padded(self):
        """Header has 3 columns but body row has only 2 → body padded to 3."""
        token = {
            "type": "table",
            "children": [
                {
                    "type": "table_head",
                    "children": [_text_cell("A"), _text_cell("B"), _text_cell("C")],
                },
                {
                    "type": "table_body",
                    "children": [
                        {
                            "type": "table_row",
                            "children": [_text_cell("1"), _text_cell("2")],
                        }
                    ],
                },
            ],
        }
        block, warnings = build_table(token, make_config())
        assert block is not None
        assert block["type"] == "table"
        rows = block["table"]["children"]
        # Both rows must have 3 cells
        for row in rows:
            assert len(row["table_row"]["cells"]) == 3
        # Padded cell is an empty rich_text list
        assert rows[1]["table_row"]["cells"][2] == []


class TestBuildRowCellsNonTableCell:
    """Lines 170-171: non-table_cell tokens produce empty lists."""

    def test_non_table_cell_produces_empty_entry(self):
        config = make_config()
        cells = [
            {"type": "other_token", "raw": "ignored"},
            _text_cell("hello"),
        ]
        result = _build_row_cells(cells, config)
        assert len(result) == 2
        # First entry is empty (non-table_cell)
        assert result[0] == []
        # Second entry has rich_text content
        assert len(result[1]) >= 1


class TestCellsToTextNonTableCell:
    """Line 250: non-table_cell tokens are skipped in _cells_to_text."""

    def test_non_table_cell_skipped(self):
        cells = [
            {"type": "not_a_cell", "raw": "skip me"},
            {"type": "table_cell", "children": [{"type": "text", "raw": "keep me"}]},
        ]
        result = _cells_to_text(cells)
        assert "keep me" in result
        assert "skip me" not in result


class TestExtractInlineTextBranches:
    """Lines 263-266: children and raw branches in extract_text."""

    def test_token_with_children_recurses(self):
        """A non-text token with 'children' recursively extracts text (line 263-264)."""
        tokens = [
            {
                "type": "strong",
                "children": [
                    {"type": "text", "raw": "bold text"},
                ],
            }
        ]
        result = extract_text(tokens)
        assert result == "bold text"

    def test_token_with_raw_but_no_children(self):
        """A non-text token with 'raw' but no children uses raw (lines 265-266)."""
        tokens = [
            {"type": "softline", "raw": " "},
        ]
        result = extract_text(tokens)
        assert result == " "

    def test_mixed_token_types(self):
        """Mixed text, children, and raw tokens all contribute."""
        tokens = [
            {"type": "text", "raw": "hello "},
            {"type": "em", "children": [{"type": "text", "raw": "world"}]},
            {"type": "softline", "raw": "!"},
        ]
        result = extract_text(tokens)
        assert result == "hello world!"

    def test_neither_children_nor_raw_ignored(self):
        """Tokens without text/children/raw are silently skipped."""
        tokens = [
            {"type": "unknown_token"},
            {"type": "text", "raw": "visible"},
        ]
        result = extract_text(tokens)
        assert result == "visible"
