"""Full Markdown-to-Notion conversion pipeline.

:class:`MarkdownToNotionConverter` orchestrates the three-stage pipeline:

1. **Parse** — Mistune parses raw Markdown into an AST.
2. **Normalize** — :class:`ASTNormalizer` maps token types to canonical names.
3. **Build** — :func:`build_blocks` converts normalized tokens into Notion
   API block dicts, collecting :class:`PendingImage` and
   :class:`ConversionWarning` along the way.

The result is a :class:`ConversionResult` containing the blocks, images
that need uploading, and any non-fatal warnings.
"""

from __future__ import annotations

import sys

from notionify.config import NotionifyConfig
from notionify.converter.ast_normalizer import ASTNormalizer
from notionify.converter.block_builder import build_blocks
from notionify.models import ConversionResult


class MarkdownToNotionConverter:
    """Convert Markdown text to Notion API block payloads.

    Parameters
    ----------
    config:
        SDK configuration controlling math strategy, image handling,
        table support, heading overflow, and other conversion options.

    Examples
    --------
    >>> from notionify.config import NotionifyConfig
    >>> converter = MarkdownToNotionConverter(NotionifyConfig())
    >>> result = converter.convert("# Hello\\n\\nWorld")
    >>> len(result.blocks)
    2
    >>> result.blocks[0]["type"]
    'heading_1'
    """

    def __init__(self, config: NotionifyConfig) -> None:
        self._config = config
        self._normalizer = ASTNormalizer()

    def convert(self, markdown: str) -> ConversionResult:
        """Full pipeline: parse -> normalize -> build blocks -> collect images/warnings.

        Parameters
        ----------
        markdown:
            Raw Markdown text to convert.

        Returns
        -------
        ConversionResult
            Contains ``blocks`` (list of Notion block dicts), ``images``
            (list of :class:`PendingImage`), and ``warnings`` (list of
            :class:`ConversionWarning`).
        """
        # Stage 1 & 2: Parse and normalize
        tokens = self._normalizer.parse(markdown)

        # Debug: dump normalized AST
        if self._config.debug_dump_ast:
            import json
            print(
                "[notionify] Normalized AST:",
                json.dumps(tokens, indent=2, ensure_ascii=False),
                file=sys.stderr,
            )

        # Stage 3: Build Notion blocks
        blocks, images, warnings = build_blocks(tokens, self._config)

        # Debug: dump redacted payload
        if self._config.debug_dump_payload:
            import json

            from notionify.utils.redact import redact
            safe = redact({"blocks": blocks}, self._config.token)
            print(
                "[notionify] Notion blocks payload:",
                json.dumps(safe["blocks"], indent=2, ensure_ascii=False),
                file=sys.stderr,
            )

        return ConversionResult(
            blocks=blocks,
            images=images,
            warnings=warnings,
        )
