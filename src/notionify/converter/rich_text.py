"""Build Notion rich_text arrays from normalized inline AST tokens.

A rich_text segment is a dict in one of two forms:

Text segment::

    {
        "type": "text",
        "text": {"content": "hello"},
        "annotations": {"bold": false, "italic": false, "strikethrough": false,
                         "underline": false, "code": false, "color": "default"}
    }

Equation segment::

    {
        "type": "equation",
        "equation": {"expression": "E=mc^2"}
    }

When a link is present the text segment also carries ``"href": "https://..."``
at the top level.
"""

from __future__ import annotations

from notionify.config import NotionifyConfig
from notionify.models import ConversionWarning
from notionify.utils.text_split import split_string


# ---------------------------------------------------------------------------
# Annotation defaults
# ---------------------------------------------------------------------------

def _default_annotations() -> dict:
    """Return a fresh default Notion annotations dict."""
    return {
        "bold": False,
        "italic": False,
        "strikethrough": False,
        "underline": False,
        "code": False,
        "color": "default",
    }


def _merge_annotations(base: dict, **overrides: bool) -> dict:
    """Merge annotation overrides into a copy of *base*."""
    merged = dict(base)
    for key, value in overrides.items():
        if key in merged:
            # Annotations are OR-merged: if either is True, result is True
            merged[key] = merged[key] or value
    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_rich_text(
    children: list[dict],
    config: NotionifyConfig,
    *,
    annotations: dict | None = None,
    href: str | None = None,
    warnings: list[ConversionWarning] | None = None,
) -> list[dict]:
    """Convert inline AST tokens to Notion rich_text array.

    Handles: text, strong, emphasis, codespan, strikethrough, link, image
    (as text fallback), inline_math, softbreak, linebreak, html_inline.

    Parameters
    ----------
    children:
        List of normalized inline AST tokens.
    config:
        SDK configuration (used for math strategy decisions).
    annotations:
        Inherited annotations from a parent inline node (e.g. bold from
        a ``strong`` wrapper).  Defaults to all-false.
    href:
        Inherited link URL from a parent ``link`` node.
    warnings:
        Optional mutable list to collect :class:`ConversionWarning` instances
        generated during inline math conversion.

    Returns
    -------
    list[dict]
        A list of Notion rich_text segment dicts.
    """
    if annotations is None:
        annotations = _default_annotations()

    segments: list[dict] = []

    for token in children:
        token_type = token.get("type", "")

        if token_type == "text":
            raw = token.get("raw", "")
            if not raw:
                continue
            seg = _make_text_segment(raw, annotations, href)
            segments.append(seg)

        elif token_type == "strong":
            child_annots = _merge_annotations(annotations, bold=True)
            child_segs = build_rich_text(
                token.get("children", []), config,
                annotations=child_annots, href=href, warnings=warnings,
            )
            segments.extend(child_segs)

        elif token_type == "emphasis":
            child_annots = _merge_annotations(annotations, italic=True)
            child_segs = build_rich_text(
                token.get("children", []), config,
                annotations=child_annots, href=href, warnings=warnings,
            )
            segments.extend(child_segs)

        elif token_type == "strikethrough":
            child_annots = _merge_annotations(annotations, strikethrough=True)
            child_segs = build_rich_text(
                token.get("children", []), config,
                annotations=child_annots, href=href, warnings=warnings,
            )
            segments.extend(child_segs)

        elif token_type == "codespan":
            raw = token.get("raw", "")
            child_annots = _merge_annotations(annotations, code=True)
            seg = _make_text_segment(raw, child_annots, href)
            segments.append(seg)

        elif token_type == "link":
            link_url = token.get("attrs", {}).get("url", "")
            child_segs = build_rich_text(
                token.get("children", []), config,
                annotations=annotations, href=link_url, warnings=warnings,
            )
            segments.extend(child_segs)

        elif token_type == "image":
            # Image in inline context: render as text fallback "[alt](url)"
            alt = _extract_text(token.get("children", []))
            url = token.get("attrs", {}).get("url", "")
            if alt and url:
                text = f"[{alt}]({url})"
            elif url:
                text = url
            elif alt:
                text = alt
            else:
                text = "[image]"
            seg = _make_text_segment(text, annotations, href)
            segments.append(seg)

        elif token_type == "inline_math":
            from notionify.converter.math import build_inline_math
            expression = token.get("raw", "")
            math_seg, math_warnings = build_inline_math(expression, config)
            if warnings is not None:
                warnings.extend(math_warnings)
            # math_seg can be a single segment or list
            if isinstance(math_seg, list):
                segments.extend(math_seg)
            else:
                segments.append(math_seg)

        elif token_type == "softbreak":
            # Soft break in markdown = single newline, usually rendered as space
            seg = _make_text_segment(" ", annotations, href)
            segments.append(seg)

        elif token_type == "linebreak":
            # Hard break = literal newline
            seg = _make_text_segment("\n", annotations, href)
            segments.append(seg)

        elif token_type == "html_inline":
            # Render raw HTML as plain text
            raw = token.get("raw", "")
            if raw:
                seg = _make_text_segment(raw, annotations, href)
                segments.append(seg)

        # Unknown inline types are silently skipped

    return segments


