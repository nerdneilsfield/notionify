"""Tests for diff/signature.py"""
from notionify.diff.signature import (
    _extract_children_info,
    _extract_plain_text,
    _extract_type_attrs,
    _normalize_rich_text,
    _normalize_table_row_cells,
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

    def test_image_file_upload(self):
        """Uploaded images use 'file_upload' type with 'id' instead of 'url'."""
        block = {
            "image": {
                "type": "file_upload",
                "file_upload": {"id": "abc-123-upload"},
            }
        }
        attrs = _extract_type_attrs(block, "image")
        assert attrs["image_type"] == "file_upload"
        assert attrs["upload_id"] == "abc-123-upload"

    def test_equation_expression(self):
        block = {"equation": {"expression": "E = mc^2"}}
        attrs = _extract_type_attrs(block, "equation")
        assert attrs["expression"] == "E = mc^2"

    def test_unknown_type_returns_empty(self):
        block = {"unknown_type": {}}
        attrs = _extract_type_attrs(block, "unknown_type")
        assert attrs == {}

    # -----------------------------------------------------------------------
    # Media block URL extraction (video, audio, pdf, file)
    # -----------------------------------------------------------------------

    def test_video_external_url_extracted(self):
        """video block with external source captures the URL in attrs."""
        block = {
            "video": {
                "type": "external",
                "external": {"url": "https://youtube.com/watch?v=abc"},
            }
        }
        attrs = _extract_type_attrs(block, "video")
        assert attrs["url"] == "https://youtube.com/watch?v=abc"

    def test_audio_external_url_extracted(self):
        """audio block with external source captures the URL in attrs."""
        block = {
            "audio": {
                "type": "external",
                "external": {"url": "https://example.com/track.mp3"},
            }
        }
        attrs = _extract_type_attrs(block, "audio")
        assert attrs["url"] == "https://example.com/track.mp3"

    def test_pdf_file_url_extracted(self):
        """pdf block with Notion-hosted file captures the URL in attrs."""
        block = {
            "pdf": {
                "type": "file",
                "file": {"url": "https://s3.amazonaws.com/doc.pdf"},
            }
        }
        attrs = _extract_type_attrs(block, "pdf")
        assert attrs["url"] == "https://s3.amazonaws.com/doc.pdf"

    def test_file_file_upload_id_extracted(self):
        """file block with file_upload source captures the upload ID in attrs."""
        block = {
            "file": {
                "type": "file_upload",
                "file_upload": {"id": "upload-xyz-789"},
            }
        }
        attrs = _extract_type_attrs(block, "file")
        assert attrs["upload_id"] == "upload-xyz-789"

    def test_different_video_urls_produce_different_signatures(self):
        """Two video blocks with different URLs must have different signatures."""
        block1 = {
            "type": "video",
            "video": {
                "type": "external",
                "external": {"url": "https://youtube.com/watch?v=aaa"},
            },
        }
        block2 = {
            "type": "video",
            "video": {
                "type": "external",
                "external": {"url": "https://youtube.com/watch?v=bbb"},
            },
        }
        sig1 = compute_signature(block1)
        sig2 = compute_signature(block2)
        assert sig1 != sig2, "Different video URLs must produce different signatures"

    def test_same_video_url_produces_same_signature(self):
        """Two video blocks with the same URL must have identical signatures."""
        block1 = {
            "type": "video",
            "video": {
                "type": "external",
                "external": {"url": "https://youtube.com/watch?v=same"},
            },
        }
        block2 = {
            "type": "video",
            "video": {
                "type": "external",
                "external": {"url": "https://youtube.com/watch?v=same"},
            },
        }
        sig1 = compute_signature(block1)
        sig2 = compute_signature(block2)
        assert sig1 == sig2, "Same video URL must produce identical signatures"


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

    def test_different_annotations_different_signature(self):
        """PRD: same text with different annotations must produce different signatures."""
        bold_block = {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "plain_text": "hello",
                        "annotations": {"bold": True, "italic": False},
                    }
                ]
            },
        }
        italic_block = {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "plain_text": "hello",
                        "annotations": {"bold": False, "italic": True},
                    }
                ]
            },
        }
        sig_bold = compute_signature(bold_block)
        sig_italic = compute_signature(italic_block)
        assert sig_bold.rich_text_hash != sig_italic.rich_text_hash

    def test_same_annotations_same_signature(self):
        """Blocks with identical text and annotations produce the same signature."""
        block = {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "plain_text": "hello",
                        "annotations": {"bold": True},
                    }
                ]
            },
        }
        assert compute_signature(block) == compute_signature(block)

    def test_no_annotations_vs_annotations_different_signature(self):
        """Plain text vs annotated text with same content differ."""
        plain_block = {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "hello"}]},
        }
        annotated_block = {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "plain_text": "hello",
                        "annotations": {"bold": True},
                    }
                ]
            },
        }
        assert (
            compute_signature(plain_block).rich_text_hash
            != compute_signature(annotated_block).rich_text_hash
        )

    def test_different_href_different_signature(self):
        """Same text with different links must produce different signatures."""
        link1 = {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"plain_text": "click", "href": "https://a.com"}
                ]
            },
        }
        link2 = {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"plain_text": "click", "href": "https://b.com"}
                ]
            },
        }
        assert (
            compute_signature(link1).rich_text_hash
            != compute_signature(link2).rich_text_hash
        )


    def test_bookmark_url_attr(self):
        """Bookmark block extracts URL from type-specific attributes."""
        block = {"type": "bookmark", "bookmark": {"url": "https://example.com"}}
        attrs = _extract_type_attrs(block, "bookmark")
        assert attrs["url"] == "https://example.com"

    def test_embed_url_attr(self):
        """Embed block extracts URL from type-specific attributes."""
        block = {"type": "embed", "embed": {"url": "https://youtube.com/watch?v=123"}}
        attrs = _extract_type_attrs(block, "embed")
        assert attrs["url"] == "https://youtube.com/watch?v=123"

    def test_link_preview_url_attr(self):
        """link_preview block extracts URL from type-specific attributes."""
        block = {"type": "link_preview", "link_preview": {"url": "https://example.com/post"}}
        attrs = _extract_type_attrs(block, "link_preview")
        assert attrs["url"] == "https://example.com/post"

    def test_link_preview_different_urls_different_signatures(self):
        """Two link_preview blocks with different URLs produce different signatures."""
        block_a = {"type": "link_preview", "link_preview": {"url": "https://a.com"}}
        block_b = {"type": "link_preview", "link_preview": {"url": "https://b.com"}}
        assert compute_signature(block_a) != compute_signature(block_b)

    def test_toggle_color_attr(self):
        """Toggle block extracts color attribute."""
        block = {"type": "toggle", "toggle": {"rich_text": [], "color": "red"}}
        attrs = _extract_type_attrs(block, "toggle")
        assert attrs["color"] == "red"

    def test_heading_toggleable_attr(self):
        """Heading block extracts is_toggleable and color attributes."""
        block = {
            "type": "heading_1",
            "heading_1": {"rich_text": [], "is_toggleable": True, "color": "blue"},
        }
        attrs = _extract_type_attrs(block, "heading_1")
        assert attrs["is_toggleable"] is True
        assert attrs["color"] == "blue"

    def test_column_list_empty_attrs(self):
        """column_list block has no type-specific attributes."""
        block = {"type": "column_list", "column_list": {}}
        attrs = _extract_type_attrs(block, "column_list")
        assert attrs == {}

    def test_callout_icon_and_color(self):
        """Callout block extracts icon and color."""
        block = {
            "type": "callout",
            "callout": {
                "rich_text": [],
                "icon": {"type": "emoji", "emoji": "💡"},
                "color": "yellow_background",
            },
        }
        attrs = _extract_type_attrs(block, "callout")
        assert attrs["icon"] == {"type": "emoji", "emoji": "💡"}
        assert attrs["color"] == "yellow_background"

    def test_paragraph_color_attr(self):
        """Paragraph block extracts color attribute."""
        block = {"type": "paragraph", "paragraph": {"rich_text": [], "color": "red"}}
        attrs = _extract_type_attrs(block, "paragraph")
        assert attrs["color"] == "red"

    def test_paragraph_no_color_empty_attrs(self):
        """Paragraph without color produces empty attrs (color is optional)."""
        block = {"type": "paragraph", "paragraph": {"rich_text": []}}
        attrs = _extract_type_attrs(block, "paragraph")
        assert attrs == {}

    def test_paragraph_different_colors_different_signatures(self):
        """Two identical paragraphs with different colors differ in signature."""
        block_default = {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "hi"}], "color": "default"},
        }
        block_red = {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "hi"}], "color": "red"},
        }
        assert compute_signature(block_default) != compute_signature(block_red)

    def test_paragraph_same_color_same_signature(self):
        """Two identical paragraphs with the same color produce equal signatures."""
        block_a = {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "hello"}], "color": "blue"},
        }
        block_b = {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "hello"}], "color": "blue"},
        }
        assert compute_signature(block_a) == compute_signature(block_b)

    def test_link_to_page_page_id_extracted(self):
        """link_to_page block extracts the page_id so different targets differ."""
        block = {
            "type": "link_to_page",
            "link_to_page": {"type": "page_id", "page_id": "abc-123-def"},
        }
        attrs = _extract_type_attrs(block, "link_to_page")
        assert attrs["type"] == "page_id"
        assert attrs["page_id"] == "abc-123-def"

    def test_link_to_page_database_id_extracted(self):
        """link_to_page block extracts the database_id for database links."""
        block = {
            "type": "link_to_page",
            "link_to_page": {"type": "database_id", "database_id": "db-456-ghi"},
        }
        attrs = _extract_type_attrs(block, "link_to_page")
        assert attrs["type"] == "database_id"
        assert attrs["database_id"] == "db-456-ghi"

    def test_link_to_page_different_targets_different_signatures(self):
        """Two link_to_page blocks pointing to different pages produce different signatures."""
        block_a = {
            "type": "link_to_page",
            "link_to_page": {"type": "page_id", "page_id": "page-aaa"},
        }
        block_b = {
            "type": "link_to_page",
            "link_to_page": {"type": "page_id", "page_id": "page-bbb"},
        }
        sig_a = compute_signature(block_a)
        sig_b = compute_signature(block_b)
        assert sig_a != sig_b

    def test_link_to_page_same_target_same_signature(self):
        """Two link_to_page blocks with the same page_id produce the same signature."""
        block_a = {
            "type": "link_to_page",
            "link_to_page": {"type": "page_id", "page_id": "page-same"},
        }
        block_b = {
            "type": "link_to_page",
            "link_to_page": {"type": "page_id", "page_id": "page-same"},
        }
        sig_a = compute_signature(block_a)
        sig_b = compute_signature(block_b)
        assert sig_a == sig_b


