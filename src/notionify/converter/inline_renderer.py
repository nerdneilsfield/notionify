"""Inline rendering: Notion rich_text arrays to Markdown strings.

This module converts Notion API rich_text segment arrays into their
Markdown representation, handling annotations (bold, italic, code,
strikethrough, underline), equations, and links.

Escaping rules follow PRD section 11.4.
Annotation combination order follows PRD section 11.2.
"""

from __future__ import annotations

import re

# Characters that must be escaped in inline Markdown context.
ESCAPE_CHARS = r'\`*_{}[]()#+-.!|'

_ESCAPE_RE = re.compile(r'([\\`*_{}\[\]()#+\-.!|])')


def markdown_escape(text: str, context: str = "inline") -> str:
    """Escape special Markdown characters.

    Parameters
    ----------
    text:
        The raw text to escape.
    context:
        One of ``"inline"``, ``"code"``, or ``"url"``.

        * ``"inline"`` -- escape all special Markdown characters.
        * ``"code"`` -- no escaping (content is inside a code span/block).
        * ``"url"`` -- only percent-encode parentheses so links parse correctly.

    Returns
    -------
    str
        The escaped text.
    """
    if context == "code":
        return text
    if context == "url":
        return text.replace("(", "%28").replace(")", "%29")
    return _ESCAPE_RE.sub(r'\\\1', text)


def render_rich_text(segments: list[dict]) -> str:
    """Render a Notion rich_text array to a Markdown string.

    Each *segment* is a Notion rich_text object.  The function handles
    two segment types:

    * ``"text"`` -- plain text with optional link and annotations.
    * ``"equation"`` -- inline LaTeX rendered as ``$expression$``.

    Annotation combination order (innermost first)::

        code -> bold -> italic -> strikethrough -> underline -> link

    This matches PRD section 11.2.

    Parameters
    ----------
    segments:
        A list of Notion rich_text objects (dicts).

    Returns
    -------
    str
        The rendered Markdown string.
    """
    if not segments:
        return ""

    parts: list[str] = []

    for seg in segments:
        seg_type = seg.get("type", "text")
        annotations = seg.get("annotations", {})
        href = seg.get("href")

        # --- Equation segments bypass normal text handling ---------------
        if seg_type == "equation":
            expression = seg.get("equation", {}).get("expression", "")
            text = f"${expression}$"
            # Equations may still have a link
            if href:
                text = f"[{text}]({markdown_escape(href, 'url')})"
            parts.append(text)
            continue

        # --- Text segments -----------------------------------------------
        # API responses use "plain_text"; locally-built blocks use "text.content".
        plain_text = seg.get("plain_text", "") or seg.get("text", {}).get("content", "")

        is_code = annotations.get("code", False)

        if is_code:
            # Inside code spans, no markdown escaping is needed.
            text = f"`{plain_text}`"
        else:
            text = markdown_escape(plain_text)

        # Apply annotations in the specified order (innermost first):
        # code (already applied above) -> bold -> italic -> strikethrough -> underline
        if annotations.get("bold", False) and not is_code:
            text = f"**{text}**"

        if annotations.get("italic", False) and not is_code:
            text = f"_{text}_"

        if annotations.get("strikethrough", False) and not is_code:
            text = f"~~{text}~~"

        if annotations.get("underline", False) and not is_code:
            text = f"<u>{text}</u>"

        # Link (outermost wrapping)
        if href:
            text = f"[{text}]({markdown_escape(href, 'url')})"

        parts.append(text)

    return "".join(parts)