def split_rich_text(segments: list[dict], limit: int = 2000) -> list[dict]:
    """Split any rich_text segment with content > limit into multiple segments.

    Preserves annotations on each split segment.  Never splits multi-byte
    characters (relies on :func:`split_string` which operates on Python
    code-points).

    Parameters
    ----------
    segments:
        List of Notion rich_text segment dicts.
    limit:
        Maximum character count per segment content.

    Returns
    -------
    list[dict]
        A new list where every segment's content is at most *limit* chars.
    """
    output: list[dict] = []

    for segment in segments:
        seg_type = segment.get("type", "text")

        if seg_type == "equation":
            # Equations have different limits (1000), handled by math module.
            # Pass them through unchanged here.
            output.append(segment)
            continue

        # Extract content from the text segment
        content = segment.get("text", {}).get("content", "")

        if len(content) <= limit:
            output.append(segment)
            continue

        # Split the content
        chunks = split_string(content, limit)
        for chunk in chunks:
            new_seg = _clone_text_segment(segment, chunk)
            output.append(new_seg)

    return output


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_text_segment(
    content: str,
    annotations: dict,
    href: str | None = None,
) -> dict:
    """Create a single Notion rich_text text segment."""
    seg: dict = {
        "type": "text",
        "text": {"content": content},
    }
    # Only include annotations if any are non-default
    if _has_non_default_annotations(annotations):
        seg["annotations"] = dict(annotations)
    if href:
        seg["href"] = href
    return seg


def _has_non_default_annotations(annotations: dict) -> bool:
    """Check if any annotation deviates from default values."""
    return (
        annotations.get("bold", False)
        or annotations.get("italic", False)
        or annotations.get("strikethrough", False)
        or annotations.get("underline", False)
        or annotations.get("code", False)
        or annotations.get("color", "default") != "default"
    )


def _clone_text_segment(segment: dict, new_content: str) -> dict:
    """Clone a text segment with new content, preserving annotations and href."""
    new_seg: dict = {
        "type": "text",
        "text": {"content": new_content},
    }
    if "annotations" in segment:
        new_seg["annotations"] = dict(segment["annotations"])
    if "href" in segment:
        new_seg["href"] = segment["href"]
    return new_seg


def _extract_text(children: list[dict]) -> str:
    """Recursively extract plain text from inline tokens."""
    parts: list[str] = []
    for token in children:
        token_type = token.get("type", "")
        if token_type == "text":
            parts.append(token.get("raw", ""))
        elif "children" in token:
            parts.append(_extract_text(token["children"]))
        elif "raw" in token:
            parts.append(token["raw"])
    return "".join(parts)