class TestNormalizeRichText:
    def test_empty_rich_text(self):
        block = {"paragraph": {"rich_text": []}}
        assert _normalize_rich_text(block, "paragraph") == []

    def test_plain_text_only(self):
        block = {"paragraph": {"rich_text": [{"plain_text": "hello"}]}}
        result = _normalize_rich_text(block, "paragraph")
        assert len(result) == 1
        assert result[0]["text"] == "hello"
        assert "annotations" not in result[0]

    def test_with_annotations(self):
        block = {
            "paragraph": {
                "rich_text": [
                    {
                        "plain_text": "bold",
                        "annotations": {"bold": True, "italic": False},
                    }
                ]
            }
        }
        result = _normalize_rich_text(block, "paragraph")
        assert result[0]["annotations"] == {"bold": True, "italic": False}

    def test_with_href(self):
        block = {
            "paragraph": {
                "rich_text": [
                    {"plain_text": "link", "href": "https://example.com"}
                ]
            }
        }
        result = _normalize_rich_text(block, "paragraph")
        assert result[0]["href"] == "https://example.com"

    def test_fallback_to_text_content(self):
        """Converter-produced blocks use text.content instead of plain_text."""
        block = {
            "paragraph": {
                "rich_text": [{"text": {"content": "fallback"}}]
            }
        }
        result = _normalize_rich_text(block, "paragraph")
        assert result[0]["text"] == "fallback"

    def test_missing_block_type(self):
        block = {}
        assert _normalize_rich_text(block, "paragraph") == []

    def test_text_field_none_does_not_raise(self):
        """rich_text segment with text=None must not raise AttributeError."""
        block = {
            "paragraph": {
                "rich_text": [{"type": "text", "text": None}]
            }
        }
        result = _normalize_rich_text(block, "paragraph")
        assert result[0]["text"] == ""

    def test_extract_plain_text_null_text(self):
        """_extract_plain_text handles text=None without crashing."""
        block = {
            "paragraph": {
                "rich_text": [{"type": "text", "text": None}]
            }
        }
        result = _extract_plain_text(block, "paragraph")
        assert result == ""


