"""Block signature computation for diff matching.

Computes a :class:`BlockSignature` fingerprint for each Notion block dict.
Two blocks that produce identical signatures are treated as unchanged by the
diff planner.
"""

from __future__ import annotations

from notionify.models import BlockSignature
from notionify.utils.hashing import md5_hash, hash_dict


# Block types that carry type-specific attributes worth tracking.
_ATTRS_EXTRACTORS: dict[str, list[str]] = {
    "code": ["language"],
    "to_do": ["checked"],
    "heading_1": ["is_toggleable", "color"],
    "heading_2": ["is_toggleable", "color"],
    "heading_3": ["is_toggleable", "color"],
    "callout": ["icon", "color"],
    "quote": ["color"],
    "toggle": ["color"],
    "bulleted_list_item": ["color"],
    "numbered_list_item": ["color"],
    "bookmark": ["url"],
    "embed": ["url"],
    "image": ["type"],
    "equation": ["expression"],
    "link_to_page": ["type"],
    "table": ["has_column_header", "has_row_header", "table_width"],
    "column_list": [],
    "divider": [],
}


def _extract_plain_text(block: dict, block_type: str) -> str:
    """Extract the concatenated plain_text from a block's rich_text array."""
    type_data = block.get(block_type, {})
    rich_text = type_data.get("rich_text", [])
    parts: list[str] = []
    for rt in rich_text:
        parts.append(rt.get("plain_text", ""))
    return "".join(parts)


def _extract_children_info(block: dict) -> dict:
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


def _extract_type_attrs(block: dict, block_type: str) -> dict:
    """Extract type-specific attributes for the attrs hash."""
    type_data = block.get(block_type, {})
    keys = _ATTRS_EXTRACTORS.get(block_type, [])
    attrs: dict = {}
    for key in keys:
        if key in type_data:
            attrs[key] = type_data[key]

    # For equation blocks, the expression lives at the top of type_data.
    if block_type == "equation":
        expr = type_data.get("expression", "")
        attrs["expression"] = expr

    # For image blocks, capture the image source info.
    if block_type == "image":
        img_type = type_data.get("type", "")
        attrs["image_type"] = img_type
        if img_type == "external":
            external = type_data.get("external", {})
            attrs["url"] = external.get("url", "")
        elif img_type == "file":
            file_info = type_data.get("file", {})
            attrs["url"] = file_info.get("url", "")

    return attrs


def compute_signature(block: dict, depth: int = 0) -> BlockSignature:
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

    # Rich text hash -- concatenated plain text.
    plain_text = _extract_plain_text(block, block_type)
    rich_text_hash = md5_hash(plain_text)

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
