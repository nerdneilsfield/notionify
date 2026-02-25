"""Tests for diff/signature.py"""
from notionify.diff.signature import (
    _extract_children_info,
    _extract_plain_text,
    _extract_type_attrs,
    compute_signature,
)


class TestExtractPlainText:
    def test_empty_rich_text(self):
        block = {"paragraph": {"rich_text": []}}
        assert _extract_plain_text(block, "paragraph") == ""

    def test_single_segment(self):
        block = {"paragraph": {"rich_text": [{"plain_text": "hello"}]}}
        assert _extract_plain_text(block, "paragraph") == "hello"

    def test_multiple_segments(self):
        block = {
            "paragraph": {
                "rich_text": [{"plain_text": "hello"}, {"plain_text": " world"}]
            }
        }
        assert _extract_plain_text(block, "paragraph") == "hello world"

    def test_missing_block_type(self):
        block = {}
        assert _extract_plain_text(block, "paragraph") == ""


class TestExtractChildrenInfo:
    def test_no_children_no_has_children(self):
        block = {}
        info = _extract_children_info(block)
        assert info == {"child_count": 0, "has_children": False}

    def test_has_children_flag(self):
        block = {"has_children": True}
        info = _extract_children_info(block)
        assert info == {"child_count": 0, "has_children": True}

    def test_with_children_list(self):
        block = {"children": [{"type": "paragraph"}, {"type": "heading_1"}]}
        info = _extract_children_info(block)
        assert info["child_count"] == 2
        assert info["child_types"] == ["paragraph", "heading_1"]

    def test_child_with_unknown_type(self):
        block = {"children": [{}]}
        info = _extract_children_info(block)
        assert info["child_types"] == ["unknown"]


class TestExtractTypeAttrs:
    def test_code_block_language(self):
        block = {"code": {"language": "python", "rich_text": []}}
        attrs = _extract_type_attrs(block, "code")
        assert attrs["language"] == "python"

    def test_image_external(self):
        block = {
            "image": {
                "type": "external",
                "external": {"url": "https://example.com/img.png"},
            }
        }
        attrs = _extract_type_attrs(block, "image")
        assert attrs["image_type"] == "external"
        assert attrs["url"] == "https://example.com/img.png"

    def test_image_file(self):
        block = {
            "image": {
                "type": "file",
                "file": {"url": "https://s3.amazonaws.com/img.png"},
            }
        }
        attrs = _extract_type_attrs(block, "image")
        assert attrs["image_type"] == "file"
        assert attrs["url"] == "https://s3.amazonaws.com/img.png"

    def test_equation_expression(self):
        block = {"equation": {"expression": "E = mc^2"}}
        attrs = _extract_type_attrs(block, "equation")
        assert attrs["expression"] == "E = mc^2"

    def test_unknown_type_returns_empty(self):
        block = {"unknown_type": {}}
        attrs = _extract_type_attrs(block, "unknown_type")
        assert attrs == {}


class TestComputeSignature:
    def test_basic_paragraph(self):
        block = {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "Hello"}]},
            "has_children": False,
        }
        sig = compute_signature(block)
        assert sig.block_type == "paragraph"
        assert sig.nesting_depth == 0

    def test_nesting_depth(self):
        block = {"type": "heading_1", "heading_1": {"rich_text": []}}
        sig = compute_signature(block, depth=2)
        assert sig.nesting_depth == 2

    def test_same_content_same_signature(self):
        block = {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "hi"}]},
        }
        sig1 = compute_signature(block)
        sig2 = compute_signature(block)
        assert sig1 == sig2

    def test_different_content_different_signature(self):
        b1 = {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "hi"}]},
        }
        b2 = {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "bye"}]},
        }
        assert compute_signature(b1).rich_text_hash != compute_signature(b2).rich_text_hash

    def test_unknown_type(self):
        block = {}
        sig = compute_signature(block)
        assert sig.block_type == "unknown"

    def test_equation_missing_expression(self):
        """Equation block without expression field defaults to empty string."""
        block = {"type": "equation", "equation": {}}
        attrs = _extract_type_attrs(block, "equation")
        assert attrs["expression"] == ""

    def test_image_missing_external_dict(self):
        """Image block with type='external' but missing external dict gets empty url."""
        block = {"type": "image", "image": {"type": "external"}}
        attrs = _extract_type_attrs(block, "image")
        assert attrs["image_type"] == "external"
        assert attrs["url"] == ""

    def test_image_missing_file_dict(self):
        """Image block with type='file' but missing file dict gets empty url."""
        block = {"type": "image", "image": {"type": "file"}}
        attrs = _extract_type_attrs(block, "image")
        assert attrs["image_type"] == "file"
        assert attrs["url"] == ""

    def test_image_unknown_subtype(self):
        """Image block with unknown sub-type still captures the type attr."""
        block = {"type": "image", "image": {"type": "unsupported"}}
        attrs = _extract_type_attrs(block, "image")
        assert attrs["image_type"] == "unsupported"
        assert "url" not in attrs

    def test_to_do_checked_attr(self):
        """To-do block extracts 'checked' attribute."""
        block = {"type": "to_do", "to_do": {"rich_text": [], "checked": True}}
        attrs = _extract_type_attrs(block, "to_do")
        assert attrs["checked"] is True

    def test_table_attrs(self):
        """Table block extracts structural attributes."""
        block = {
            "type": "table",
            "table": {"has_column_header": True, "has_row_header": False, "table_width": 3},
        }
        attrs = _extract_type_attrs(block, "table")
        assert attrs["has_column_header"] is True
        assert attrs["table_width"] == 3

    def test_divider_empty_attrs(self):
        """Divider block produces no type-specific attrs."""
        block = {"type": "divider", "divider": {}}
        attrs = _extract_type_attrs(block, "divider")
        assert attrs == {}

    def test_plain_text_segment_missing_key(self):
        """Rich text segment without 'plain_text' key defaults to empty."""
        block = {"paragraph": {"rich_text": [{"type": "text"}]}}
        text = _extract_plain_text(block, "paragraph")
        assert text == ""

    def test_signature_hashable_in_set(self):
        """Signatures with different content are distinct in a set."""
        b1 = {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "A"}]}}
        b2 = {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "B"}]}}
        s = {compute_signature(b1), compute_signature(b2)}
        assert len(s) == 2
