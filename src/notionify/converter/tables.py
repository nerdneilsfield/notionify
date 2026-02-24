"""Table conversion: Markdown table AST to Notion table block.

Builds a Notion ``table`` block from the normalized table AST token
produced by mistune's ``table`` plugin.

The table AST structure (after normalization) looks like::

    {
        "type": "table",
        "children": [
            {
                "type": "table_head",
                "children": [
                    {"type": "table_cell", "attrs": {"align": null, "head": true},
                     "children": [inline tokens...]},
                    ...
                ]
            },
            {
                "type": "table_body",
                "children": [
                    {
                        "type": "table_row",
                        "children": [
                            {"type": "table_cell", "attrs": {"align": null, "head": false},
                             "children": [inline tokens...]},
                            ...
                        ]
                    },
                    ...
                ]
            }
        ]
    }

The resulting Notion block::

    {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": <num_columns>,
            "has_column_header": true,
            "has_row_header": false,
            "children": [
                {
                    "type": "table_row",
                    "table_row": {
                        "cells": [
                            [<rich_text segments>],
                            ...
                        ]
                    }
                },
                ...
            ]
        }
    }
"""

from __future__ import annotations

from notionify.config import NotionifyConfig
from notionify.errors import NotionifyConversionError
from notionify.models import ConversionWarning
from notionify.converter.rich_text import build_rich_text, split_rich_text


def build_table(
    token: dict,
    config: NotionifyConfig,
) -> tuple[dict | None, list[ConversionWarning]]:
    """Build Notion table block from table AST token.

    Parameters
    ----------
    token:
        Normalized table AST token with ``type="table"``.
    config:
        SDK configuration.

    Returns
    -------
    tuple[dict | None, list[ConversionWarning]]
        The Notion table block (or None if tables are disabled and fallback
        is ``"skip"``-like), plus any warnings.

    Raises
    ------
    NotionifyConversionError
        If ``enable_tables=False`` and ``table_fallback="raise"``.
    """
    warnings: list[ConversionWarning] = []

    if not config.enable_tables:
        return _apply_table_fallback(token, config, warnings)

    try:
        block = _build_table_block(token, config, warnings)
        return block, warnings
    except Exception as exc:
        warnings.append(ConversionWarning(
            code="TABLE_CONVERSION_ERROR",
            message=f"Table conversion failed: {exc}",
        ))
        return _apply_table_fallback(token, config, warnings)


def _build_table_block(
    token: dict,
    config: NotionifyConfig,
    warnings: list[ConversionWarning],
) -> dict:
    """Internal: build the Notion table block from the AST."""
    children = token.get("children", [])
    notion_rows: list[dict] = []
    table_width = 0

    for child in children:
        child_type = child.get("type", "")

        if child_type == "table_head":
            # Header row: each child is a table_cell
            cells = child.get("children", [])
            table_width = max(table_width, len(cells))
            row_cells = _build_row_cells(cells, config)
            notion_rows.append({
                "type": "table_row",
                "table_row": {"cells": row_cells},
            })

        elif child_type == "table_body":
            # Body rows
            for row in child.get("children", []):
                if row.get("type") == "table_row":
                    cells = row.get("children", [])
                    table_width = max(table_width, len(cells))
                    row_cells = _build_row_cells(cells, config)
                    notion_rows.append({
                        "type": "table_row",
                        "table_row": {"cells": row_cells},
                    })

    # Ensure all rows have the same width (pad with empty cells)
    for row in notion_rows:
        row_cells = row["table_row"]["cells"]
        while len(row_cells) < table_width:
            row_cells.append([])

    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": table_width,
            "has_column_header": True,
            "has_row_header": False,
            "children": notion_rows,
        },
    }


def _build_row_cells(
    cells: list[dict],
    config: NotionifyConfig,
) -> list[list[dict]]:
    """Build a list of cell rich_text arrays from table_cell tokens."""
    result: list[list[dict]] = []
    for cell in cells:
        if cell.get("type") != "table_cell":
            result.append([])
            continue
        cell_children = cell.get("children", [])
        rich_text = build_rich_text(cell_children, config)
        rich_text = split_rich_text(rich_text)
        result.append(rich_text)
    return result


def _apply_table_fallback(
    token: dict,
    config: NotionifyConfig,
    warnings: list[ConversionWarning],
) -> tuple[dict | None, list[ConversionWarning]]:
    """Apply the configured table_fallback strategy."""
    fallback = config.table_fallback

    if fallback == "raise":
        raise NotionifyConversionError(
            message="Table conversion is disabled (enable_tables=False).",
            context={"table_fallback": fallback},
        )

    warnings.append(ConversionWarning(
        code="TABLE_DISABLED",
        message="Table was not converted (tables disabled or conversion failed).",
        context={"fallback": fallback},
    ))

    if fallback == "paragraph":
        # Render the table as a plain-text paragraph
        text = _table_to_plain_text(token, config)
        block = {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": text}},
                ],
                "color": "default",
            },
        }
        return block, warnings

    # fallback == "comment" (default)
    block = {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {"type": "text", "text": {"content": "<!-- table omitted -->"}},
            ],
            "color": "default",
        },
    }
    return block, warnings


def _table_to_plain_text(token: dict, config: NotionifyConfig) -> str:
    """Extract plain text from a table token for the paragraph fallback."""
    rows: list[str] = []
    for child in token.get("children", []):
        child_type = child.get("type", "")
        if child_type == "table_head":
            row_text = _cells_to_text(child.get("children", []))
            if row_text:
                rows.append(row_text)
        elif child_type == "table_body":
            for row in child.get("children", []):
                if row.get("type") == "table_row":
                    row_text = _cells_to_text(row.get("children", []))
                    if row_text:
                        rows.append(row_text)
    return " | ".join(rows) if rows else "[table]"


def _cells_to_text(cells: list[dict]) -> str:
    """Extract plain text from a list of table_cell tokens."""
    parts: list[str] = []
    for cell in cells:
        if cell.get("type") != "table_cell":
            continue
        text = _extract_inline_text(cell.get("children", []))
        parts.append(text)
    return " | ".join(parts)


def _extract_inline_text(children: list[dict]) -> str:
    """Recursively extract plain text from inline tokens."""
    parts: list[str] = []
    for token in children:
        token_type = token.get("type", "")
        if token_type == "text":
            parts.append(token.get("raw", ""))
        elif "children" in token:
            parts.append(_extract_inline_text(token["children"]))
        elif "raw" in token:
            parts.append(token["raw"])
    return "".join(parts)