class TestCaptionInSignature:
    """Caption changes produce different signatures for caption-bearing blocks."""

    def test_code_block_different_captions_different_signature(self):
        b1 = {
            "type": "code",
            "code": {
                "rich_text": [{"plain_text": "x = 1"}],
                "language": "python",
                "caption": [{"plain_text": "Example A"}],
            },
        }
        b2 = {
            "type": "code",
            "code": {
                "rich_text": [{"plain_text": "x = 1"}],
                "language": "python",
                "caption": [{"plain_text": "Example B"}],
            },
        }
        assert compute_signature(b1) != compute_signature(b2)

    def test_image_caption_included_in_attrs(self):
        block = {
            "type": "image",
            "image": {
                "type": "external",
                "external": {"url": "https://img.example.com/a.png"},
                "caption": [{"plain_text": "A photo"}],
            },
        }
        attrs = _extract_type_attrs(block, "image")
        assert "caption" in attrs

    def test_bookmark_caption_included_in_attrs(self):
        block = {
            "type": "bookmark",
            "bookmark": {
                "url": "https://example.com",
                "caption": [{"plain_text": "See more"}],
            },
        }
        attrs = _extract_type_attrs(block, "bookmark")
        assert "caption" in attrs


class TestNormalizeTableRowCells:
    """Tests for _normalize_table_row_cells and table_row signature correctness."""

    def _rt(self, text: str) -> dict:
        return {"plain_text": text}

    def _rt_annotated(self, text: str, bold: bool = False) -> dict:
        return {
            "plain_text": text,
            "annotations": {"bold": bold, "italic": False, "strikethrough": False,
                            "underline": False, "code": False, "color": "default"},
        }

    def test_empty_table_row(self):
        block: dict = {"table_row": {}}
        segments = _normalize_table_row_cells(block)
        assert segments == []

    def test_empty_cells_list(self):
        block = {"table_row": {"cells": []}}
        segments = _normalize_table_row_cells(block)
        assert segments == []

    def test_single_cell_single_segment(self):
        block = {"table_row": {"cells": [[self._rt("hello")]]}}
        segments = _normalize_table_row_cells(block)
        # boundary for cell 0 + the text segment
        assert segments == [{"cell_boundary": 0}, {"text": "hello"}]

    def test_two_cells_boundary_markers_present(self):
        block = {"table_row": {"cells": [[self._rt("A")], [self._rt("B")]]}}
        segments = _normalize_table_row_cells(block)
        boundaries = [s for s in segments if "cell_boundary" in s]
        assert len(boundaries) == 2
        assert boundaries[0]["cell_boundary"] == 0
        assert boundaries[1]["cell_boundary"] == 1

    def test_annotations_preserved(self):
        block = {"table_row": {"cells": [[self._rt_annotated("bold text", bold=True)]]}}
        segments = _normalize_table_row_cells(block)
        text_segs = [s for s in segments if "text" in s]
        assert len(text_segs) == 1
        assert text_segs[0]["annotations"]["bold"] is True

    def test_different_cell_content_different_signature(self):
        """Core bug fix: two table_row blocks with different cell content differ."""
        block_a = {
            "type": "table_row",
            "table_row": {"cells": [[self._rt("Alpha")], [self._rt("Beta")]]},
        }
        block_b = {
            "type": "table_row",
            "table_row": {"cells": [[self._rt("Alpha")], [self._rt("CHANGED")]]},
        }
        assert compute_signature(block_a) != compute_signature(block_b)

    def test_same_cell_content_same_signature(self):
        block_a = {
            "type": "table_row",
            "table_row": {"cells": [[self._rt("X")], [self._rt("Y")]]},
        }
        block_b = {
            "type": "table_row",
            "table_row": {"cells": [[self._rt("X")], [self._rt("Y")]]},
        }
        assert compute_signature(block_a) == compute_signature(block_b)

    def test_different_cell_count_different_signature(self):
        block_2col = {
            "type": "table_row",
            "table_row": {"cells": [[self._rt("a")], [self._rt("b")]]},
        }
        block_3col = {
            "type": "table_row",
            "table_row": {"cells": [[self._rt("a")], [self._rt("b")], [self._rt("c")]]},
        }
        assert compute_signature(block_2col) != compute_signature(block_3col)

    def test_reordered_cells_different_signature(self):
        """["A","B"] and ["B","A"] must produce different signatures."""
        block_ab = {
            "type": "table_row",
            "table_row": {"cells": [[self._rt("A")], [self._rt("B")]]},
        }
        block_ba = {
            "type": "table_row",
            "table_row": {"cells": [[self._rt("B")], [self._rt("A")]]},
        }
        assert compute_signature(block_ab) != compute_signature(block_ba)

    def test_text_split_differently_across_cells_different_signature(self):
        """["ab",""] vs ["a","b"] must differ (boundary sentinel ensures this)."""
        block_ab_empty = {
            "type": "table_row",
            "table_row": {"cells": [[self._rt("ab")], [self._rt("")]]},
        }
        block_a_b = {
            "type": "table_row",
            "table_row": {"cells": [[self._rt("a")], [self._rt("b")]]},
        }
        assert compute_signature(block_ab_empty) != compute_signature(block_a_b)

    def test_fallback_to_text_content(self):
        """Cells using text.content instead of plain_text are handled."""
        block = {
            "type": "table_row",
            "table_row": {
                "cells": [[{"text": {"content": "via_content"}}]],
            },
        }
        segments = _normalize_table_row_cells(block)
        text_segs = [s for s in segments if "text" in s]
        assert text_segs[0]["text"] == "via_content"

    def test_href_in_cell_preserved(self):
        """Rich text with href inside a cell carries the href into segments."""
        block = {
            "table_row": {
                "cells": [[{"plain_text": "link", "href": "https://example.com"}]],
            }
        }
        segments = _normalize_table_row_cells(block)
        text_segs = [s for s in segments if "text" in s]
        assert text_segs[0]["href"] == "https://example.com"
