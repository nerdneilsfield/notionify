"""Inline rendering: Notion rich_text arrays to Markdown strings.

This module converts Notion API rich_text segment arrays into their
Markdown representation, handling annotations (bold, italic, code,
strikethrough, underline), equations, and links.

Escaping rules follow PRD section 11.4.
Annotation combination order follows PRD section 11.2.
"""

from __future__ import annotations

import re
from typing import Any, Literal

# Characters that must be escaped in inline Markdown context.
ESCAPE_CHARS = r'\`*_{}[]()#+-.!|'

_ESCAPE_RE = re.compile(r'([\\`*_{}\[\]()#+\-.!|])')


def markdown_escape(text: str, context: Literal["inline", "code", "url"] = "inline") -> str:
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


# Annotation wrappers applied in order: bold -> italic -> strikethrough -> underline
_ANNOTATION_WRAPPERS = [
    ("bold", "**", "**"),
    ("italic", "_", "_"),
    ("strikethrough", "~~", "~~"),
    ("underline", "<u>", "</u>"),
]


def _render_code_span(content: str) -> str:
    """Wrap *content* in backtick fences per CommonMark rules.

    Uses a longer backtick delimiter when *content* contains backticks,
    and adds space padding when *content* starts or ends with a backtick.
    """
    fence = "`"
    while fence in content:
        fence += "`"
    if content.startswith("`") or content.endswith("`"):
        return f"{fence} {content} {fence}"
    return f"{fence}{content}{fence}"


def _render_segment_text(seg: dict[str, Any]) -> str:
    """Render a single segment's text with annotations (but not the link wrapper)."""
    seg_type = seg.get("type", "text")
    annotations = seg.get("annotations") or {}

    if seg_type == "equation":
        expression = (seg.get("equation") or {}).get("expression") or ""
        return f"${expression}$"

    plain_text = seg.get("plain_text") or (seg.get("text") or {}).get("content") or ""
    is_code = annotations.get("code", False)

    if is_code:
        return _render_code_span(plain_text)

    text = markdown_escape(plain_text)
    for key, prefix, suffix in _ANNOTATION_WRAPPERS:
        if annotations.get(key, False):
            text = f"{prefix}{text}{suffix}"
    return text


def render_rich_text(segments: list[dict[str, Any]]) -> str:
    """Render a Notion rich_text array to a Markdown string.

    Each *segment* is a Notion rich_text object.  The function handles
    two segment types:

    * ``"text"`` -- plain text with optional link and annotations.
    * ``"equation"`` -- inline LaTeX rendered as ``$expression$``.

    Adjacent segments sharing the same ``href`` are merged into a single
    Markdown link, preserving per-segment annotations.

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
    i = 0

    while i < len(segments):
        seg = segments[i]
        href = seg.get("href")

        if href:
            # Merge adjacent segments sharing the same href into one link.
            link_texts: list[str] = [_render_segment_text(seg)]
            j = i + 1
            while j < len(segments) and segments[j].get("href") == href:
                link_texts.append(_render_segment_text(segments[j]))
                j += 1
            combined = "".join(link_texts)
            parts.append(f"[{combined}]({markdown_escape(href, 'url')})")
            i = j
        else:
            parts.append(_render_segment_text(seg))
            i += 1

    return "".join(parts)
