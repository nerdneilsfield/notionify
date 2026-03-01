"""Block signature computation for diff matching.

Computes a :class:`BlockSignature` fingerprint for each Notion block dict.
Two blocks that produce identical signatures are treated as unchanged by the
diff planner.
"""

from __future__ import annotations

from typing import Any

from notionify.models import BlockSignature
from notionify.utils.hashing import hash_dict, md5_hash

# Block types that carry type-specific attributes worth tracking.
_ATTRS_EXTRACTORS: dict[str, list[str]] = {
    "paragraph": ["color"],
    "code": ["language", "caption"],
    "to_do": ["checked"],
    "heading_1": ["is_toggleable", "color"],
    "heading_2": ["is_toggleable", "color"],
    "heading_3": ["is_toggleable", "color"],
    "callout": ["icon", "color"],
    "quote": ["color"],
    "toggle": ["color"],
    "bulleted_list_item": ["color"],
    "numbered_list_item": ["color"],
    "bookmark": ["url", "caption"],
    "embed": ["url", "caption"],
    "link_preview": ["url"],
    "image": ["type", "caption"],
    "equation": ["expression"],
    "link_to_page": ["type", "page_id", "database_id"],
    "table": ["has_column_header", "has_row_header", "table_width"],
    "column_list": [],
    "divider": [],
    "video": ["type", "caption"],
    "audio": ["type", "caption"],
    "pdf": ["type", "caption"],
    "file": ["type", "caption"],
}

# Media block types that use the same nested URL structure as image blocks.
_MEDIA_BLOCK_TYPES: frozenset[str] = frozenset({"video", "audio", "pdf", "file"})


def _extract_plain_text(block: dict[str, Any], block_type: str) -> str:
    """Extract the concatenated plain_text from a block's rich_text array.

    Handles both Notion API responses (which have ``plain_text``) and
    converter-produced blocks (which store content in ``text.content``).
    """
    type_data = block.get(block_type, {})
    rich_text = type_data.get("rich_text", [])
    parts: list[str] = []
    for rt in rich_text:
        text = rt.get("plain_text", "")
        if not text:
            text = (rt.get("text") or {}).get("content") or ""
        parts.append(text)
    return "".join(parts)


def _normalize_rich_text(block: dict[str, Any], block_type: str) -> list[dict[str, Any]]:
    """Build a normalized representation of rich_text including annotations.

    Two blocks with the same plain text but different annotations (e.g. bold
    vs italic) will produce different normalized representations, ensuring
    distinct signatures as required by the PRD.
    """
    type_data = block.get(block_type, {})
    rich_text = type_data.get("rich_text", [])
    segments: list[dict[str, Any]] = []
    for rt in rich_text:
        text = rt.get("plain_text", "")
        if not text:
            text = (rt.get("text") or {}).get("content") or ""
        segment: dict[str, Any] = {"text": text}
        annotations = rt.get("annotations")
        if annotations:
            segment["annotations"] = annotations
        href = rt.get("href")
        if href:
            segment["href"] = href
        segments.append(segment)
    return segments


