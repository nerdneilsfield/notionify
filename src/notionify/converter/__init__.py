"""Markdown ↔ Notion conversion pipeline.

Public API:

- :class:`MarkdownToNotionConverter` — Markdown → Notion blocks.
- :class:`NotionToMarkdownRenderer` — Notion blocks → Markdown.
- :class:`ASTNormalizer` — parse and normalize Markdown to canonical AST.
- :func:`build_blocks` — convert normalized AST to Notion block dicts.
- :func:`build_rich_text` — convert inline AST tokens to rich_text arrays.
- :func:`split_rich_text` — split oversized rich_text segments.
"""

from notionify.converter.ast_normalizer import ASTNormalizer
from notionify.converter.block_builder import build_blocks
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import NotionToMarkdownRenderer
from notionify.converter.rich_text import build_rich_text, split_rich_text

__all__ = [
    "ASTNormalizer",
    "MarkdownToNotionConverter",
    "NotionToMarkdownRenderer",
    "build_blocks",
    "build_rich_text",
    "split_rich_text",
]
