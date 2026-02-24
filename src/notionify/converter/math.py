"""Math conversion: LaTeX to Notion equation/code/text.

Implements the math strategy decision tree from PRD section 10.4:

Inline math token::

    strategy == "equation"   -> rich_text equation object
      len > 1000 -> math_overflow_inline:
        "split"  -> split expression (best-effort)
        "code"   -> annotation code=True
        "text"   -> plain text with $...$
    strategy == "code"       -> annotation code=True
    strategy == "latex_text" -> plain text with $...$

Block math token::

    strategy == "equation"   -> equation block
      len > 1000 -> math_overflow_block:
        "split"  -> split into multiple equation blocks
        "code"   -> code block language="latex"
        "text"   -> paragraph with $$...$$
    strategy == "code"       -> code block language="latex"
    strategy == "latex_text" -> paragraph with $$...$$

The Notion API equation limit is 1000 characters.
"""

from __future__ import annotations

from notionify.config import NotionifyConfig
from notionify.models import ConversionWarning
from notionify.utils.text_split import split_string

EQUATION_CHAR_LIMIT: int = 1000


# ---------------------------------------------------------------------------
# Block-level math
# ---------------------------------------------------------------------------

def build_block_math(
    expression: str,
    config: NotionifyConfig,
) -> tuple[list[dict], list[ConversionWarning]]:
    """Build Notion block(s) for block-level math.

    Returns a *list* of blocks because overflow with ``"split"`` strategy
    may produce multiple equation blocks.

    Parameters
    ----------
    expression:
        The raw LaTeX expression (without ``$$`` delimiters).
    config:
        SDK configuration.

    Returns
    -------
    tuple[list[dict], list[ConversionWarning]]
        (blocks, warnings)
    """
    strategy = config.math_strategy
    warnings: list[ConversionWarning] = []

    if strategy == "equation":
        if len(expression) <= EQUATION_CHAR_LIMIT:
            block = _make_equation_block(expression)
            return [block], warnings

        # Overflow
        warnings.append(ConversionWarning(
            code="MATH_OVERFLOW",
            message=(
                f"Block math expression ({len(expression)} chars) exceeds "
                f"the {EQUATION_CHAR_LIMIT}-char equation limit."
            ),
            context={
                "expression_length": len(expression),
                "limit": EQUATION_CHAR_LIMIT,
                "strategy": strategy,
                "overflow": config.math_overflow_block,
            },
        ))

        overflow = config.math_overflow_block

        if overflow == "split":
            chunks = split_string(expression, EQUATION_CHAR_LIMIT)
            blocks = [_make_equation_block(chunk) for chunk in chunks]
            return blocks, warnings

        if overflow == "code":
            block = _make_code_block(expression, language="latex")
            return [block], warnings

        # overflow == "text"
        block = _make_paragraph_block(f"$${expression}$$")
        return [block], warnings

    if strategy == "code":
        block = _make_code_block(expression, language="latex")
        return [block], warnings

    # strategy == "latex_text"
    block = _make_paragraph_block(f"$${expression}$$")
    return [block], warnings


# ---------------------------------------------------------------------------
# Inline math
# ---------------------------------------------------------------------------

def build_inline_math(
    expression: str,
    config: NotionifyConfig,
) -> tuple[dict | list[dict], list[ConversionWarning]]:
    """Build rich_text segment(s) for inline math.

    Parameters
    ----------
    expression:
        The raw LaTeX expression (without ``$`` delimiters).
    config:
        SDK configuration.

    Returns
    -------
    tuple[dict | list[dict], list[ConversionWarning]]
        A single rich_text segment dict, or a list of them when splitting.
        Plus any warnings.
    """
    strategy = config.math_strategy
    warnings: list[ConversionWarning] = []

    if strategy == "equation":
        if len(expression) <= EQUATION_CHAR_LIMIT:
            seg = _make_equation_rich_text(expression)
            return seg, warnings

        # Overflow
        warnings.append(ConversionWarning(
            code="MATH_OVERFLOW",
            message=(
                f"Inline math expression ({len(expression)} chars) exceeds "
                f"the {EQUATION_CHAR_LIMIT}-char equation limit."
            ),
            context={
                "expression_length": len(expression),
                "limit": EQUATION_CHAR_LIMIT,
                "strategy": strategy,
                "overflow": config.math_overflow_inline,
            },
        ))

        overflow = config.math_overflow_inline

        if overflow == "split":
            chunks = split_string(expression, EQUATION_CHAR_LIMIT)
            segs = [_make_equation_rich_text(chunk) for chunk in chunks]
            return segs, warnings

        if overflow == "code":
            seg = _make_code_rich_text(expression)
            return seg, warnings

        # overflow == "text"
        seg = _make_plain_text_rich_text(f"${expression}$")
        return seg, warnings

    if strategy == "code":
        seg = _make_code_rich_text(expression)
        return seg, warnings

    # strategy == "latex_text"
    seg = _make_plain_text_rich_text(f"${expression}$")
    return seg, warnings


# ---------------------------------------------------------------------------
# Factory helpers — blocks
# ---------------------------------------------------------------------------

def _make_equation_block(expression: str) -> dict:
    """Create a Notion equation block."""
    return {
        "object": "block",
        "type": "equation",
        "equation": {
            "expression": expression,
        },
    }


def _make_code_block(code: str, *, language: str = "plain text") -> dict:
    """Create a Notion code block."""
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": [
                {"type": "text", "text": {"content": code}},
            ],
            "language": language,
            "caption": [],
        },
    }


def _make_paragraph_block(text: str) -> dict:
    """Create a Notion paragraph block with plain text."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {"type": "text", "text": {"content": text}},
            ],
            "color": "default",
        },
    }


# ---------------------------------------------------------------------------
# Factory helpers — rich_text segments
# ---------------------------------------------------------------------------

def _make_equation_rich_text(expression: str) -> dict:
    """Create a Notion rich_text equation segment."""
    return {
        "type": "equation",
        "equation": {"expression": expression},
    }


def _make_code_rich_text(text: str) -> dict:
    """Create a Notion rich_text text segment with code annotation."""
    return {
        "type": "text",
        "text": {"content": text},
        "annotations": {
            "bold": False,
            "italic": False,
            "strikethrough": False,
            "underline": False,
            "code": True,
            "color": "default",
        },
    }


def _make_plain_text_rich_text(text: str) -> dict:
    """Create a plain Notion rich_text text segment."""
    return {
        "type": "text",
        "text": {"content": text},
    }