def _normalize_table_row_cells(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a normalized representation of all cells in a table_row block.

    A table_row's ``cells`` field is a list of lists — one inner list per
    column.  Each inner list contains rich_text objects with the same structure
    as a normal ``rich_text`` array.  We flatten all cells into a single
    ordered list of segments, inserting ``{"cell_boundary": <idx>}`` sentinels
    between cells so that different distributions of the same text across cells
    (e.g. ``["a", "b"]`` vs ``["ab", ""]``) still produce distinct hashes.
    """
    type_data = block.get("table_row", {})
    cells = type_data.get("cells", [])
    result: list[dict[str, Any]] = []
    for cell_idx, cell in enumerate(cells):
        result.append({"cell_boundary": cell_idx})
        for rt in cell:
            text = rt.get("plain_text", "")
            if not text:
                text = (rt.get("text") or {}).get("content") or ""
            segment: dict[str, Any] = {"text": text}
            annotations = rt.get("annotations")
            if annotations:
                segment["annotations"] = annotations
            href = rt.get("href")
            if href:
                segment["href"] = href
            result.append(segment)
    return result


def _extract_children_info(block: dict[str, Any]) -> dict[str, Any]:
    """Build a dict summarising child blocks for structural hashing."""
    children = block.get("children", [])
    if not children:
        has_children = block.get("has_children", False)
        return {"child_count": 0, "has_children": has_children}

    child_types = [child.get("type", "unknown") for child in children]
    return {
        "child_count": len(children),
        "child_types": child_types,
    }


def _extract_file_source_attrs(type_data: dict[str, Any], attrs: dict[str, Any]) -> None:
    """Populate *attrs* with the URL or upload ID from a Notion file-source dict.

    Notion uses this same ``{type, external/file/file_upload}`` structure for
    image, video, audio, pdf, and file blocks.  Extracting the URL ensures that
    two blocks with different media sources produce different signatures.
    """
    source_type = type_data.get("type", "")
    if source_type == "external":
        external = type_data.get("external") or {}
        attrs["url"] = external.get("url", "")
    elif source_type == "file":
        file_info = type_data.get("file") or {}
        attrs["url"] = file_info.get("url", "")
    elif source_type == "file_upload":
        file_upload = type_data.get("file_upload") or {}
        attrs["upload_id"] = file_upload.get("id", "")


def _extract_type_attrs(block: dict[str, Any], block_type: str) -> dict[str, Any]:
    """Extract type-specific attributes for the attrs hash."""
    type_data = block.get(block_type, {})
    keys = _ATTRS_EXTRACTORS.get(block_type, [])
    attrs: dict[str, Any] = {}
    for key in keys:
        if key in type_data:
            attrs[key] = type_data[key]

    # For equation blocks, the expression lives at the top of type_data.
    if block_type == "equation":
        expr = type_data.get("expression") or ""
        attrs["expression"] = expr

    # For image blocks, capture the image source info.
    if block_type == "image":
        attrs["image_type"] = type_data.get("type", "")
        _extract_file_source_attrs(type_data, attrs)

    # For media blocks (video, audio, pdf, file), capture the media source URL
    # using the same nested structure as image blocks.  Without this, two blocks
    # of the same media type but with different URLs produce identical signatures,
    # causing the diff planner to treat them as unchanged.
    if block_type in _MEDIA_BLOCK_TYPES:
        _extract_file_source_attrs(type_data, attrs)

    return attrs


def compute_signature(block: dict[str, Any], depth: int = 0) -> BlockSignature:
    """Compute a structural signature for a Notion block dict.

    Used for diff matching -- same content produces the same signature.

    Parameters
    ----------
    block:
        A Notion block dictionary (as returned by the API or produced by
        the converter).
    depth:
        Nesting depth of this block (root children are depth 0).

    Returns
    -------
    BlockSignature
        A frozen dataclass suitable for equality comparison and hashing.
    """
    block_type: str = block.get("type", "unknown")

    # Rich text hash -- includes annotations so that identical text with
    # different formatting (bold/italic/etc.) produces different signatures.
    # table_row blocks store content in ``cells`` (list-of-lists) rather than
    # a top-level ``rich_text`` array, so they need their own normalizer.
    if block_type == "table_row":
        rich_text_segments = _normalize_table_row_cells(block)
    else:
        rich_text_segments = _normalize_rich_text(block, block_type)
    rich_text_hash = hash_dict({"segments": rich_text_segments})

    # Structural hash -- child count and child types.
    children_info = _extract_children_info(block)
    structural_hash = hash_dict(children_info)

    # Attrs hash -- type-specific attributes.
    type_attrs = _extract_type_attrs(block, block_type)
    attrs_hash = hash_dict(type_attrs) if type_attrs else md5_hash("")

    return BlockSignature(
        block_type=block_type,
        rich_text_hash=rich_text_hash,
        structural_hash=structural_hash,
        attrs_hash=attrs_hash,
        nesting_depth=depth,
    )
