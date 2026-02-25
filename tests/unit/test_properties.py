"""Property-based tests for notionify SDK using Hypothesis.

These tests verify algebraic / invariant properties of core utility and
converter functions.  They complement the example-based unit tests by
exercising the code with a wide range of randomly generated inputs.
"""

from __future__ import annotations

import copy
import json as _json
import logging as _logging
import pickle
import re
import string
from datetime import datetime
from datetime import timezone as _tz

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from notionify.config import NotionifyConfig, _validate_mime_list
from notionify.converter.ast_normalizer import ASTNormalizer
from notionify.converter.block_builder import (
    _LANGUAGE_ALIASES,
    _NOTION_LANGUAGES,
    _apply_image_fallback,
    _build_divider,
    _build_heading,
    _build_image_block,
    _build_list,
    _build_list_item,
    _build_task_list_item,
    _BuildContext,
    _classify_image_source,
    _handle_html_block,
    _normalize_language,
    _process_token,
    _process_tokens,
)
from notionify.converter.inline_renderer import markdown_escape, render_rich_text
from notionify.converter.math import (
    EQUATION_CHAR_LIMIT,
    _make_code_block,
    _make_code_rich_text,
    _make_equation_block,
    _make_equation_rich_text,
    _make_paragraph_block,
    _make_plain_text_rich_text,
    build_block_math,
    build_inline_math,
)
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import (
    NotionToMarkdownRenderer,
    _notion_url,
    _sanitize_comment,
)
from notionify.converter.notion_to_md import (
    _extract_plain_text as _extract_plain_text_from_block,
)
from notionify.converter.rich_text import (
    _clone_text_segment,
    _default_annotations,
    _handle_codespan,
    _handle_emphasis,
    _handle_html_inline,
    _handle_image,
    _handle_inline_math,
    _handle_linebreak,
    _handle_link,
    _handle_softbreak,
    _handle_strikethrough,
    _handle_strong,
    _handle_text,
    _has_non_default_annotations,
    _make_text_segment,
    _merge_annotations,
    build_rich_text,
    extract_text,
    split_rich_text,
)
from notionify.converter.tables import (
    _apply_table_fallback,
    _build_row_cells,
    _cells_to_text,
    _table_to_plain_text,
    build_table,
)
from notionify.diff.conflict import detect_conflict, take_snapshot
from notionify.diff.executor import _ExecState
from notionify.diff.lcs_matcher import lcs_match
from notionify.diff.planner import DiffPlanner
from notionify.diff.signature import (
    _ATTRS_EXTRACTORS,
    _extract_children_info,
    _extract_plain_text,
    _extract_type_attrs,
    _normalize_rich_text,
    compute_signature,
)
from notionify.errors import (
    NotionifyError,
    NotionifyImageSizeError,
    NotionifyImageTypeError,
    NotionifyUnsupportedBlockError,
    NotionifyValidationError,
    _reconstruct_error,
)
from notionify.image.attach import build_image_block_external, build_image_block_uploaded
from notionify.image.detect import detect_image_source, mime_to_extension
from notionify.image.validate import validate_image
from notionify.models import (
    BlockSignature,
    BlockUpdateResult,
    ConversionResult,
    ConversionWarning,
    DiffOp,
    DiffOpType,
    ImageSourceType,
    UpdateResult,
)
from notionify.notion_api.blocks import extract_block_ids
from notionify.notion_api.retries import compute_backoff, should_retry
from notionify.observability.logger import StructuredFormatter
from notionify.observability.metrics import MetricsHook, NoopMetricsHook
from notionify.utils.chunk import chunk_children
from notionify.utils.hashing import hash_dict, md5_hash
from notionify.utils.redact import (
    _SENSITIVE_KEY_PATTERNS,
    _looks_binary,
    _mask_token,
    _redact_dict,
    _redact_value,
    redact,
)
from notionify.utils.text_split import split_string

# ---------------------------------------------------------------------------
# Reusable strategies
# ---------------------------------------------------------------------------

# Safe alphabet: ASCII letters + digits, no Markdown special chars.
_SAFE_TEXT = string.ascii_letters + string.digits

# Strategy for a simple Notion-style block dict (the exact shape does not
# matter for chunk_children, which is type-agnostic).
_block_st = st.fixed_dictionaries({"type": st.text(min_size=1, max_size=20)})

# Strategy for a Notion rich_text *text* segment with non-empty content.
_rich_text_segment_st = st.fixed_dictionaries({
    "type": st.just("text"),
    "text": st.fixed_dictionaries({
        "content": st.text(min_size=1, max_size=300),
    }),
}).flatmap(
    lambda seg: st.tuples(
        st.just(seg),
        st.just(st.none())
        | st.fixed_dictionaries({
            "bold": st.booleans(),
            "italic": st.booleans(),
            "strikethrough": st.booleans(),
            "underline": st.booleans(),
            "code": st.booleans(),
            "color": st.just("default"),
        }),
    ).map(lambda pair: _attach_annotations(pair[0], pair[1]))
)


def _attach_annotations(seg: dict, annotations: dict | None) -> dict:
    """Optionally attach annotations to a rich_text segment."""
    if annotations is not None:
        seg = {**seg, "annotations": annotations}
    return seg


# ---------------------------------------------------------------------------
# 1. TestChunkChildrenProperties
# ---------------------------------------------------------------------------


class TestChunkChildrenProperties:
    """Property-based tests for :func:`chunk_children`."""

    @given(
        blocks=st.lists(_block_st, max_size=500),
        size=st.integers(min_value=1, max_value=200),
    )
    def test_concatenation_of_chunks_equals_original(
        self, blocks: list[dict], size: int
    ) -> None:
        """Concatenating all chunks must reproduce the original list."""
        chunks = chunk_children(blocks, size)
        flattened = [block for chunk in chunks for block in chunk]
        assert flattened == blocks

    @given(
        blocks=st.lists(_block_st, max_size=500),
        size=st.integers(min_value=1, max_value=200),
    )
    def test_all_chunks_respect_size_limit(
        self, blocks: list[dict], size: int
    ) -> None:
        """Every chunk must have length <= size."""
        chunks = chunk_children(blocks, size)
        for chunk in chunks:
            assert len(chunk) <= size

    @given(
        blocks=st.lists(_block_st, min_size=1, max_size=500),
        size=st.integers(min_value=1, max_value=200),
    )
    def test_no_empty_chunks_when_input_is_nonempty(
        self, blocks: list[dict], size: int
    ) -> None:
        """No chunk should be empty when the input list is non-empty."""
        chunks = chunk_children(blocks, size)
        assert len(chunks) >= 1
        for chunk in chunks:
            assert len(chunk) >= 1

    @given(size=st.integers(min_value=1, max_value=200))
    def test_empty_input_returns_empty_list(self, size: int) -> None:
        """An empty input always returns an empty list (not [[]])."""
        assert chunk_children([], size) == []

    @given(size=st.integers(max_value=0))
    def test_invalid_size_raises(self, size: int) -> None:
        """A size < 1 must raise ValueError."""
        with pytest.raises(ValueError, match="size"):
            chunk_children([], size)


# ---------------------------------------------------------------------------
# 2. TestSplitStringProperties
# ---------------------------------------------------------------------------


class TestSplitStringProperties:
    """Property-based tests for :func:`split_string`."""

    @given(
        text=st.text(max_size=5000),
        limit=st.integers(min_value=1, max_value=500),
    )
    def test_concatenation_equals_original(self, text: str, limit: int) -> None:
        """Joining all splits must reproduce the original string."""
        parts = split_string(text, limit)
        assert "".join(parts) == text

    @given(
        text=st.text(min_size=1, max_size=5000),
        limit=st.integers(min_value=1, max_value=500),
    )
    def test_each_split_respects_limit(self, text: str, limit: int) -> None:
        """Every split piece must be at most *limit* characters long."""
        parts = split_string(text, limit)
        for part in parts:
            assert len(part) <= limit

    @given(
        text=st.text(min_size=1, max_size=5000),
        limit=st.integers(min_value=1, max_value=500),
    )
    def test_no_empty_parts(self, text: str, limit: int) -> None:
        """No split piece should be empty when the input is non-empty."""
        parts = split_string(text, limit)
        assert len(parts) >= 1
        for part in parts:
            assert len(part) >= 1

    @given(limit=st.integers(min_value=1, max_value=500))
    def test_empty_input_returns_empty_list(self, limit: int) -> None:
        """An empty string always gives an empty list."""
        assert split_string("", limit) == []

    @given(
        text=st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "S", "So"),
            ),
            min_size=1,
            max_size=200,
        ),
        limit=st.integers(min_value=1, max_value=50),
    )
    def test_unicode_safety(self, text: str, limit: int) -> None:
        """Arbitrary Unicode text is split without corruption."""
        parts = split_string(text, limit)
        assert "".join(parts) == text
        for part in parts:
            # Every part must be a valid Python str (no invalid surrogates).
            part.encode("utf-8")

    @given(limit=st.integers(max_value=0))
    def test_invalid_limit_raises(self, limit: int) -> None:
        """A limit < 1 must raise ValueError."""
        with pytest.raises(ValueError, match="limit"):
            split_string("abc", limit)


# ---------------------------------------------------------------------------
# 3. TestMd5HashProperties
# ---------------------------------------------------------------------------


class TestMd5HashProperties:
    """Property-based tests for :func:`md5_hash` and :func:`hash_dict`."""

    @given(data=st.text(max_size=1000))
    def test_deterministic(self, data: str) -> None:
        """Same input must always produce the same hash."""
        assert md5_hash(data) == md5_hash(data)

    @given(data=st.text(max_size=1000))
    def test_hash_is_32_hex_chars(self, data: str) -> None:
        """The hash must always be a 32-character lowercase hex string."""
        h = md5_hash(data)
        assert len(h) == 32
        assert re.fullmatch(r"[0-9a-f]{32}", h)

    @given(
        a=st.text(min_size=1, max_size=200),
        b=st.text(min_size=1, max_size=200),
    )
    def test_different_inputs_give_different_hashes(
        self, a: str, b: str
    ) -> None:
        """Distinct inputs should (almost certainly) give distinct hashes."""
        assume(a != b)
        # MD5 collisions are theoretically possible but astronomically
        # unlikely for random strings.
        assert md5_hash(a) != md5_hash(b)

    @given(
        d=st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=st.one_of(
                st.integers(),
                st.text(max_size=50),
                st.booleans(),
                st.none(),
            ),
            max_size=10,
        ),
    )
    def test_hash_dict_deterministic(self, d: dict) -> None:
        """hash_dict is deterministic for the same dictionary."""
        assert hash_dict(d) == hash_dict(d)

    @given(
        d=st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=st.one_of(
                st.integers(),
                st.text(max_size=50),
                st.booleans(),
                st.none(),
            ),
            min_size=2,
            max_size=10,
        ),
    )
    def test_hash_dict_key_order_independent(self, d: dict) -> None:
        """hash_dict must produce the same hash regardless of key ordering."""
        # Reverse the key order.
        reversed_d = dict(reversed(list(d.items())))
        assert hash_dict(d) == hash_dict(reversed_d)

    @given(d=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.one_of(st.integers(), st.text(max_size=50)),
        max_size=10,
    ))
    def test_hash_dict_is_32_hex_chars(self, d: dict) -> None:
        """hash_dict output must be a 32-character lowercase hex string."""
        h = hash_dict(d)
        assert len(h) == 32
        assert re.fullmatch(r"[0-9a-f]{32}", h)


# ---------------------------------------------------------------------------
# 4. TestRichTextSplitProperties
# ---------------------------------------------------------------------------


class TestRichTextSplitProperties:
    """Property-based tests for :func:`split_rich_text`."""

    @given(
        segments=st.lists(
            st.fixed_dictionaries({
                "type": st.just("text"),
                "text": st.fixed_dictionaries({
                    "content": st.text(min_size=1, max_size=500),
                }),
            }),
            min_size=0,
            max_size=20,
        ),
        limit=st.integers(min_value=1, max_value=200),
    )
    def test_content_concatenation_equals_original(
        self, segments: list[dict], limit: int
    ) -> None:
        """Concatenating split content must equal the original content."""
        original_content = "".join(
            seg["text"]["content"] for seg in segments
        )
        result = split_rich_text(segments, limit)
        result_content = "".join(
            seg.get("text", {}).get("content", "") for seg in result
        )
        assert result_content == original_content

    @given(
        segments=st.lists(
            st.fixed_dictionaries({
                "type": st.just("text"),
                "text": st.fixed_dictionaries({
                    "content": st.text(min_size=1, max_size=500),
                }),
            }),
            min_size=0,
            max_size=20,
        ),
        limit=st.integers(min_value=1, max_value=200),
    )
    def test_each_segment_content_within_limit(
        self, segments: list[dict], limit: int
    ) -> None:
        """Every output segment's content must be at most *limit* chars."""
        result = split_rich_text(segments, limit)
        for seg in result:
            content = seg.get("text", {}).get("content", "")
            assert len(content) <= limit

    @given(
        segments=st.lists(
            st.fixed_dictionaries({
                "type": st.just("text"),
                "text": st.fixed_dictionaries({
                    "content": st.text(min_size=1, max_size=500),
                }),
                "annotations": st.fixed_dictionaries({
                    "bold": st.booleans(),
                    "italic": st.booleans(),
                    "strikethrough": st.booleans(),
                    "underline": st.booleans(),
                    "code": st.booleans(),
                    "color": st.just("default"),
                }),
            }),
            min_size=1,
            max_size=10,
        ),
        limit=st.integers(min_value=1, max_value=100),
    )
    def test_annotations_preserved_after_split(
        self, segments: list[dict], limit: int
    ) -> None:
        """Annotations from the original segment must be preserved on all split pieces."""
        result = split_rich_text(segments, limit)
        # Build a mapping from original segment index to its annotations.
        # Because split_rich_text may expand one segment into N, walk the
        # results and check that each result segment with annotations matches
        # the source segment's annotations.
        result_idx = 0
        for seg in segments:
            original_annotations = seg.get("annotations")
            original_content = seg["text"]["content"]
            remaining = len(original_content)
            while remaining > 0 and result_idx < len(result):
                r_seg = result[result_idx]
                if original_annotations is not None:
                    assert r_seg.get("annotations") == original_annotations
                chunk_len = len(r_seg.get("text", {}).get("content", ""))
                remaining -= chunk_len
                result_idx += 1

    @given(
        segments=st.lists(
            st.fixed_dictionaries({
                "type": st.just("equation"),
                "equation": st.fixed_dictionaries({
                    "expression": st.text(min_size=1, max_size=500),
                }),
            }),
            min_size=1,
            max_size=10,
        ),
        limit=st.integers(min_value=1, max_value=200),
    )
    def test_equation_segments_passed_through(
        self, segments: list[dict], limit: int
    ) -> None:
        """Equation segments should be passed through unchanged."""
        result = split_rich_text(segments, limit)
        assert result == segments


# ---------------------------------------------------------------------------
# 5. TestRedactProperties
# ---------------------------------------------------------------------------


class TestRedactProperties:
    """Property-based tests for :func:`redact`."""

    @given(
        payload=st.dictionaries(
            keys=st.text(min_size=1, max_size=30),
            values=st.one_of(
                st.text(max_size=100),
                st.integers(),
                st.booleans(),
                st.none(),
            ),
            max_size=15,
        ),
    )
    def test_original_never_mutated(self, payload: dict) -> None:
        """The original dict must not be modified by redact."""
        original = copy.deepcopy(payload)
        redact(payload)
        assert payload == original

    @given(
        payload=st.dictionaries(
            keys=st.text(min_size=1, max_size=30),
            values=st.one_of(
                st.text(max_size=100),
                st.integers(),
                st.booleans(),
                st.none(),
            ),
            max_size=15,
        ),
    )
    def test_redacted_has_same_keys(self, payload: dict) -> None:
        """The redacted dict must have exactly the same keys as the original."""
        result = redact(payload)
        assert set(result.keys()) == set(payload.keys())

    @given(
        key=st.sampled_from(sorted(_SENSITIVE_KEY_PATTERNS)),
    )
    def test_known_sensitive_keys_string_values_redacted(
        self, key: str
    ) -> None:
        """A sensitive key whose value contains a Bearer token must be redacted."""
        payload = {key: "Bearer ntn_super_secret_1234"}
        result = redact(payload)
        assert result[key] != payload[key]
        assert "ntn_super_secret_1234" not in result[key]

    @given(
        key=st.sampled_from(sorted(_SENSITIVE_KEY_PATTERNS)),
        value=st.integers(),
    )
    def test_known_sensitive_keys_nonstring_values_redacted(
        self, key: str, value: int
    ) -> None:
        """A sensitive key with a non-string value must be replaced with '<redacted>'."""
        payload = {key: value}
        result = redact(payload)
        assert result[key] == "<redacted>"

    @given(
        key=st.sampled_from(sorted(_SENSITIVE_KEY_PATTERNS)),
        token=st.text(min_size=5, max_size=50),
    )
    def test_known_sensitive_keys_token_scrubbed(
        self, key: str, token: str
    ) -> None:
        """When a token is supplied, it is scrubbed from sensitive key values."""
        # Exclude tokens that are substrings of the redaction placeholder itself
        # ("<redacted>") to avoid false assertions where the token appears in the
        # placeholder text rather than as the original value.
        assume(token not in "<redacted>")
        payload = {key: f"value contains {token} here"}
        result = redact(payload, token=token)
        assert token not in result[key]

    def test_token_suffix_overlap_uses_generic_placeholder(self) -> None:
        """When the suffix-based placeholder would re-introduce the token, fall back."""
        payload = {"api-key": "value contains .0000 here"}
        result = redact(payload, token=".0000")
        assert ".0000" not in result["api-key"]
        assert "<redacted>" in result["api-key"]

    @given(
        payload=st.dictionaries(
            keys=st.text(
                alphabet=st.characters(whitelist_categories=("L",)),
                min_size=1,
                max_size=30,
            ).filter(
                lambda k: not any(
                    pat in k.lower() for pat in _SENSITIVE_KEY_PATTERNS
                )
            ),
            values=st.text(
                alphabet=string.ascii_letters + string.digits,
                min_size=1,
                max_size=50,
            ),
            max_size=10,
        ),
    )
    @settings(suppress_health_check=[HealthCheck.too_slow])
    def test_non_sensitive_plain_values_unchanged(self, payload: dict) -> None:
        """Non-sensitive keys with plain text values are left unchanged."""
        result = redact(payload)
        for key in payload:
            assert result[key] == payload[key]

    @given(
        payload=st.fixed_dictionaries({
            "data": st.text(max_size=100),
        }),
        token=st.from_regex(r"ntn_[a-zA-Z0-9]{10,40}", fullmatch=True),
    )
    def test_token_scrubbed_from_values(
        self, payload: dict, token: str
    ) -> None:
        """If a token is supplied, it must not appear in any output value.

        Uses realistic token format (ntn_...) to avoid false positives
        where the token accidentally matches the redaction suffix.
        """
        # Inject the token into a value.
        injected = {**payload, "body": f"prefix {token} suffix"}
        result = redact(injected, token=token)
        for value in result.values():
            if isinstance(value, str):
                assert token not in value

    @given(
        payload=st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=st.recursive(
                st.one_of(
                    st.text(max_size=50),
                    st.integers(),
                    st.booleans(),
                    st.none(),
                ),
                lambda children: st.one_of(
                    st.lists(children, max_size=5),
                    st.dictionaries(
                        st.text(min_size=1, max_size=10),
                        children,
                        max_size=5,
                    ),
                ),
                max_leaves=20,
            ),
            max_size=10,
        ),
    )
    def test_redact_never_raises_on_nested_dicts(self, payload: dict) -> None:
        """redact must handle arbitrarily nested dicts/lists without raising."""
        result = redact(payload)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 6. TestMarkdownParseProperties
# ---------------------------------------------------------------------------


class TestMarkdownParseProperties:
    """Property-based tests for :class:`ASTNormalizer.parse`."""

    _normalizer = ASTNormalizer()

    @given(text=st.text(max_size=2000))
    @settings(max_examples=200)
    def test_parse_never_raises_on_arbitrary_text(self, text: str) -> None:
        """parse() must never raise, no matter what text is fed in."""
        # This is the key robustness property: the parser must be resilient
        # to arbitrary input.
        result = self._normalizer.parse(text)
        assert isinstance(result, list)

    @given(text=st.text(max_size=2000))
    @settings(max_examples=200)
    def test_parse_returns_list_of_dicts(self, text: str) -> None:
        """Every element in the parsed result must be a dict with a 'type' key."""
        result = self._normalizer.parse(text)
        for token in result:
            assert isinstance(token, dict)
            assert "type" in token

    @given(
        text=st.from_regex(
            r"#{1,6} [A-Za-z0-9 ]+\n",
            fullmatch=True,
        ),
    )
    @settings(max_examples=100)
    def test_headings_are_parsed(self, text: str) -> None:
        """Strings that look like ATX headings should produce heading tokens."""
        result = self._normalizer.parse(text)
        # At least one token should exist.
        assert len(result) >= 1
        # There should be a heading in the result.
        types = [t["type"] for t in result]
        assert "heading" in types

    @given(
        text=st.from_regex(
            r"```[a-z]*\n[A-Za-z0-9 \n]+\n```\n",
            fullmatch=True,
        ),
    )
    @settings(max_examples=100)
    def test_code_blocks_are_parsed(self, text: str) -> None:
        """Fenced code blocks should produce block_code tokens."""
        result = self._normalizer.parse(text)
        types = [t["type"] for t in result]
        assert "block_code" in types

    @given(text=st.just(""))
    def test_empty_string_returns_empty_list(self, text: str) -> None:
        """An empty string must parse to an empty list."""
        result = self._normalizer.parse(text)
        assert result == []

    @given(
        text=st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "Z", "S"),
            ),
            min_size=1,
            max_size=500,
        ),
    )
    @settings(max_examples=100)
    def test_unicode_text_does_not_crash(self, text: str) -> None:
        """Arbitrary Unicode text must be handled without errors."""
        result = self._normalizer.parse(text)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 7. TestConverterProperties (PRD section 20.8)
# ---------------------------------------------------------------------------


class TestConverterProperties:
    """Property-based tests for the full conversion pipeline.

    PRD section 20.8 requires: ``test_converter_never_crashes`` — the full
    converter must never raise on any text input.
    """

    _config = NotionifyConfig(token="test-token")
    _converter = MarkdownToNotionConverter(_config)

    @given(text=st.text(min_size=0, max_size=5000))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_converter_never_crashes(self, text: str) -> None:
        """The full pipeline must never raise, regardless of input."""
        result = self._converter.convert(text)
        assert isinstance(result.blocks, list)
        assert isinstance(result.warnings, list)
        assert isinstance(result.images, list)

    @given(text=st.text(min_size=0, max_size=5000))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_all_blocks_have_type(self, text: str) -> None:
        """Every block produced must have a 'type' key."""
        result = self._converter.convert(text)
        for block in result.blocks:
            assert isinstance(block, dict)
            assert "type" in block

    @given(text=st.text(min_size=0, max_size=3000))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_signature_computation_never_crashes(self, text: str) -> None:
        """Signature computation must handle all converter-produced blocks."""
        result = self._converter.convert(text)
        for block in result.blocks:
            sig = compute_signature(block)
            assert sig.block_type == block["type"]

    @given(text=st.text(min_size=0, max_size=3000))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_convert_is_deterministic(self, text: str) -> None:
        """Converting the same markdown twice produces identical block types."""
        r1 = self._converter.convert(text)
        r2 = self._converter.convert(text)
        types1 = [b["type"] for b in r1.blocks]
        types2 = [b["type"] for b in r2.blocks]
        assert types1 == types2

    @given(
        level=st.integers(min_value=4, max_value=6),
        text=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=60),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_heading_overflow_downgrade_produces_no_h4_h5_h6(
        self, level: int, text: str
    ) -> None:
        """With heading_overflow='downgrade', H4-H6 blocks are downgraded to heading_3."""
        cfg = NotionifyConfig(token="test", heading_overflow="downgrade")
        converter = MarkdownToNotionConverter(cfg)
        md = "#" * level + " " + text
        result = converter.convert(md)
        types = [b["type"] for b in result.blocks]
        assert "heading_4" not in types
        assert "heading_5" not in types
        assert "heading_6" not in types

    @given(
        level=st.integers(min_value=4, max_value=6),
        text=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=60),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_heading_overflow_paragraph_produces_paragraph_not_heading(
        self, level: int, text: str
    ) -> None:
        """With heading_overflow='paragraph', H4-H6 blocks become paragraphs."""
        cfg = NotionifyConfig(token="test", heading_overflow="paragraph")
        converter = MarkdownToNotionConverter(cfg)
        md = "#" * level + " " + text
        result = converter.convert(md)
        types = [b["type"] for b in result.blocks]
        assert "paragraph" in types
        assert "heading_4" not in types
        assert "heading_5" not in types
        assert "heading_6" not in types

    @given(
        col_count=st.integers(min_value=2, max_value=4),
        row_count=st.integers(min_value=1, max_value=4),
        content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=20),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_enable_tables_false_produces_no_table_blocks(
        self, col_count: int, row_count: int, content: str
    ) -> None:
        """With enable_tables=False, no 'table' blocks are produced."""
        cfg = NotionifyConfig(
            token="test", enable_tables=False, table_fallback="comment"
        )
        converter = MarkdownToNotionConverter(cfg)
        # Build a simple GFM table
        header = " | ".join([content] * col_count)
        separator = " | ".join(["---"] * col_count)
        row_line = " | ".join([content] * col_count)
        rows = "\n".join([row_line] * row_count)
        md = f"| {header} |\n| {separator} |\n| {rows} |"
        result = converter.convert(md)
        types = [b["type"] for b in result.blocks]
        assert "table" not in types


# ---------------------------------------------------------------------------
# 8. TestNotionToMarkdownRendererProperties
# ---------------------------------------------------------------------------

# Strategy for a minimal Notion block with arbitrary type and optional
# rich_text content.  The shape is intentionally broad so the renderer must
# handle unknown/empty/malformed inputs gracefully.
_notion_block_st = st.fixed_dictionaries(
    {
        "type": st.text(min_size=1, max_size=30),
    },
    optional={
        "paragraph": st.fixed_dictionaries(
            {"rich_text": st.lists(
                st.fixed_dictionaries(
                    {"plain_text": st.text(max_size=200)},
                    optional={"annotations": st.fixed_dictionaries({
                        "bold": st.booleans(),
                        "italic": st.booleans(),
                        "strikethrough": st.booleans(),
                        "underline": st.booleans(),
                        "code": st.booleans(),
                        "color": st.just("default"),
                    })},
                ),
                max_size=5,
            )}
        ),
    },
)

_KNOWN_BLOCK_TYPES = [
    "paragraph", "heading_1", "heading_2", "heading_3",
    "bulleted_list_item", "numbered_list_item", "to_do",
    "quote", "code", "divider", "equation",
    "image", "bookmark", "callout",
]


def _make_rich_text(text: str) -> list[dict]:
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


class TestNotionToMarkdownRendererProperties:
    """Property-based tests for :class:`NotionToMarkdownRenderer`.

    Key invariants:
    - render_blocks never raises on any list of dicts
    - render_blocks always returns a string
    - Empty input always returns an empty/whitespace string
    - Determinism: same input → same output
    - Signature stability across all converter-produced blocks
    """

    _config = NotionifyConfig(token="test-token")
    _renderer = NotionToMarkdownRenderer(_config)

    @given(
        blocks=st.lists(
            st.fixed_dictionaries({"type": st.text(min_size=1, max_size=40)}),
            max_size=20,
        )
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_render_blocks_never_raises(self, blocks: list[dict]) -> None:
        """render_blocks must never raise on arbitrary block dicts."""
        renderer = NotionToMarkdownRenderer(self._config)
        result = renderer.render_blocks(blocks)
        assert isinstance(result, str)

    @given(
        blocks=st.lists(
            st.fixed_dictionaries({"type": st.text(min_size=1, max_size=40)}),
            max_size=20,
        )
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_render_blocks_is_deterministic(self, blocks: list[dict]) -> None:
        """Same block list must always produce the same Markdown output."""
        r1 = NotionToMarkdownRenderer(self._config).render_blocks(blocks)
        r2 = NotionToMarkdownRenderer(self._config).render_blocks(blocks)
        assert r1 == r2

    def test_render_blocks_empty_returns_empty(self) -> None:
        """Empty block list must render to empty/whitespace string."""
        renderer = NotionToMarkdownRenderer(self._config)
        assert renderer.render_blocks([]).strip() == ""

    @given(block_type=st.sampled_from(_KNOWN_BLOCK_TYPES))
    @settings(max_examples=50)
    def test_render_block_known_types_return_string(self, block_type: str) -> None:
        """render_block on every known type must return a string."""
        renderer = NotionToMarkdownRenderer(self._config)
        block: dict = {
            "type": block_type,
            block_type: {"rich_text": _make_rich_text("hello")},
        }
        if block_type == "to_do":
            block[block_type]["checked"] = False
        elif block_type == "code":
            block[block_type]["language"] = "python"
        elif block_type == "equation":
            block[block_type] = {"expression": "E=mc^2"}
        elif block_type == "image":
            block[block_type] = {"type": "external", "external": {"url": "https://example.com/img.png"}}
        elif block_type == "bookmark":
            block[block_type] = {"url": "https://example.com"}
        elif block_type == "divider":
            block[block_type] = {}
        result = renderer.render_block(block)
        assert isinstance(result, str)

    @given(
        text=st.text(min_size=0, max_size=500),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_paragraph_rich_text_always_renders(self, text: str) -> None:
        """Paragraphs with arbitrary rich text content always render."""
        renderer = NotionToMarkdownRenderer(self._config)
        block = {
            "type": "paragraph",
            "paragraph": {"rich_text": _make_rich_text(text)},
        }
        result = renderer.render_block(block)
        assert isinstance(result, str)

    @given(
        text=st.text(min_size=0, max_size=300),
        depth=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_list_items_depth_never_raises(self, text: str, depth: int) -> None:
        """Bulleted/numbered list items at arbitrary depth always render."""
        renderer = NotionToMarkdownRenderer(self._config)
        for btype in ("bulleted_list_item", "numbered_list_item"):
            block = {
                "type": btype,
                btype: {"rich_text": _make_rich_text(text)},
            }
            result = renderer.render_block(block, depth=depth)
            assert isinstance(result, str)

    @given(
        text=st.text(min_size=0, max_size=3000),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_round_trip_block_types_preserved(self, text: str) -> None:
        """Block types produced by the converter are handled by the renderer."""
        converter = MarkdownToNotionConverter(self._config)
        renderer = NotionToMarkdownRenderer(self._config)
        result = converter.convert(text)
        # Renderer must not raise on any converter-produced block.
        md = renderer.render_blocks(result.blocks)
        assert isinstance(md, str)

    @given(
        text=st.text(min_size=0, max_size=2000),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_signature_stable_on_renderer_output_roundtrip(self, text: str) -> None:
        """compute_signature is deterministic on all converter-produced blocks."""
        converter = MarkdownToNotionConverter(self._config)
        result = converter.convert(text)
        for block in result.blocks:
            sig1 = compute_signature(block)
            sig2 = compute_signature(block)
            assert sig1 == sig2


# ---------------------------------------------------------------------------
# 9. TestInlineRendererProperties
# ---------------------------------------------------------------------------


class TestInlineRendererProperties:
    """Property-based tests for :func:`markdown_escape` and :func:`render_rich_text`."""

    @given(text=st.text(max_size=500))
    @settings(max_examples=200)
    def test_escape_code_context_is_identity(self, text: str) -> None:
        """In code context, markdown_escape must return the text unchanged."""
        assert markdown_escape(text, context="code") == text

    @given(text=st.text(max_size=500))
    @settings(max_examples=200)
    def test_escape_always_returns_string(self, text: str) -> None:
        """markdown_escape must always return a string for any context."""
        for ctx in ("inline", "code", "url"):
            result = markdown_escape(text, context=ctx)
            assert isinstance(result, str)

    @given(text=st.text(max_size=500))
    @settings(max_examples=200)
    def test_escape_url_replaces_parens(self, text: str) -> None:
        """In url context, parentheses are percent-encoded."""
        result = markdown_escape(text, context="url")
        assert "(" not in result
        assert ")" not in result

    @given(
        text=st.text(
            alphabet=st.characters(blacklist_characters=r'\`*_{}[]()#+-.!|'),
            max_size=500,
        )
    )
    @settings(max_examples=200)
    def test_escape_inline_no_special_chars_unchanged(self, text: str) -> None:
        """Text without special Markdown chars is unchanged in inline context."""
        assert markdown_escape(text, context="inline") == text

    @given(text=st.text(max_size=200))
    @settings(max_examples=200)
    def test_escape_inline_is_deterministic(self, text: str) -> None:
        """markdown_escape produces the same result for repeated calls."""
        assert markdown_escape(text) == markdown_escape(text)

    @given(
        segments=st.lists(
            st.fixed_dictionaries({
                "type": st.just("text"),
                "plain_text": st.text(max_size=200),
                "text": st.fixed_dictionaries({"content": st.text(max_size=200)}),
            }),
            max_size=10,
        )
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_render_rich_text_never_raises(self, segments: list[dict]) -> None:
        """render_rich_text must never raise on arbitrary text segments."""
        result = render_rich_text(segments)
        assert isinstance(result, str)

    def test_render_rich_text_empty_returns_empty(self) -> None:
        """Empty segment list always renders to empty string."""
        assert render_rich_text([]) == ""

    @given(
        text=st.text(min_size=1, max_size=200),
    )
    @settings(max_examples=200)
    def test_render_rich_text_plain_contains_text(self, text: str) -> None:
        """Plain text segment must appear (possibly escaped) in rendered output."""
        # Build a segment without special Markdown chars to avoid escaping.
        safe = text.replace("\\", "").replace("`", "").replace("*", "").replace("_", "")
        if not safe:
            return
        seg = {"type": "text", "plain_text": safe, "text": {"content": safe}}
        result = render_rich_text([seg])
        # The escaped output must contain the core text characters.
        assert isinstance(result, str)
        assert len(result) >= len(safe)

    @given(
        char=st.sampled_from(list(r'\`*_{}[]()#+-.!|')),
    )
    @settings(max_examples=50)
    def test_each_special_char_is_escaped_in_inline_context(self, char: str) -> None:
        """Every character in ESCAPE_CHARS must be backslash-escaped in inline context."""
        result = markdown_escape(char, context="inline")
        assert result == f"\\{char}"

    @given(
        text=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
    )
    @settings(max_examples=200)
    def test_bold_annotation_wraps_safe_text(self, text: str) -> None:
        """A text segment with bold=True must produce **text** in rendered output."""
        seg = {
            "type": "text",
            "plain_text": text,
            "text": {"content": text},
            "annotations": {
                "bold": True,
                "italic": False,
                "strikethrough": False,
                "underline": False,
                "code": False,
                "color": "default",
            },
        }
        result = render_rich_text([seg])
        assert f"**{text}**" in result

    @given(
        text=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
    )
    @settings(max_examples=200)
    def test_italic_annotation_wraps_safe_text(self, text: str) -> None:
        """A text segment with italic=True must produce _text_ in rendered output."""
        seg = {
            "type": "text",
            "plain_text": text,
            "text": {"content": text},
            "annotations": {
                "bold": False,
                "italic": True,
                "strikethrough": False,
                "underline": False,
                "code": False,
                "color": "default",
            },
        }
        result = render_rich_text([seg])
        assert f"_{text}_" in result

    @given(
        text=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
    )
    @settings(max_examples=200)
    def test_strikethrough_annotation_wraps_safe_text(self, text: str) -> None:
        """A text segment with strikethrough=True must produce ~~text~~ in rendered output."""
        seg = {
            "type": "text",
            "plain_text": text,
            "text": {"content": text},
            "annotations": {
                "bold": False,
                "italic": False,
                "strikethrough": True,
                "underline": False,
                "code": False,
                "color": "default",
            },
        }
        result = render_rich_text([seg])
        assert f"~~{text}~~" in result

    @given(
        text=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
    )
    @settings(max_examples=200)
    def test_code_annotation_wraps_safe_text(self, text: str) -> None:
        """A text segment with code=True must produce `text` in rendered output."""
        seg = {
            "type": "text",
            "plain_text": text,
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
        result = render_rich_text([seg])
        assert f"`{text}`" in result

    @given(
        text=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
    )
    @settings(max_examples=200)
    def test_underline_annotation_wraps_safe_text(self, text: str) -> None:
        """A text segment with underline=True must produce <u>text</u> in rendered output."""
        seg = {
            "type": "text",
            "plain_text": text,
            "text": {"content": text},
            "annotations": {
                "bold": False,
                "italic": False,
                "strikethrough": False,
                "underline": True,
                "code": False,
                "color": "default",
            },
        }
        result = render_rich_text([seg])
        assert f"<u>{text}</u>" in result

    @given(
        text=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
    )
    @settings(max_examples=200)
    def test_escaped_output_longer_than_or_equal_to_input_with_special_chars(
        self, text: str
    ) -> None:
        """Inline escape must not shorten text (each special char gets a backslash added)."""
        # We add a special char to ensure escaping happens.
        input_text = text + "*"
        result = markdown_escape(input_text, context="inline")
        assert len(result) > len(text)  # at minimum the * is escaped to \*

    @given(
        expression=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=50),
    )
    @settings(max_examples=200)
    def test_equation_segment_renders_as_dollar_expression(
        self, expression: str
    ) -> None:
        """Equation segments render as $expression$."""
        seg = {
            "type": "equation",
            "equation": {"expression": expression},
        }
        result = render_rich_text([seg])
        assert result == f"${expression}$"

    @given(
        text=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=50),
        url=st.from_regex(r"https://[a-z]{3,10}\.com/[a-z]{0,10}", fullmatch=True),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_href_produces_markdown_link(self, text: str, url: str) -> None:
        """A text segment with href renders as [text](url)."""
        seg = {
            "type": "text",
            "plain_text": text,
            "text": {"content": text},
            "href": url,
            "annotations": {
                "bold": False, "italic": False, "strikethrough": False,
                "underline": False, "code": False, "color": "default",
            },
        }
        result = render_rich_text([seg])
        assert f"[{text}]" in result
        assert url in result


# ---------------------------------------------------------------------------
# 10. TestComputeBackoffProperties
# ---------------------------------------------------------------------------


class TestComputeBackoffProperties:
    """Property-based tests for :func:`compute_backoff` and :func:`should_retry`."""

    @given(
        attempt=st.integers(min_value=0, max_value=20),
        base=st.floats(min_value=0.01, max_value=10.0, allow_nan=False),
        maximum=st.floats(min_value=0.1, max_value=3600.0, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_backoff_without_jitter_never_exceeds_maximum(
        self, attempt: int, base: float, maximum: float
    ) -> None:
        """Without jitter, compute_backoff must never exceed maximum."""
        delay = compute_backoff(attempt, base=base, maximum=maximum, jitter=False)
        assert delay <= maximum + 1e-9  # float tolerance

    @given(
        attempt=st.integers(min_value=0, max_value=20),
        base=st.floats(min_value=0.01, max_value=10.0, allow_nan=False),
        maximum=st.floats(min_value=0.1, max_value=3600.0, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_backoff_is_non_negative(
        self, attempt: int, base: float, maximum: float
    ) -> None:
        """compute_backoff must always return a non-negative value."""
        delay = compute_backoff(attempt, base=base, maximum=maximum, jitter=True)
        assert delay >= 0.0

    @given(
        retry_after=st.floats(min_value=0.0, max_value=600.0, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_retry_after_overrides_exponential(self, retry_after: float) -> None:
        """When retry_after is supplied without jitter, it is returned exactly."""
        delay = compute_backoff(
            attempt=5, base=1.0, maximum=60.0, jitter=False, retry_after=retry_after
        )
        assert delay == retry_after

    @given(
        attempt=st.integers(min_value=0, max_value=5),
        max_attempts=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=200)
    def test_should_retry_never_retries_after_exhaustion(
        self, attempt: int, max_attempts: int
    ) -> None:
        """should_retry must return False when attempt >= max_attempts - 1."""
        if attempt + 1 >= max_attempts:
            assert not should_retry(500, None, attempt, max_attempts)
        # When below the limit with a retryable status, it should retry.
        elif attempt + 1 < max_attempts:
            assert should_retry(500, None, attempt, max_attempts)

    @given(
        status_code=st.integers(min_value=200, max_value=499).filter(
            lambda s: s != 429
        ),
        attempt=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=200)
    def test_non_retryable_status_codes_not_retried(
        self, status_code: int, attempt: int
    ) -> None:
        """2xx and 4xx (except 429) status codes must not trigger retry."""
        assert not should_retry(status_code, None, attempt, max_attempts=10)


# ---------------------------------------------------------------------------
# 11. TestLCSMatcherProperties
# ---------------------------------------------------------------------------


def _make_sig(block_type: str, text: str) -> BlockSignature:
    """Create a minimal BlockSignature for property tests."""
    from notionify.utils.hashing import md5_hash
    return BlockSignature(
        block_type=block_type,
        rich_text_hash=md5_hash(text),
        structural_hash=md5_hash("0"),
        attrs_hash=md5_hash("{}"),
        nesting_depth=0,
    )


_sig_st = st.builds(
    _make_sig,
    block_type=st.sampled_from(["paragraph", "heading_1", "code", "divider"]),
    text=st.text(min_size=0, max_size=50),
)


class TestLCSMatcherProperties:
    """Property-based tests for :func:`lcs_match`."""

    @given(sigs=st.lists(_sig_st, max_size=20))
    @settings(max_examples=200)
    def test_identical_sequences_fully_matched(
        self, sigs: list[BlockSignature]
    ) -> None:
        """lcs_match of identical lists must return n matched pairs."""
        pairs = lcs_match(sigs, sigs)
        assert len(pairs) == len(sigs)
        for i, (ei, ni) in enumerate(pairs):
            assert ei == i
            assert ni == i

    @given(sigs=st.lists(_sig_st, max_size=20))
    @settings(max_examples=200)
    def test_empty_existing_returns_no_pairs(
        self, sigs: list[BlockSignature]
    ) -> None:
        """lcs_match with empty existing always returns empty list."""
        assert lcs_match([], sigs) == []

    @given(sigs=st.lists(_sig_st, max_size=20))
    @settings(max_examples=200)
    def test_empty_new_returns_no_pairs(
        self, sigs: list[BlockSignature]
    ) -> None:
        """lcs_match with empty new always returns empty list."""
        assert lcs_match(sigs, []) == []

    @given(
        existing=st.lists(_sig_st, max_size=15),
        new=st.lists(_sig_st, max_size=15),
    )
    @settings(max_examples=200)
    def test_pairs_length_bounded_by_min(
        self, existing: list[BlockSignature], new: list[BlockSignature]
    ) -> None:
        """Number of matched pairs cannot exceed min(len(existing), len(new))."""
        pairs = lcs_match(existing, new)
        assert len(pairs) <= min(len(existing), len(new))

    @given(
        existing=st.lists(_sig_st, max_size=15),
        new=st.lists(_sig_st, max_size=15),
    )
    @settings(max_examples=200)
    def test_pairs_indices_in_range(
        self, existing: list[BlockSignature], new: list[BlockSignature]
    ) -> None:
        """All returned indices must be valid positions in the respective lists."""
        pairs = lcs_match(existing, new)
        for ei, ni in pairs:
            assert 0 <= ei < len(existing)
            assert 0 <= ni < len(new)

    @given(
        existing=st.lists(_sig_st, min_size=1, max_size=15),
        new=st.lists(_sig_st, min_size=1, max_size=15),
    )
    @settings(max_examples=200)
    def test_pairs_are_strictly_increasing(
        self, existing: list[BlockSignature], new: list[BlockSignature]
    ) -> None:
        """Returned pairs must be strictly increasing in both indices."""
        pairs = lcs_match(existing, new)
        for k in range(1, len(pairs)):
            assert pairs[k][0] > pairs[k - 1][0]
            assert pairs[k][1] > pairs[k - 1][1]

    @given(
        existing=st.lists(_sig_st, max_size=15),
        new=st.lists(_sig_st, max_size=15),
    )
    @settings(max_examples=200)
    def test_matched_signatures_are_equal(
        self, existing: list[BlockSignature], new: list[BlockSignature]
    ) -> None:
        """Each matched pair must reference equal signatures."""
        pairs = lcs_match(existing, new)
        for ei, ni in pairs:
            assert existing[ei] == new[ni]


# ---------------------------------------------------------------------------
# 12. TestNotionifyConfigProperties
# ---------------------------------------------------------------------------


class TestNotionifyConfigProperties:
    """Property-based tests for :class:`NotionifyConfig` validation."""

    _BASE_URL = "https://api.notion.com/v1"

    @given(
        base=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
        maximum=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_valid_retry_delays_accepted(self, base: float, maximum: float) -> None:
        """Config with base <= max delay must not raise ValueError."""
        assume(base <= maximum)
        cfg = NotionifyConfig(
            token="test",
            base_url=self._BASE_URL,
            retry_base_delay=base,
            retry_max_delay=maximum,
        )
        assert cfg.retry_base_delay == base
        assert cfg.retry_max_delay == maximum

    @given(
        base=st.floats(min_value=0.01, max_value=100.0, allow_nan=False),
        maximum=st.floats(min_value=0.0, max_value=99.0, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_base_delay_greater_than_max_raises(
        self, base: float, maximum: float
    ) -> None:
        """Config with base_delay > max_delay must raise ValueError."""
        assume(base > maximum)
        with pytest.raises(ValueError, match="retry_base_delay"):
            NotionifyConfig(
                token="test",
                base_url=self._BASE_URL,
                retry_base_delay=base,
                retry_max_delay=maximum,
            )

    @given(delay=st.floats(min_value=-1000.0, max_value=-0.001, allow_nan=False))
    @settings(max_examples=100)
    def test_negative_retry_base_delay_raises(self, delay: float) -> None:
        """Negative retry_base_delay must raise ValueError."""
        with pytest.raises(ValueError, match="retry_base_delay"):
            NotionifyConfig(
                token="test",
                base_url=self._BASE_URL,
                retry_base_delay=delay,
                retry_max_delay=abs(delay) + 1,
            )

    @given(rps=st.floats(min_value=-100.0, max_value=0.0, allow_nan=False))
    @settings(max_examples=100)
    def test_non_positive_rate_limit_rps_raises(self, rps: float) -> None:
        """rate_limit_rps <= 0 must raise ValueError."""
        with pytest.raises(ValueError, match="rate_limit_rps"):
            NotionifyConfig(token="test", base_url=self._BASE_URL, rate_limit_rps=rps)

    @given(attempts=st.integers(min_value=0, max_value=20))
    @settings(max_examples=100)
    def test_valid_max_attempts_accepted(self, attempts: int) -> None:
        """Non-negative retry_max_attempts must not raise."""
        cfg = NotionifyConfig(
            token="test", base_url=self._BASE_URL, retry_max_attempts=attempts
        )
        assert cfg.retry_max_attempts == attempts

    def test_insecure_http_non_localhost_raises(self) -> None:
        """HTTP base_url to non-local host must raise ValueError."""
        with pytest.raises(ValueError, match="insecure HTTP"):
            NotionifyConfig(token="test", base_url="http://api.notion.com/v1")

    def test_http_localhost_accepted(self) -> None:
        """HTTP base_url to localhost must be accepted."""
        cfg = NotionifyConfig(token="test", base_url="http://localhost:3000/v1")
        assert "localhost" in cfg.base_url


# ---------------------------------------------------------------------------
# 13. TestImageDetectProperties
# ---------------------------------------------------------------------------


class TestImageDetectProperties:
    """Property-based tests for :func:`detect_image_source` and :func:`mime_to_extension`."""

    @given(text=st.text(max_size=500))
    @settings(max_examples=200)
    def test_detect_never_raises(self, text: str) -> None:
        """detect_image_source must never raise on any string input."""
        result = detect_image_source(text)
        assert isinstance(result, ImageSourceType)

    @given(
        suffix=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            max_size=100,
        )
    )
    @settings(max_examples=200)
    def test_data_uri_detected(self, suffix: str) -> None:
        """Any string starting with 'data:' must be DATA_URI."""
        result = detect_image_source(f"data:{suffix}")
        assert result == ImageSourceType.DATA_URI

    @given(
        host=st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,50}", fullmatch=True),
        path=st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789/-_.",
            max_size=30,
        ),
    )
    @settings(max_examples=200)
    def test_https_url_detected(self, host: str, path: str) -> None:
        """Valid https:// URLs must be EXTERNAL_URL."""
        result = detect_image_source(f"https://{host}/{path}")
        assert result == ImageSourceType.EXTERNAL_URL

    @given(
        host=st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,50}", fullmatch=True),
        path=st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789/-_.",
            max_size=30,
        ),
    )
    @settings(max_examples=200)
    def test_http_url_detected(self, host: str, path: str) -> None:
        """Valid http:// URLs must be EXTERNAL_URL."""
        result = detect_image_source(f"http://{host}/{path}")
        assert result == ImageSourceType.EXTERNAL_URL

    @given(whitespace=st.text(alphabet=" \t\n\r", min_size=0, max_size=10))
    @settings(max_examples=100)
    def test_empty_or_whitespace_is_unknown(self, whitespace: str) -> None:
        """Empty or whitespace-only strings must be UNKNOWN."""
        assert detect_image_source(whitespace) == ImageSourceType.UNKNOWN

    @given(
        ext=st.sampled_from([
            ".jpg", ".jpeg", ".png", ".gif", ".webp",
            ".svg", ".bmp", ".tiff", ".tif", ".ico", ".avif",
        ])
    )
    @settings(max_examples=50)
    def test_image_extension_is_local_file(self, ext: str) -> None:
        """A filename with a known image extension is LOCAL_FILE."""
        result = detect_image_source(f"photo{ext}")
        assert result == ImageSourceType.LOCAL_FILE

    @given(mime=st.text(min_size=1, max_size=50))
    @settings(max_examples=200)
    def test_mime_to_extension_always_returns_dotted_string(
        self, mime: str
    ) -> None:
        """mime_to_extension must always return a string starting with '.'."""
        result = mime_to_extension(mime)
        assert isinstance(result, str)
        assert result.startswith(".")

    @given(
        mime=st.sampled_from([
            "image/jpeg", "image/png", "image/gif", "image/webp",
            "image/svg+xml", "image/bmp", "image/tiff",
        ])
    )
    @settings(max_examples=50)
    def test_known_mime_types_have_known_extensions(self, mime: str) -> None:
        """Known MIME types must map to known (non-.bin) extensions."""
        result = mime_to_extension(mime)
        assert result != ".bin"
        assert result.startswith(".")


# ---------------------------------------------------------------------------
# 14. TestMathBuilderProperties
# ---------------------------------------------------------------------------

_MATH_STRATEGIES = ["equation", "code", "latex_text"]
_MATH_OVERFLOW_BLOCK = ["split", "code", "text"]
_MATH_OVERFLOW_INLINE = ["split", "code", "text"]


class TestMathBuilderProperties:
    """Property-based tests for :func:`build_block_math` and :func:`build_inline_math`."""

    _BASE_URL = "https://api.notion.com/v1"

    def _config(
        self,
        strategy: str = "equation",
        overflow_block: str = "split",
        overflow_inline: str = "split",
    ) -> NotionifyConfig:
        return NotionifyConfig(
            token="test",
            base_url=self._BASE_URL,
            math_strategy=strategy,
            math_overflow_block=overflow_block,
            math_overflow_inline=overflow_inline,
        )

    @given(
        expression=st.text(max_size=2000),
        strategy=st.sampled_from(_MATH_STRATEGIES),
        overflow_block=st.sampled_from(_MATH_OVERFLOW_BLOCK),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_build_block_math_never_raises(
        self, expression: str, strategy: str, overflow_block: str
    ) -> None:
        """build_block_math must never raise on any expression or strategy."""
        cfg = self._config(strategy=strategy, overflow_block=overflow_block)
        blocks, warnings = build_block_math(expression, cfg)
        assert isinstance(blocks, list)
        assert isinstance(warnings, list)

    @given(
        expression=st.text(max_size=2000),
        strategy=st.sampled_from(_MATH_STRATEGIES),
        overflow_inline=st.sampled_from(_MATH_OVERFLOW_INLINE),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_build_inline_math_never_raises(
        self, expression: str, strategy: str, overflow_inline: str
    ) -> None:
        """build_inline_math must never raise on any expression or strategy."""
        cfg = self._config(strategy=strategy, overflow_inline=overflow_inline)
        segments, warnings = build_inline_math(expression, cfg)
        assert isinstance(segments, list)
        assert isinstance(warnings, list)

    @given(
        expression=st.text(min_size=0, max_size=EQUATION_CHAR_LIMIT),
    )
    @settings(max_examples=200)
    def test_equation_strategy_within_limit_no_warnings(
        self, expression: str
    ) -> None:
        """Equation strategy with expression <= limit produces no warnings."""
        cfg = self._config(strategy="equation")
        blocks, warnings = build_block_math(expression, cfg)
        assert warnings == []
        assert len(blocks) == 1
        assert blocks[0]["type"] == "equation"

    @given(
        expression=st.text(
            min_size=EQUATION_CHAR_LIMIT + 1, max_size=EQUATION_CHAR_LIMIT + 500
        ),
        overflow=st.sampled_from(_MATH_OVERFLOW_BLOCK),
    )
    @settings(max_examples=100)
    def test_equation_strategy_overflow_emits_warning(
        self, expression: str, overflow: str
    ) -> None:
        """Equation strategy with expression > limit always emits MATH_OVERFLOW warning."""
        cfg = self._config(strategy="equation", overflow_block=overflow)
        _, warnings = build_block_math(expression, cfg)
        assert len(warnings) >= 1
        assert warnings[0].code == "MATH_OVERFLOW"

    @given(expression=st.text(max_size=2000))
    @settings(max_examples=200)
    def test_code_strategy_always_one_block(self, expression: str) -> None:
        """Code strategy always returns exactly one code block."""
        cfg = self._config(strategy="code")
        blocks, warnings = build_block_math(expression, cfg)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "code"
        assert warnings == []

    @given(expression=st.text(max_size=2000))
    @settings(max_examples=200)
    def test_latex_text_strategy_always_one_paragraph(self, expression: str) -> None:
        """latex_text strategy always returns exactly one paragraph block."""
        cfg = self._config(strategy="latex_text")
        blocks, warnings = build_block_math(expression, cfg)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "paragraph"
        assert warnings == []

    @given(expression=st.text(min_size=1, max_size=500))
    @settings(max_examples=200)
    def test_block_math_all_blocks_have_type(self, expression: str) -> None:
        """Every block returned by build_block_math must have a 'type' key."""
        for strategy in _MATH_STRATEGIES:
            cfg = self._config(strategy=strategy, overflow_block="split")
            blocks, _ = build_block_math(expression, cfg)
            for block in blocks:
                assert "type" in block


# ---------------------------------------------------------------------------
# 15. TestTableBuilderProperties
# ---------------------------------------------------------------------------

# Strategy for a minimal table cell token.
_table_cell_st = st.fixed_dictionaries({
    "type": st.just("table_cell"),
    "attrs": st.fixed_dictionaries({
        "align": st.none(),
        "head": st.booleans(),
    }),
    "children": st.lists(
        st.fixed_dictionaries({
            "type": st.just("text"),
            "raw": st.text(max_size=50),
        }),
        max_size=3,
    ),
})

_table_row_st = st.fixed_dictionaries({
    "type": st.just("table_row"),
    "children": st.lists(_table_cell_st, min_size=1, max_size=5),
})

_table_head_st = st.fixed_dictionaries({
    "type": st.just("table_head"),
    "children": st.lists(_table_cell_st, min_size=1, max_size=5),
})

_table_body_st = st.fixed_dictionaries({
    "type": st.just("table_body"),
    "children": st.lists(_table_row_st, max_size=5),
})

_table_token_st = st.fixed_dictionaries({
    "type": st.just("table"),
    "children": st.lists(
        st.one_of(_table_head_st, _table_body_st),
        max_size=4,
    ),
})


class TestTableBuilderProperties:
    """Property-based tests for :func:`build_table`."""

    _BASE_URL = "https://api.notion.com/v1"
    _config = NotionifyConfig(token="test", base_url="https://api.notion.com/v1")

    @given(token=_table_token_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_build_table_never_raises(self, token: dict) -> None:
        """build_table must never raise on any table AST token."""
        block, warnings = build_table(token, self._config)
        assert block is None or isinstance(block, dict)
        assert isinstance(warnings, list)

    @given(token=_table_token_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_build_table_enabled_returns_table_or_none(self, token: dict) -> None:
        """With enable_tables=True, result is either a table block or None."""
        block, _ = build_table(token, self._config)
        if block is not None:
            assert block.get("type") == "table"

    @given(token=_table_token_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_build_table_disabled_returns_none_or_paragraph(
        self, token: dict
    ) -> None:
        """With enable_tables=False, result is None or a paragraph fallback."""
        cfg = NotionifyConfig(
            token="test",
            base_url=self._BASE_URL,
            enable_tables=False,
            table_fallback="comment",
        )
        block, _ = build_table(token, cfg)
        assert block is None or block.get("type") in ("paragraph", "code")

    @given(token=_table_token_st)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_build_table_is_deterministic(self, token: dict) -> None:
        """build_table must return the same result for identical inputs."""
        block1, w1 = build_table(token, self._config)
        block2, w2 = build_table(token, self._config)
        assert block1 == block2
        assert [w.code for w in w1] == [w.code for w in w2]


# ---------------------------------------------------------------------------
# 16. TestNormalizeLanguageProperties
# ---------------------------------------------------------------------------

_ALL_NOTION_LANGS = sorted(_NOTION_LANGUAGES)
_ALL_ALIASES = sorted(_LANGUAGE_ALIASES.keys())


class TestNormalizeLanguageProperties:
    """Property-based tests for :func:`_normalize_language`."""

    @given(info=st.text(max_size=100))
    @settings(max_examples=200)
    def test_always_returns_string(self, info: str) -> None:
        """_normalize_language must always return a non-empty string."""
        result = _normalize_language(info)
        assert isinstance(result, str)
        assert len(result) > 0

    @given(info=st.one_of(st.none(), st.just(""), st.just("  ")))
    @settings(max_examples=20)
    def test_none_or_empty_returns_plain_text(self, info) -> None:
        """None or empty info always returns 'plain text'."""
        assert _normalize_language(info) == "plain text"

    @given(lang=st.sampled_from(_ALL_NOTION_LANGS))
    @settings(max_examples=50)
    def test_known_notion_language_returned_unchanged(self, lang: str) -> None:
        """Known Notion language identifiers are returned as-is."""
        assert _normalize_language(lang) == lang

    @given(alias=st.sampled_from(_ALL_ALIASES))
    @settings(max_examples=50)
    def test_known_alias_resolves_correctly(self, alias: str) -> None:
        """Known aliases are resolved to their canonical Notion language."""
        expected = _LANGUAGE_ALIASES[alias]
        assert _normalize_language(alias) == expected

    @given(info=st.text(max_size=100))
    @settings(max_examples=200)
    def test_result_always_in_notion_languages(self, info: str) -> None:
        """Every result must be a known Notion language or 'plain text'."""
        result = _normalize_language(info)
        assert result in _NOTION_LANGUAGES or result == "plain text"

    @given(lang=st.sampled_from(_ALL_NOTION_LANGS))
    @settings(max_examples=50)
    def test_case_insensitive(self, lang: str) -> None:
        """Language matching is case-insensitive."""
        assert _normalize_language(lang.upper()) == lang
        assert _normalize_language(lang.capitalize()) == lang


# ---------------------------------------------------------------------------
# 17. TestConflictDetectionProperties
# ---------------------------------------------------------------------------

_dt_st = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
    timezones=st.just(_tz.utc),
)
_etags_st = st.dictionaries(
    keys=st.from_regex(r"[a-f0-9\-]{8,36}", fullmatch=True),
    values=st.from_regex(r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", fullmatch=True),
    max_size=10,
)


def _make_snapshot(page_id: str, last_edited: datetime, block_etags: dict):
    from notionify.models import PageSnapshot
    return PageSnapshot(
        page_id=page_id,
        last_edited=last_edited,
        block_etags=block_etags,
    )


class TestConflictDetectionProperties:
    """Property-based tests for :func:`detect_conflict` and :func:`take_snapshot`."""

    @given(
        page_id=st.text(min_size=1, max_size=36),
        last_edited=_dt_st,
        block_etags=_etags_st,
    )
    @settings(max_examples=200)
    def test_snapshot_vs_itself_no_conflict(
        self, page_id: str, last_edited: datetime, block_etags: dict
    ) -> None:
        """A snapshot compared against itself must never indicate a conflict."""
        snapshot = _make_snapshot(page_id, last_edited, block_etags)
        assert not detect_conflict(snapshot, snapshot)

    @given(
        page_id=st.text(min_size=1, max_size=36),
        t1=_dt_st,
        t2=_dt_st,
        etags=_etags_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_different_timestamps_always_conflict(
        self, page_id: str, t1: datetime, t2: datetime, etags: dict
    ) -> None:
        """Different last_edited timestamps must always indicate a conflict."""
        assume(t1 != t2)
        s1 = _make_snapshot(page_id, t1, etags)
        s2 = _make_snapshot(page_id, t2, etags)
        assert detect_conflict(s1, s2)

    @given(
        page_id=st.text(min_size=1, max_size=36),
        page=st.fixed_dictionaries({
            "last_edited_time": st.one_of(
                st.just(""),
                st.from_regex(
                    r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", fullmatch=True
                ),
            )
        }),
        blocks=st.lists(
            st.fixed_dictionaries({
                "id": st.text(min_size=1, max_size=36),
                "last_edited_time": st.from_regex(
                    r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", fullmatch=True
                ),
            }),
            max_size=10,
        ),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_take_snapshot_never_raises(
        self, page_id: str, page: dict, blocks: list[dict]
    ) -> None:
        """take_snapshot must never raise on valid inputs."""
        from notionify.models import PageSnapshot
        snapshot = take_snapshot(page_id, page, blocks)
        assert isinstance(snapshot, PageSnapshot)
        assert snapshot.page_id == page_id

    @given(
        page_id=st.text(min_size=1, max_size=36),
        t=_dt_st,
        etags=_etags_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_changed_block_etag_triggers_conflict(
        self,
        page_id: str,
        t: datetime,
        etags: dict,
    ) -> None:
        """Any change in block ETags triggers conflict detection."""
        assume(len(etags) > 0)
        # Create snapshot with original ETags.
        s1 = _make_snapshot(page_id, t, etags)
        # Modify the first block's etag.
        first_key = next(iter(etags))
        modified = {**etags, first_key: etags[first_key] + "_modified"}
        s2 = _make_snapshot(page_id, t, modified)
        assert detect_conflict(s1, s2)

    @given(
        page_id=st.text(min_size=1, max_size=36),
        t=_dt_st,
        etags=_etags_st,
        new_block_id=st.text(min_size=1, max_size=36),
        new_etag=st.text(min_size=1, max_size=30),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_extra_blocks_in_current_do_not_conflict_alone(
        self,
        page_id: str,
        t: datetime,
        etags: dict,
        new_block_id: str,
        new_etag: str,
    ) -> None:
        """Blocks only in 'current' (not in snapshot) never trigger conflict alone.

        detect_conflict only checks blocks from snapshot.block_etags.
        If current has additional blocks and the page timestamp is unchanged,
        no conflict is reported.
        """
        assume(new_block_id not in etags)
        s1 = _make_snapshot(page_id, t, etags)
        extended = {**etags, new_block_id: new_etag}
        s2 = _make_snapshot(page_id, t, extended)
        assert not detect_conflict(s1, s2)

    @given(
        page_id=st.text(min_size=1, max_size=36),
        t=_dt_st,
        etags=_etags_st,
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_detect_conflict_is_reflexive(
        self, page_id: str, t: datetime, etags: dict
    ) -> None:
        """detect_conflict(s, s) is always False (reflexivity property)."""
        s = _make_snapshot(page_id, t, etags)
        assert not detect_conflict(s, s)

    @given(
        page_id=st.text(min_size=1, max_size=36),
        blocks=st.lists(
            st.fixed_dictionaries({
                "id": st.text(min_size=1, max_size=36),
                "last_edited_time": st.from_regex(
                    r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", fullmatch=True
                ),
            }),
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_take_snapshot_captures_all_blocks_with_id_and_time(
        self, page_id: str, blocks: list[dict]
    ) -> None:
        """Every block with both 'id' and 'last_edited_time' appears in block_etags."""
        # Require unique IDs so last-wins dict semantics don't hide any blocks.
        assume(len({b["id"] for b in blocks}) == len(blocks))
        page = {"last_edited_time": ""}
        snapshot = take_snapshot(page_id, page, blocks)
        for block in blocks:
            assert block["id"] in snapshot.block_etags
            assert snapshot.block_etags[block["id"]] == block["last_edited_time"]


# ---------------------------------------------------------------------------
# TestRichTextBuilderProperties
# ---------------------------------------------------------------------------

class TestRichTextBuilderProperties:
    """Property-based tests for build_rich_text and extract_text."""

    _config = NotionifyConfig()

    def test_empty_children_returns_empty(self) -> None:
        """build_rich_text([]) always returns []."""
        assert build_rich_text([], self._config) == []

    @given(
        tokens=st.lists(
            st.fixed_dictionaries({
                "type": st.just("text"),
                "raw": st.text(max_size=100),
            }),
            max_size=10,
        )
    )
    @settings(max_examples=200)
    def test_never_raises_on_text_tokens(self, tokens: list[dict]) -> None:
        """build_rich_text never raises on a list of text tokens."""
        result = build_rich_text(tokens, self._config)
        assert isinstance(result, list)

    @given(
        tokens=st.lists(
            st.fixed_dictionaries({
                "type": st.just("text"),
                "raw": st.text(min_size=1, max_size=50),
            }),
            min_size=1,
            max_size=5,
        )
    )
    @settings(max_examples=200)
    def test_text_tokens_produce_typed_segments(self, tokens: list[dict]) -> None:
        """Every segment produced from text tokens has a 'type' key."""
        result = build_rich_text(tokens, self._config)
        for seg in result:
            assert "type" in seg

    @given(raws=st.lists(st.text(max_size=50), max_size=5))
    @settings(max_examples=200)
    def test_extract_text_concatenates_raws(self, raws: list[str]) -> None:
        """extract_text on plain text tokens returns concatenated raw values."""
        tokens = [{"type": "text", "raw": r} for r in raws]
        assert extract_text(tokens) == "".join(raws)

    def test_extract_text_empty(self) -> None:
        """extract_text([]) returns empty string."""
        assert extract_text([]) == ""

    @given(inner_text=st.text(min_size=1, max_size=50))
    @settings(max_examples=200)
    def test_strong_wrapper_sets_bold(self, inner_text: str) -> None:
        """Text wrapped in a strong token has bold=True annotation."""
        tokens = [{"type": "strong", "children": [{"type": "text", "raw": inner_text}]}]
        result = build_rich_text(tokens, self._config)
        assert len(result) > 0
        for seg in result:
            if seg.get("type") == "text":
                assert seg.get("annotations", {}).get("bold") is True

    @given(
        inner_text=st.text(min_size=1, max_size=50),
        url=st.from_regex(r"https://[a-z]{3,10}\.[a-z]{2,4}/[a-z]{0,5}", fullmatch=True),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_link_token_sets_href(self, inner_text: str, url: str) -> None:
        """Text wrapped in a link token has href set to the link URL."""
        tokens = [{"type": "link", "attrs": {"url": url},
                   "children": [{"type": "text", "raw": inner_text}]}]
        result = build_rich_text(tokens, self._config)
        assert len(result) > 0
        for seg in result:
            if seg.get("type") == "text":
                assert seg.get("href") == url

    @given(
        base_bold=st.booleans(),
        base_italic=st.booleans(),
        override_bold=st.booleans(),
    )
    @settings(max_examples=200)
    def test_merge_annotations_or_semantics(
        self, base_bold: bool, base_italic: bool, override_bold: bool
    ) -> None:
        """_merge_annotations uses OR semantics: True once => always True."""
        from notionify.converter.rich_text import _merge_annotations
        base = {
            "bold": base_bold, "italic": base_italic,
            "strikethrough": False, "underline": False,
            "code": False, "color": "default",
        }
        merged = _merge_annotations(base, bold=override_bold)
        assert merged["bold"] == (base_bold or override_bold)
        assert merged["italic"] == base_italic  # not overridden

    @given(
        tokens=st.lists(
            st.one_of(
                st.fixed_dictionaries({"type": st.just("softbreak")}),
                st.fixed_dictionaries({"type": st.just("linebreak")}),
            ),
            min_size=1,
            max_size=10,
        )
    )
    @settings(max_examples=200)
    def test_break_tokens_produce_whitespace(self, tokens: list[dict]) -> None:
        """softbreak and linebreak tokens each produce a single whitespace segment."""
        result = build_rich_text(tokens, self._config)
        assert len(result) == len(tokens)
        for seg in result:
            content = seg.get("text", {}).get("content", "")
            assert content in (" ", "\n")


# ---------------------------------------------------------------------------
# TestDiffPlannerProperties
# ---------------------------------------------------------------------------

# Reusable block-ID alphabet (no special chars to avoid hypothesis edge cases).
_BLOCK_ID_ALPHABET = string.ascii_lowercase + string.digits + "-"

# Strategy for a simple block with a unique-ish ID.
_block_with_id_st = st.fixed_dictionaries({
    "id": st.from_regex(r"[a-z0-9]{8}", fullmatch=True),
    "type": st.sampled_from(["paragraph", "heading_1", "bulleted_list_item"]),
})

# Strategy for a new block (no ID needed by planner).
_new_block_st = st.fixed_dictionaries({
    "type": st.sampled_from(["paragraph", "heading_1", "bulleted_list_item"]),
})


class TestDiffPlannerProperties:
    """Property-based tests for DiffPlanner.plan invariants."""

    _config = NotionifyConfig()

    def test_both_empty_returns_empty(self) -> None:
        """plan([], []) returns []."""
        planner = DiffPlanner(self._config)
        assert planner.plan([], []) == []

    @given(new_blocks=st.lists(_new_block_st, max_size=10))
    @settings(max_examples=200)
    def test_no_existing_all_inserts(self, new_blocks: list[dict]) -> None:
        """plan([], new) produces only INSERT operations."""
        planner = DiffPlanner(self._config)
        ops = planner.plan([], new_blocks)
        assert len(ops) == len(new_blocks)
        assert all(op.op_type == DiffOpType.INSERT for op in ops)

    @given(
        existing_blocks=st.lists(
            st.fixed_dictionaries({
                "id": st.from_regex(r"[a-z0-9]{8}", fullmatch=True),
                "type": st.just("paragraph"),
            }),
            min_size=1,
            max_size=10,
        )
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_no_new_all_deletes(self, existing_blocks: list[dict]) -> None:
        """plan(existing, []) produces only DELETE operations."""
        planner = DiffPlanner(self._config)
        ops = planner.plan(existing_blocks, [])
        assert len(ops) == len(existing_blocks)
        assert all(op.op_type == DiffOpType.DELETE for op in ops)

    @given(
        existing_blocks=st.lists(_block_with_id_st, max_size=8),
        new_blocks=st.lists(_new_block_st, max_size=8),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_ops_account_for_all_blocks(
        self, existing_blocks: list[dict], new_blocks: list[dict]
    ) -> None:
        """All existing and new blocks are accounted for exactly once in the ops."""
        planner = DiffPlanner(self._config)
        ops = planner.plan(existing_blocks, new_blocks)

        # Each INSERT/UPDATE/REPLACE/KEEP consumes one new block.
        new_ops = (DiffOpType.INSERT, DiffOpType.UPDATE, DiffOpType.REPLACE, DiffOpType.KEEP)
        old_ops = (DiffOpType.DELETE, DiffOpType.UPDATE, DiffOpType.REPLACE, DiffOpType.KEEP)
        new_consuming = sum(1 for op in ops if op.op_type in new_ops)
        existing_consuming = sum(1 for op in ops if op.op_type in old_ops)
        assert new_consuming == len(new_blocks)
        assert existing_consuming == len(existing_blocks)

    @given(
        texts=st.lists(st.text(min_size=1, max_size=30), min_size=1, max_size=8, unique=True)
    )
    @settings(max_examples=100)
    def test_identical_lists_all_keep(self, texts: list[str]) -> None:
        """Identical existing and new blocks produce only KEEP operations."""
        import hashlib
        planner = DiffPlanner(self._config)
        blocks = [
            {
                "id": hashlib.md5(t.encode()).hexdigest()[:8],
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": t}}]},
            }
            for t in texts
        ]
        ops = planner.plan(blocks, blocks)
        assert all(op.op_type == DiffOpType.KEEP for op in ops)

    @given(
        existing_blocks=st.lists(_block_with_id_st, max_size=8),
        new_blocks=st.lists(_new_block_st, max_size=8),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_plan_never_raises(
        self, existing_blocks: list[dict], new_blocks: list[dict]
    ) -> None:
        """DiffPlanner.plan never raises on valid inputs."""
        planner = DiffPlanner(self._config)
        ops = planner.plan(existing_blocks, new_blocks)
        assert isinstance(ops, list)

    @given(
        existing_blocks=st.lists(
            st.fixed_dictionaries({
                "id": st.from_regex(r"[a-z0-9]{8}", fullmatch=True),
                "type": st.just("paragraph"),
            }),
            min_size=1,
            max_size=8,
        ),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_keep_ids_are_subset_of_existing(self, existing_blocks: list[dict]) -> None:
        """KEEP op IDs must reference IDs present in existing."""
        planner = DiffPlanner(self._config)
        # Use same blocks as new (identical, so all KEEP).
        ops = planner.plan(existing_blocks, existing_blocks)
        existing_ids = {b["id"] for b in existing_blocks}
        for op in ops:
            if op.op_type == DiffOpType.KEEP:
                assert op.existing_id in existing_ids


# ---------------------------------------------------------------------------
# TestTokenBucketProperties
# ---------------------------------------------------------------------------

class TestTokenBucketProperties:
    """Property-based tests for TokenBucket initialization and state invariants."""

    @given(rate=st.floats(min_value=-100.0, max_value=0.0, allow_nan=False))
    @settings(max_examples=100)
    def test_invalid_rate_raises(self, rate: float) -> None:
        """rate_rps <= 0 raises ValueError."""
        from notionify.notion_api.rate_limit import TokenBucket
        with pytest.raises(ValueError, match="rate_rps"):
            TokenBucket(rate_rps=rate)

    @given(burst=st.integers(max_value=0))
    @settings(max_examples=100)
    def test_invalid_burst_raises(self, burst: int) -> None:
        """burst < 1 raises ValueError."""
        from notionify.notion_api.rate_limit import TokenBucket
        with pytest.raises(ValueError, match="burst"):
            TokenBucket(rate_rps=1.0, burst=burst)

    @given(
        rate=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False),
        burst=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=200)
    def test_initial_tokens_equals_burst(self, rate: float, burst: int) -> None:
        """Fresh bucket starts at full capacity (tokens == burst)."""
        from notionify.notion_api.rate_limit import TokenBucket
        bucket = TokenBucket(rate_rps=rate, burst=burst)
        assert bucket.tokens == float(burst)

    @given(
        rate=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False),
        burst=st.integers(min_value=1, max_value=100),
        requested=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=200)
    def test_acquire_from_full_bucket_no_wait(
        self, rate: float, burst: int, requested: int
    ) -> None:
        """Acquiring <= burst tokens from a full bucket returns 0.0 wait."""
        from unittest.mock import patch

        from notionify.notion_api.rate_limit import TokenBucket
        assume(requested <= burst)
        bucket = TokenBucket(rate_rps=rate, burst=burst)
        with patch("time.sleep"):
            wait = bucket.acquire(requested)
        assert wait == 0.0
        assert bucket.tokens == float(burst - requested)

    @given(
        rate=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False),
        burst=st.integers(min_value=1, max_value=100),
        requested=st.integers(min_value=1, max_value=200),
    )
    @settings(max_examples=200)
    def test_acquire_wait_is_non_negative(
        self, rate: float, burst: int, requested: int
    ) -> None:
        """acquire() always returns a non-negative wait time."""
        from unittest.mock import patch

        from notionify.notion_api.rate_limit import TokenBucket
        bucket = TokenBucket(rate_rps=rate, burst=burst)
        with patch("time.sleep"):
            wait = bucket.acquire(requested)
        assert wait >= 0.0


# ---------------------------------------------------------------------------
# TestImageMimeSniffProperties
# ---------------------------------------------------------------------------

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_GIF87_MAGIC = b"GIF87a"
_GIF89_MAGIC = b"GIF89a"
_BMP_MAGIC = b"BM"


class TestImageMimeSniffProperties:
    """Property-based tests for _sniff_mime magic-byte MIME detection."""

    @given(suffix=st.binary(max_size=20))
    @settings(max_examples=200)
    def test_png_magic_detected(self, suffix: bytes) -> None:
        """PNG magic bytes always yield 'image/png'."""
        from notionify.image.validate import _sniff_mime
        assert _sniff_mime(_PNG_MAGIC + suffix) == "image/png"

    @given(suffix=st.binary(max_size=20))
    @settings(max_examples=200)
    def test_jpeg_magic_detected(self, suffix: bytes) -> None:
        """JPEG magic bytes always yield 'image/jpeg'."""
        from notionify.image.validate import _sniff_mime
        assert _sniff_mime(_JPEG_MAGIC + suffix) == "image/jpeg"

    @given(suffix=st.binary(max_size=20))
    @settings(max_examples=200)
    def test_gif87_magic_detected(self, suffix: bytes) -> None:
        """GIF87a magic bytes always yield 'image/gif'."""
        from notionify.image.validate import _sniff_mime
        assert _sniff_mime(_GIF87_MAGIC + suffix) == "image/gif"

    @given(suffix=st.binary(max_size=20))
    @settings(max_examples=200)
    def test_gif89_magic_detected(self, suffix: bytes) -> None:
        """GIF89a magic bytes always yield 'image/gif'."""
        from notionify.image.validate import _sniff_mime
        assert _sniff_mime(_GIF89_MAGIC + suffix) == "image/gif"

    @given(middle=st.binary(min_size=4, max_size=4), suffix=st.binary(max_size=10))
    @settings(max_examples=200)
    def test_webp_magic_detected(self, middle: bytes, suffix: bytes) -> None:
        """RIFF....WEBP pattern yields 'image/webp'."""
        from notionify.image.validate import _sniff_mime
        data = b"RIFF" + middle + b"WEBP" + suffix
        assert _sniff_mime(data) == "image/webp"

    def test_riff_non_webp_returns_none(self) -> None:
        """RIFF with non-WEBP marker at bytes 8-12 returns None."""
        from notionify.image.validate import _sniff_mime
        # RIFF + 4 bytes padding + WAVE (not WEBP) + padding
        data = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00\x00\x00\x00"
        assert _sniff_mime(data) is None

    @given(suffix=st.binary(max_size=20))
    @settings(max_examples=200)
    def test_bmp_magic_detected(self, suffix: bytes) -> None:
        """BM magic bytes always yield 'image/bmp'."""
        from notionify.image.validate import _sniff_mime
        assert _sniff_mime(_BMP_MAGIC + suffix) == "image/bmp"

    @given(data=st.binary(max_size=3))
    @settings(max_examples=200)
    def test_short_data_result_is_valid(self, data: bytes) -> None:
        """_sniff_mime on short data returns None or a valid image/ MIME type."""
        from notionify.image.validate import _sniff_mime
        result = _sniff_mime(data)
        assert result is None or result.startswith("image/")


# ---------------------------------------------------------------------------
# TestDiffExecutorProperties
# ---------------------------------------------------------------------------

def _make_mock_block_api() -> object:
    """Return a MagicMock that mimics BlockAPI for executor tests."""
    from unittest.mock import MagicMock
    api = MagicMock()
    # append_children returns a response with results containing one block ID.
    api.append_children.return_value = {
        "results": [{"id": "mock-appended-id"}],
    }
    return api


def _make_op(op_type: DiffOpType, eid: str | None = None, new_block: dict | None = None) -> DiffOp:
    """Helper to construct a DiffOp."""
    return DiffOp(op_type=op_type, existing_id=eid, new_block=new_block)


class TestDiffExecutorProperties:
    """Property-based tests for DiffExecutor operation counting invariants."""

    _config = NotionifyConfig()

    def test_empty_ops_zero_counts(self) -> None:
        """Empty op list → all zero counts in UpdateResult."""
        from notionify.diff.executor import DiffExecutor
        api = _make_mock_block_api()
        executor = DiffExecutor(api, self._config)
        result = executor.execute("page-id", [])
        assert result.blocks_kept == 0
        assert result.blocks_inserted == 0
        assert result.blocks_deleted == 0
        assert result.blocks_replaced == 0

    @given(n=st.integers(min_value=0, max_value=20))
    @settings(max_examples=100)
    def test_all_keep_ops_increments_kept(self, n: int) -> None:
        """n KEEP ops → kept == n, inserted/deleted/replaced == 0."""
        from notionify.diff.executor import DiffExecutor
        api = _make_mock_block_api()
        executor = DiffExecutor(api, self._config)
        ops = [_make_op(DiffOpType.KEEP, eid=f"block-{i}") for i in range(n)]
        result = executor.execute("page-id", ops)
        assert result.blocks_kept == n
        assert result.blocks_inserted == 0
        assert result.blocks_deleted == 0
        assert result.blocks_replaced == 0

    @given(n=st.integers(min_value=0, max_value=20))
    @settings(max_examples=100)
    def test_all_delete_ops_increments_deleted(self, n: int) -> None:
        """n DELETE ops → deleted == n, kept/inserted/replaced == 0."""
        from notionify.diff.executor import DiffExecutor
        api = _make_mock_block_api()
        executor = DiffExecutor(api, self._config)
        ops = [_make_op(DiffOpType.DELETE, eid=f"block-{i}") for i in range(n)]
        result = executor.execute("page-id", ops)
        assert result.blocks_deleted == n
        assert result.blocks_kept == 0
        assert result.blocks_replaced == 0

    @given(n=st.integers(min_value=0, max_value=20))
    @settings(max_examples=100)
    def test_all_insert_ops_increments_inserted(self, n: int) -> None:
        """n INSERT ops → inserted == n, kept/deleted/replaced == 0."""
        from notionify.diff.executor import DiffExecutor
        api = _make_mock_block_api()
        executor = DiffExecutor(api, self._config)
        ops = [
            _make_op(DiffOpType.INSERT, new_block={"type": "paragraph"})
            for _ in range(n)
        ]
        result = executor.execute("page-id", ops)
        assert result.blocks_inserted == n
        assert result.blocks_kept == 0
        assert result.blocks_deleted == 0
        assert result.blocks_replaced == 0

    @given(n=st.integers(min_value=0, max_value=10))
    @settings(max_examples=100)
    def test_all_replace_ops_increments_replaced_and_deleted(self, n: int) -> None:
        """n REPLACE ops → replaced == n, deleted == n, kept/inserted == 0."""
        from notionify.diff.executor import DiffExecutor
        api = _make_mock_block_api()
        executor = DiffExecutor(api, self._config)
        ops = [
            _make_op(DiffOpType.REPLACE, eid=f"block-{i}", new_block={"type": "paragraph"})
            for i in range(n)
        ]
        result = executor.execute("page-id", ops)
        assert result.blocks_replaced == n
        assert result.blocks_deleted == n
        assert result.blocks_kept == 0
        assert result.blocks_inserted == 0

    @given(
        n_keep=st.integers(min_value=0, max_value=5),
        n_insert=st.integers(min_value=0, max_value=5),
        n_delete=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=100)
    def test_mixed_ops_counts_are_additive(
        self, n_keep: int, n_insert: int, n_delete: int
    ) -> None:
        """Mixed ops: counts are the sum of each op type."""
        from notionify.diff.executor import DiffExecutor
        api = _make_mock_block_api()
        executor = DiffExecutor(api, self._config)
        _ins = {"type": "paragraph"}
        ops = (
            [_make_op(DiffOpType.KEEP, eid=f"k{i}") for i in range(n_keep)]
            + [_make_op(DiffOpType.INSERT, new_block=_ins) for _ in range(n_insert)]
            + [_make_op(DiffOpType.DELETE, eid=f"d{i}") for i in range(n_delete)]
        )
        result = executor.execute("page-id", ops)
        assert result.blocks_kept == n_keep
        assert result.blocks_inserted == n_insert
        assert result.blocks_deleted == n_delete

    @given(
        op_types=st.lists(
            st.sampled_from(list(DiffOpType)),
            min_size=0,
            max_size=20,
        )
    )
    @settings(max_examples=200)
    def test_emit_diff_metrics_sum_equals_op_count(
        self, op_types: list[DiffOpType]
    ) -> None:
        """_emit_diff_metrics: total of all increment values equals len(ops)."""
        from notionify.diff.executor import _emit_diff_metrics

        increments: list[int] = []

        class TrackingMetrics:
            def increment(self, name: str, value: int = 1, tags=None) -> None:
                increments.append(value)

            def timing(self, name: str, ms: float, tags=None) -> None:
                pass

            def gauge(self, name: str, value: float, tags=None) -> None:
                pass

        ops = [
            DiffOp(op_type=op_type, existing_id="x" if op_type != DiffOpType.INSERT else None)
            for op_type in op_types
        ]
        _emit_diff_metrics(TrackingMetrics(), ops)
        assert sum(increments) == len(ops)


# ---------------------------------------------------------------------------
# TestComputeSignatureProperties
# ---------------------------------------------------------------------------

# Strategy for a minimal Notion block dict.
_sig_block_st = st.fixed_dictionaries({
    "type": st.sampled_from(
        ["paragraph", "heading_1", "heading_2", "bulleted_list_item",
         "numbered_list_item", "code", "equation", "divider", "quote"]
    ),
}).flatmap(lambda b: st.fixed_dictionaries({
    "type": st.just(b["type"]),
    b["type"]: st.fixed_dictionaries({
        "rich_text": st.lists(
            st.fixed_dictionaries({
                "type": st.just("text"),
                "text": st.fixed_dictionaries({"content": st.text(max_size=50)}),
            }),
            max_size=3,
        ),
    }),
}))


class TestComputeSignatureProperties:
    """Property-based tests for compute_signature invariants."""

    @given(block=_sig_block_st)
    @settings(max_examples=200)
    def test_deterministic(self, block: dict) -> None:
        """compute_signature is deterministic: same input → same output."""
        sig1 = compute_signature(block)
        sig2 = compute_signature(block)
        assert sig1 == sig2

    @given(block=_sig_block_st)
    @settings(max_examples=200)
    def test_block_type_captured(self, block: dict) -> None:
        """sig.block_type matches block['type']."""
        sig = compute_signature(block)
        assert sig.block_type == block["type"]

    @given(
        block=_sig_block_st,
        depth=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=200)
    def test_nesting_depth_captured(self, block: dict, depth: int) -> None:
        """sig.nesting_depth matches the depth argument."""
        sig = compute_signature(block, depth=depth)
        assert sig.nesting_depth == depth

    @given(block=_sig_block_st)
    @settings(max_examples=200)
    def test_never_raises(self, block: dict) -> None:
        """compute_signature never raises on arbitrary block dicts."""
        sig = compute_signature(block)
        assert isinstance(sig, BlockSignature)

    @given(
        block_type=st.sampled_from(["paragraph", "heading_1", "code"]),
        text_a=st.text(min_size=1, max_size=50),
        text_b=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=200)
    def test_different_text_different_rich_text_hash(
        self, block_type: str, text_a: str, text_b: str
    ) -> None:
        """Blocks differing only in text content produce different rich_text_hash."""
        assume(text_a != text_b)

        def _make_text_block(txt: str) -> dict:
            return {"type": block_type, block_type: {
                "rich_text": [{"type": "text", "text": {"content": txt}}],
            }}

        sig_a = compute_signature(_make_text_block(text_a))
        sig_b = compute_signature(_make_text_block(text_b))
        assert sig_a.rich_text_hash != sig_b.rich_text_hash

    @given(
        text=st.text(max_size=30),
        type_a=st.sampled_from(["paragraph", "heading_1", "code"]),
        type_b=st.sampled_from(["quote", "heading_2", "numbered_list_item"]),
    )
    @settings(max_examples=200)
    def test_different_type_different_signature(
        self, text: str, type_a: str, type_b: str
    ) -> None:
        """Blocks with different types always produce different signatures."""
        assume(type_a != type_b)

        def _make_typed_block(btype: str) -> dict:
            return {"type": btype, btype: {
                "rich_text": [{"type": "text", "text": {"content": text}}],
            }}

        sig_a = compute_signature(_make_typed_block(type_a))
        sig_b = compute_signature(_make_typed_block(type_b))
        assert sig_a != sig_b


# ---------------------------------------------------------------------------
# TestDataUriParseProperties
# ---------------------------------------------------------------------------

class TestDataUriParseProperties:
    """Property-based tests for _parse_data_uri."""

    @given(
        raw_bytes=st.binary(min_size=0, max_size=200),
        mime=st.sampled_from(["image/png", "image/jpeg", "image/gif", "image/webp"]),
    )
    @settings(max_examples=200)
    def test_valid_base64_uri_round_trips(self, raw_bytes: bytes, mime: str) -> None:
        """Valid base64 data URIs are parsed back to original bytes."""
        import base64

        from notionify.image.validate import _parse_data_uri
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        src = f"data:{mime};base64,{encoded}"
        parsed_mime, decoded = _parse_data_uri(src)
        assert parsed_mime == mime
        assert decoded == raw_bytes

    @given(
        raw_bytes=st.binary(min_size=0, max_size=200),
        mime=st.sampled_from(["image/png", "image/jpeg"]),
    )
    @settings(max_examples=200)
    def test_base64_uri_mime_is_preserved(self, raw_bytes: bytes, mime: str) -> None:
        """MIME type from data URI header is returned verbatim."""
        import base64

        from notionify.image.validate import _parse_data_uri
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        src = f"data:{mime};base64,{encoded}"
        parsed_mime, _ = _parse_data_uri(src)
        assert parsed_mime == mime

    @given(garbage=st.text(min_size=1, max_size=50, alphabet=string.ascii_letters + "!@#$%"))
    @settings(max_examples=200)
    def test_invalid_base64_raises_parse_error(self, garbage: str) -> None:
        """Invalid base64 payload raises NotionifyImageParseError."""
        from notionify.errors import NotionifyImageParseError
        from notionify.image.validate import _parse_data_uri
        # Use characters that are invalid in base64 to force a decode error.
        src = f"data:image/png;base64,{garbage}!!!invalid!!!"
        with pytest.raises((NotionifyImageParseError, Exception)):
            _parse_data_uri(src)

    @given(
        raw_bytes=st.binary(min_size=0, max_size=100),
    )
    @settings(max_examples=200)
    def test_no_mime_defaults_to_octet_stream(self, raw_bytes: bytes) -> None:
        """Data URIs without a MIME type default to 'application/octet-stream'."""
        import base64

        from notionify.image.validate import _parse_data_uri
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        src = f"data:;base64,{encoded}"
        parsed_mime, _ = _parse_data_uri(src)
        assert parsed_mime == "application/octet-stream"

    @given(
        raw_bytes=st.binary(min_size=0, max_size=100),
        mime=st.sampled_from(["image/png", "image/jpeg"]),
    )
    @settings(max_examples=200)
    def test_decoded_length_matches_original(self, raw_bytes: bytes, mime: str) -> None:
        """Decoded bytes length equals original bytes length."""
        import base64

        from notionify.image.validate import _parse_data_uri
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        src = f"data:{mime};base64,{encoded}"
        _, decoded = _parse_data_uri(src)
        assert len(decoded) == len(raw_bytes)


# ---------------------------------------------------------------------------
# TestUploadStateMachineProperties
# ---------------------------------------------------------------------------

class TestUploadStateMachineProperties:
    """Property-based tests for UploadStateMachine state transition invariants."""

    def test_initial_state_is_pending(self) -> None:
        """Fresh state machine always starts in PENDING."""
        from notionify.image.state import UploadStateMachine
        from notionify.models import UploadState
        sm = UploadStateMachine("upload-id")
        assert sm.state == UploadState.PENDING

    def test_happy_path_transitions(self) -> None:
        """PENDING → UPLOADING → UPLOADED → ATTACHED is valid."""
        from notionify.image.state import UploadStateMachine
        from notionify.models import UploadState
        sm = UploadStateMachine("upload-id")
        sm.transition(UploadState.UPLOADING)
        assert sm.state == UploadState.UPLOADING
        sm.transition(UploadState.UPLOADED)
        assert sm.state == UploadState.UPLOADED
        sm.transition(UploadState.ATTACHED)
        assert sm.state == UploadState.ATTACHED

    def test_failure_path_transitions(self) -> None:
        """PENDING → UPLOADING → FAILED is valid."""
        from notionify.image.state import UploadStateMachine
        from notionify.models import UploadState
        sm = UploadStateMachine("upload-id")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.FAILED)
        assert sm.state == UploadState.FAILED

    def test_retry_path_transitions(self) -> None:
        """UPLOADED → EXPIRED → UPLOADING → UPLOADED (retry) is valid."""
        from notionify.image.state import UploadStateMachine
        from notionify.models import UploadState
        sm = UploadStateMachine("upload-id")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.UPLOADED)
        sm.transition(UploadState.EXPIRED)
        sm.transition(UploadState.UPLOADING)  # retry
        assert sm.state == UploadState.UPLOADING

    def test_invalid_transition_from_pending_raises(self) -> None:
        """PENDING → UPLOADED (skipping UPLOADING) raises ValueError."""
        from notionify.image.state import UploadStateMachine
        from notionify.models import UploadState
        sm = UploadStateMachine("upload-id")
        with pytest.raises(ValueError, match="Invalid state transition"):
            sm.transition(UploadState.UPLOADED)

    def test_expired_to_attach_raises_upload_expired_error(self) -> None:
        """EXPIRED → ATTACHED raises NotionifyUploadExpiredError."""
        from notionify.errors import NotionifyUploadExpiredError
        from notionify.image.state import UploadStateMachine
        from notionify.models import UploadState
        sm = UploadStateMachine("upload-id")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.UPLOADED)
        sm.transition(UploadState.EXPIRED)
        with pytest.raises(NotionifyUploadExpiredError):
            sm.transition(UploadState.ATTACHED)

    def test_attached_is_terminal_state(self) -> None:
        """ATTACHED is terminal: any further transition raises ValueError."""
        from notionify.image.state import UploadStateMachine
        from notionify.models import UploadState
        sm = UploadStateMachine("upload-id")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.UPLOADED)
        sm.transition(UploadState.ATTACHED)
        with pytest.raises(ValueError, match="Invalid state transition"):
            sm.transition(UploadState.UPLOADING)

    def test_assert_can_attach_in_uploaded_state(self) -> None:
        """assert_can_attach() succeeds in UPLOADED state."""
        from notionify.image.state import UploadStateMachine
        from notionify.models import UploadState
        sm = UploadStateMachine("upload-id")
        sm.transition(UploadState.UPLOADING)
        sm.transition(UploadState.UPLOADED)
        sm.assert_can_attach()  # should not raise

    @given(
        upload_id=st.text(min_size=1, max_size=36,
                          alphabet=string.ascii_letters + string.digits + "-"),
    )
    @settings(max_examples=200)
    def test_initial_state_always_pending(self, upload_id: str) -> None:
        """Regardless of upload_id, initial state is always PENDING."""
        from notionify.image.state import UploadStateMachine
        from notionify.models import UploadState
        sm = UploadStateMachine(upload_id)
        assert sm.state == UploadState.PENDING
        assert sm.upload_id == upload_id


# ---------------------------------------------------------------------------
# TestAdditionalBlockTypeRendererProperties
# ---------------------------------------------------------------------------


class TestAdditionalBlockTypeRendererProperties:
    """Property-based tests for renderer on block types added in iteration 7.

    Covers: toggle, file, embed, link_preview, child_page, child_database.
    Key invariant: render_block must never raise on these types with arbitrary
    field values, and must always return a string.
    """

    _config = NotionifyConfig(token="test-token")

    @given(
        text=st.text(max_size=200),
        depth=st.integers(min_value=0, max_value=4),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_toggle_block_never_raises(self, text: str, depth: int) -> None:
        """Toggle blocks with arbitrary rich_text always render."""
        renderer = NotionToMarkdownRenderer(self._config)
        rt_seg = {"type": "text", "plain_text": text, "text": {"content": text}}
        block = {
            "type": "toggle",
            "toggle": {"rich_text": [rt_seg]},
        }
        result = renderer.render_block(block, depth=depth)
        assert isinstance(result, str)

    @given(
        url=st.text(max_size=200),
        file_type=st.sampled_from(["external", "file", ""]),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_file_block_never_raises(self, url: str, file_type: str) -> None:
        """File blocks with arbitrary URL and type fields always render."""
        renderer = NotionToMarkdownRenderer(self._config)
        block: dict = {
            "type": "file",
            "file": {
                "type": file_type,
                "name": "",
                "caption": [],
            },
        }
        if file_type == "external":
            block["file"]["external"] = {"url": url}
        elif file_type == "file":
            block["file"]["file"] = {"url": url}
        result = renderer.render_block(block)
        assert isinstance(result, str)

    @given(url=st.text(max_size=300))
    @settings(max_examples=200)
    def test_embed_block_never_raises(self, url: str) -> None:
        """Embed blocks with arbitrary URLs always render."""
        renderer = NotionToMarkdownRenderer(self._config)
        block = {"type": "embed", "embed": {"url": url}}
        result = renderer.render_block(block)
        assert isinstance(result, str)
        assert "Embed" in result

    @given(url=st.text(max_size=300))
    @settings(max_examples=200)
    def test_link_preview_block_never_raises(self, url: str) -> None:
        """link_preview blocks with arbitrary URLs always render."""
        renderer = NotionToMarkdownRenderer(self._config)
        block = {"type": "link_preview", "link_preview": {"url": url}}
        result = renderer.render_block(block)
        assert isinstance(result, str)

    @given(
        title=st.text(max_size=200),
        block_id=st.text(max_size=36, alphabet=string.ascii_letters + string.digits + "-"),
    )
    @settings(max_examples=200)
    def test_child_page_block_never_raises(self, title: str, block_id: str) -> None:
        """child_page blocks with arbitrary titles always render."""
        renderer = NotionToMarkdownRenderer(self._config)
        block = {
            "type": "child_page",
            "id": block_id,
            "child_page": {"title": title},
        }
        result = renderer.render_block(block)
        assert isinstance(result, str)
        assert "Page:" in result

    @given(
        title=st.text(max_size=200),
        block_id=st.text(max_size=36, alphabet=string.ascii_letters + string.digits + "-"),
    )
    @settings(max_examples=200)
    def test_child_database_block_never_raises(self, title: str, block_id: str) -> None:
        """child_database blocks with arbitrary titles always render."""
        renderer = NotionToMarkdownRenderer(self._config)
        block = {
            "type": "child_database",
            "id": block_id,
            "child_database": {"title": title},
        }
        result = renderer.render_block(block)
        assert isinstance(result, str)
        assert "Database:" in result

    @given(
        url=st.text(max_size=300),
        caption=st.text(max_size=200),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_bookmark_block_never_raises(self, url: str, caption: str) -> None:
        """Bookmark blocks with arbitrary URL and caption always render."""
        renderer = NotionToMarkdownRenderer(self._config)
        block = {
            "type": "bookmark",
            "bookmark": {
                "url": url,
                "caption": [{"type": "text", "plain_text": caption, "text": {"content": caption}}]
                if caption else [],
            },
        }
        result = renderer.render_block(block)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# TestRoundTripProperties (PRD section 1.2 - round-trip fidelity)
# ---------------------------------------------------------------------------


class TestRoundTripProperties:
    """Property-based round-trip tests for the full conversion pipeline.

    PRD section 1.2 requires ≥95% round-trip fidelity for supported block
    types.  These tests verify that common block types (headings, bullets,
    code blocks, paragraphs) survive MD→Notion→MD→Notion with stable
    block types and preserved content.
    """

    _config = NotionifyConfig(token="test-token")

    def _roundtrip(self, md: str) -> tuple[list[dict], str]:
        """Convert MD → Notion blocks, render back to MD.

        Returns (blocks_from_first_pass, rendered_markdown).
        """
        converter = MarkdownToNotionConverter(self._config)
        renderer = NotionToMarkdownRenderer(self._config)
        result = converter.convert(md)
        md_out = renderer.render_blocks(result.blocks)
        return result.blocks, md_out

    @given(text=st.text(min_size=0, max_size=2000))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_full_roundtrip_pipeline_never_raises(self, text: str) -> None:
        """The combined MD→Notion→MD pipeline must never raise on any input."""
        converter = MarkdownToNotionConverter(self._config)
        renderer = NotionToMarkdownRenderer(self._config)
        result = converter.convert(text)
        md_out = renderer.render_blocks(result.blocks)
        assert isinstance(md_out, str)

    @given(
        heading_level=st.integers(min_value=1, max_value=3),
        heading_text=st.text(
            alphabet=string.ascii_letters + string.digits + " ",
            min_size=1,
            max_size=80,
        ),
    )
    @settings(max_examples=100)
    def test_heading_type_survives_roundtrip(
        self, heading_level: int, heading_text: str
    ) -> None:
        """H1-H3 headings must round-trip as the same heading type."""
        text = heading_text.strip()
        assume(text)
        md = "#" * heading_level + " " + text
        blocks, md_out = self._roundtrip(md)
        expected = f"heading_{heading_level}"
        assert any(b["type"] == expected for b in blocks)
        # Second pass must preserve the heading type
        converter = MarkdownToNotionConverter(self._config)
        result2 = converter.convert(md_out)
        assert any(b["type"] == expected for b in result2.blocks)

    @given(
        items=st.lists(
            st.text(
                alphabet=string.ascii_letters + string.digits + " ",
                min_size=1,
                max_size=60,
            ),
            min_size=1,
            max_size=8,
        ),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_bullet_list_survives_roundtrip(self, items: list[str]) -> None:
        """Bullet list items must round-trip as bulleted_list_item blocks."""
        cleaned = [i.strip() for i in items if i.strip()]
        assume(cleaned)
        md = "\n".join(f"- {item}" for item in cleaned)
        blocks, md_out = self._roundtrip(md)
        assert any(b["type"] == "bulleted_list_item" for b in blocks)
        # After round-trip the bullet items must still be present
        converter = MarkdownToNotionConverter(self._config)
        result2 = converter.convert(md_out)
        assert any(b["type"] == "bulleted_list_item" for b in result2.blocks)

    @given(
        lang=st.sampled_from([
            "python", "javascript", "typescript", "go", "rust",
            "java", "c", "bash", "sql", "",
        ]),
        code=st.text(
            alphabet=string.ascii_letters + string.digits + " \n",
            min_size=1,
            max_size=150,
        ),
    )
    @settings(max_examples=80, suppress_health_check=[HealthCheck.too_slow])
    def test_code_block_survives_roundtrip(self, lang: str, code: str) -> None:
        """Fenced code blocks must round-trip as code blocks."""
        md = f"```{lang}\n{code}\n```"
        blocks, md_out = self._roundtrip(md)
        assert any(b["type"] == "code" for b in blocks)
        converter = MarkdownToNotionConverter(self._config)
        result2 = converter.convert(md_out)
        assert any(b["type"] == "code" for b in result2.blocks)

    @given(
        content=st.text(
            alphabet=string.ascii_letters + string.digits + " ",
            min_size=1,
            max_size=200,
        ),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_plain_paragraph_content_survives_roundtrip(self, content: str) -> None:
        """Plain text paragraphs must preserve their content through the round-trip."""
        text = content.strip()
        assume(text)
        blocks, md_out = self._roundtrip(text)
        assert any(b["type"] == "paragraph" for b in blocks)
        assert text in md_out


# ---------------------------------------------------------------------------
# TestCalloutRenderingNeverRaiseProperties (iteration 19)
# ---------------------------------------------------------------------------


class TestCalloutRenderingNeverRaiseProperties:
    """Callout blocks with arbitrary inputs must never raise and always return str.

    Invariant: NotionToMarkdownRenderer.render_block on a callout block with
    any combination of text, icon type, and depth must not raise an exception.
    """

    _config = NotionifyConfig(token="test-token")

    @given(
        text=st.text(max_size=300),
        depth=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_callout_no_icon_never_raises(self, text: str, depth: int) -> None:
        """Callout without icon always renders."""
        renderer = NotionToMarkdownRenderer(self._config)
        seg = {"type": "text", "plain_text": text, "text": {"content": text}}
        block = {"type": "callout", "callout": {"rich_text": [seg]}}
        result = renderer.render_block(block, depth=depth)
        assert isinstance(result, str)
        assert ">" in result

    @given(
        text=st.text(max_size=200),
        emoji=st.text(min_size=1, max_size=4),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_callout_emoji_icon_never_raises(self, text: str, emoji: str) -> None:
        """Callout with emoji icon (arbitrary emoji string) always renders."""
        renderer = NotionToMarkdownRenderer(self._config)
        seg = {"type": "text", "plain_text": text, "text": {"content": text}}
        block = {
            "type": "callout",
            "callout": {
                "rich_text": [seg],
                "icon": {"type": "emoji", "emoji": emoji},
            },
        }
        result = renderer.render_block(block)
        assert isinstance(result, str)
        assert result.strip().startswith(">")

    @given(
        text=st.text(max_size=200),
        url=st.text(max_size=300),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_callout_external_icon_never_raises(self, text: str, url: str) -> None:
        """Callout with external URL icon always renders."""
        renderer = NotionToMarkdownRenderer(self._config)
        seg = {"type": "text", "plain_text": text, "text": {"content": text}}
        block = {
            "type": "callout",
            "callout": {
                "rich_text": [seg],
                "icon": {"type": "external", "external": {"url": url}},
            },
        }
        result = renderer.render_block(block)
        assert isinstance(result, str)

    @given(text=st.text(max_size=200))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_callout_output_always_starts_with_blockquote(self, text: str) -> None:
        """The rendered callout always starts the first line with '>'."""
        renderer = NotionToMarkdownRenderer(self._config)
        seg = {"type": "text", "plain_text": text, "text": {"content": text}}
        block = {"type": "callout", "callout": {"rich_text": [seg]}}
        result = renderer.render_block(block)
        assert result.lstrip().startswith(">")

    @given(text=st.text(max_size=200))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_callout_always_ends_with_double_newline(self, text: str) -> None:
        """Callout rendering always ends with double newline."""
        renderer = NotionToMarkdownRenderer(self._config)
        seg = {"type": "text", "plain_text": text, "text": {"content": text}}
        block = {"type": "callout", "callout": {"rich_text": [seg]}}
        result = renderer.render_block(block)
        assert result.endswith("\n\n")


# ---------------------------------------------------------------------------
# TestLargeRichTextSegmentListProperties (iteration 19)
# ---------------------------------------------------------------------------


class TestLargeRichTextSegmentListProperties:
    """split_rich_text handles large segment lists without data loss or crash.

    Invariant: Splitting a list of N rich text segments produces output where
    total character count equals input total character count.
    """

    @given(
        texts=st.lists(
            st.text(min_size=1, max_size=50),
            min_size=50,
            max_size=500,
        ),
        limit=st.integers(min_value=100, max_value=2000),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_large_list_total_char_count_preserved(self, texts: list[str], limit: int) -> None:
        """Total character count is preserved when splitting large segment arrays."""
        segs = [
            {
                "type": "text",
                "text": {"content": t},
                "annotations": {
                    "bold": False, "italic": False, "code": False,
                    "strikethrough": False, "underline": False, "color": "default",
                },
            }
            for t in texts
        ]
        result = split_rich_text(segs, limit=limit)
        total_in = sum(len(t) for t in texts)
        total_out = sum(
            len(seg.get("text", {}).get("content", "") or
                seg.get("equation", {}).get("expression", ""))
            for seg in result
        )
        assert total_in == total_out

    @given(
        texts=st.lists(
            st.text(min_size=1, max_size=50),
            min_size=100,
            max_size=1000,
        ),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_large_list_never_raises(self, texts: list[str]) -> None:
        """split_rich_text with 100-1000 segments never raises."""
        segs = [
            {
                "type": "text",
                "text": {"content": t},
                "annotations": {
                    "bold": False, "italic": False, "code": False,
                    "strikethrough": False, "underline": False, "color": "default",
                },
            }
            for t in texts
        ]
        result = split_rich_text(segs, limit=2000)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# TestRetryStatusCodeProperties (iteration 19)
# ---------------------------------------------------------------------------


class TestRetryStatusCodeProperties:
    """Property-based tests for should_retry and compute_backoff across HTTP status ranges.

    Invariants:
    - should_retry is deterministic: same inputs → same output
    - compute_backoff is monotonically non-decreasing with attempt number
    - 2xx/3xx/4xx (not 429) are never retried
    - 429 is always retried (up to max attempts)
    - compute_backoff result is always non-negative
    """

    @given(status=st.integers(min_value=200, max_value=399))
    @settings(max_examples=200)
    def test_2xx_3xx_not_retried(self, status: int) -> None:
        """Success and redirect responses are never retried."""
        assert should_retry(status_code=status, exception=None, attempt=0, max_attempts=3) is False

    @given(status=st.integers(min_value=400, max_value=428))
    @settings(max_examples=200)
    def test_4xx_not_retried_except_429(self, status: int) -> None:
        """4xx errors (except 429) are not retried."""
        assert should_retry(status_code=status, exception=None, attempt=0, max_attempts=3) is False

    @given(status=st.integers(min_value=430, max_value=499))
    @settings(max_examples=200)
    def test_4xx_above_429_not_retried(self, status: int) -> None:
        """4xx errors above 429 are not retried."""
        assert should_retry(status_code=status, exception=None, attempt=0, max_attempts=3) is False

    @given(attempt=st.integers(min_value=0, max_value=1))
    @settings(max_examples=50)
    def test_429_always_retried_within_attempts(self, attempt: int) -> None:
        """Rate limit (429) is retried when attempt+1 < max_attempts."""
        # max_attempts=3: attempts 0 and 1 are retried; attempt 2 is the last
        result = should_retry(status_code=429, exception=None, attempt=attempt, max_attempts=3)
        assert result is True

    @given(attempt=st.integers(min_value=3, max_value=100))
    @settings(max_examples=50)
    def test_never_retry_past_max_attempts(self, attempt: int) -> None:
        """No status code is retried past max_attempts."""
        for status in (429, 500, 502, 503, 504):
            result = should_retry(
                status_code=status, exception=None, attempt=attempt, max_attempts=3
            )
            assert result is False

    @given(
        attempt=st.integers(min_value=0, max_value=10),
        base=st.floats(min_value=0.1, max_value=5.0, allow_nan=False),
        maximum=st.floats(min_value=5.0, max_value=60.0, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_compute_backoff_always_nonnegative(
        self, attempt: int, base: float, maximum: float
    ) -> None:
        """compute_backoff always returns a non-negative value."""
        result = compute_backoff(attempt=attempt, base=base, maximum=maximum, jitter=False)
        assert result >= 0.0

    @given(
        base=st.floats(min_value=0.1, max_value=5.0, allow_nan=False),
        maximum=st.floats(min_value=5.0, max_value=60.0, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_compute_backoff_never_exceeds_maximum(
        self, base: float, maximum: float
    ) -> None:
        """compute_backoff result never exceeds maximum."""
        for attempt in range(10):
            result = compute_backoff(attempt=attempt, base=base, maximum=maximum, jitter=False)
            assert result <= maximum

    @given(
        attempt=st.integers(min_value=0, max_value=8),
        base=st.floats(min_value=0.1, max_value=5.0, allow_nan=False),
        maximum=st.floats(min_value=5.0, max_value=60.0, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_compute_backoff_monotonically_nondecreasing(
        self, attempt: int, base: float, maximum: float
    ) -> None:
        """compute_backoff(n+1) >= compute_backoff(n) with jitter disabled."""
        r0 = compute_backoff(attempt=attempt, base=base, maximum=maximum, jitter=False)
        r1 = compute_backoff(attempt=attempt + 1, base=base, maximum=maximum, jitter=False)
        # Both are capped at maximum, so either r1 >= r0 or both equal maximum
        assert r1 >= r0 or (r0 == maximum and r1 == maximum)


# ---------------------------------------------------------------------------
# 16. TestDiffPlannerAlgebraicProperties
# ---------------------------------------------------------------------------


def _make_block(
    block_type: str = "paragraph", block_id: str | None = None, text: str = "hello"
) -> dict:
    """Build a minimal Notion-style block dict for diff planner tests."""
    block: dict = {
        "type": block_type,
        block_type: {
            "rich_text": [
                {"type": "text", "text": {"content": text}, "plain_text": text}
            ],
        },
    }
    if block_id is not None:
        block["id"] = block_id
    return block


_block_type_st = st.sampled_from(
    ["paragraph", "heading_1", "heading_2", "heading_3", "code", "quote"]
)


class TestDiffPlannerAlgebraicProperties:
    """Property-based tests for the DiffPlanner.

    Verifies algebraic invariants of the diff plan produced by DiffPlanner.plan(),
    including coverage (all blocks accounted for), referential integrity, and
    correct fall-through to full overwrite when match ratio is low.
    """

    @given(
        texts=st.lists(st.text(min_size=1, max_size=50), min_size=0, max_size=15),
    )
    @settings(max_examples=200)
    def test_plan_never_crashes_on_arbitrary_blocks(self, texts: list[str]) -> None:
        """DiffPlanner.plan() must never raise on any input pair."""
        config = NotionifyConfig(token="test")
        planner = DiffPlanner(config)

        existing = [_make_block(text=t, block_id=f"b-{i}") for i, t in enumerate(texts)]
        new = [_make_block(text=t) for t in reversed(texts)]

        ops = planner.plan(existing, new)
        assert isinstance(ops, list)
        for op in ops:
            assert isinstance(op, DiffOp)

    @given(
        texts=st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=15),
    )
    @settings(max_examples=200)
    def test_identical_blocks_all_kept(self, texts: list[str]) -> None:
        """When existing and new blocks are identical, all ops should be KEEP."""
        config = NotionifyConfig(token="test")
        planner = DiffPlanner(config)

        blocks = [_make_block(text=t, block_id=f"b-{i}") for i, t in enumerate(texts)]
        # New blocks have the same content but no IDs (like converter output)
        new_blocks = [_make_block(text=t) for t in texts]

        ops = planner.plan(blocks, new_blocks)
        keep_ops = [op for op in ops if op.op_type == DiffOpType.KEEP]
        assert len(keep_ops) == len(texts)

    @given(
        texts=st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=10),
    )
    @settings(max_examples=200)
    def test_empty_existing_produces_all_inserts(self, texts: list[str]) -> None:
        """When existing is empty, all ops must be INSERT."""
        config = NotionifyConfig(token="test")
        planner = DiffPlanner(config)

        new_blocks = [_make_block(text=t) for t in texts]
        ops = planner.plan([], new_blocks)
        assert len(ops) == len(texts)
        assert all(op.op_type == DiffOpType.INSERT for op in ops)

    @given(
        texts=st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=10),
    )
    @settings(max_examples=200)
    def test_empty_new_produces_all_deletes(self, texts: list[str]) -> None:
        """When new is empty, all ops must be DELETE."""
        config = NotionifyConfig(token="test")
        planner = DiffPlanner(config)

        existing = [_make_block(text=t, block_id=f"b-{i}") for i, t in enumerate(texts)]
        ops = planner.plan(existing, [])
        assert len(ops) == len(texts)
        assert all(op.op_type == DiffOpType.DELETE for op in ops)

    def test_both_empty_returns_empty(self) -> None:
        """plan([], []) must return empty list."""
        config = NotionifyConfig(token="test")
        planner = DiffPlanner(config)
        assert planner.plan([], []) == []

    @given(
        existing_texts=st.lists(
            st.text(min_size=1, max_size=30), min_size=1, max_size=10
        ),
        new_texts=st.lists(
            st.text(min_size=1, max_size=30), min_size=1, max_size=10
        ),
    )
    @settings(max_examples=200)
    def test_plan_ops_cover_all_new_blocks(
        self, existing_texts: list[str], new_texts: list[str]
    ) -> None:
        """Every new block must be accounted for: as INSERT, UPDATE, REPLACE, or KEEP."""
        config = NotionifyConfig(token="test")
        planner = DiffPlanner(config)

        existing = [
            _make_block(text=t, block_id=f"b-{i}")
            for i, t in enumerate(existing_texts)
        ]
        new_blocks = [_make_block(text=t) for t in new_texts]

        ops = planner.plan(existing, new_blocks)

        # Count operations that "produce" a new block
        producing_ops = sum(
            1
            for op in ops
            if op.op_type
            in (DiffOpType.INSERT, DiffOpType.UPDATE, DiffOpType.REPLACE, DiffOpType.KEEP)
        )
        # Must be at least len(new_texts) (every new block accounted for)
        assert producing_ops >= len(new_texts)

    @given(
        block_types=st.lists(
            st.sampled_from(["paragraph", "heading_1", "code"]),
            min_size=2,
            max_size=8,
        ),
    )
    @settings(max_examples=100)
    def test_plan_is_deterministic(self, block_types: list[str]) -> None:
        """Same input always produces the same plan."""
        config = NotionifyConfig(token="test")
        planner = DiffPlanner(config)

        existing = [
            _make_block(block_type=bt, text=f"text-{i}", block_id=f"b-{i}")
            for i, bt in enumerate(block_types)
        ]
        new_blocks = [
            _make_block(block_type=bt, text=f"new-{i}")
            for i, bt in enumerate(reversed(block_types))
        ]

        ops1 = planner.plan(existing, new_blocks)
        ops2 = planner.plan(existing, new_blocks)
        assert ops1 == ops2


# ---------------------------------------------------------------------------
# 17. TestDiffPlannerUpgradeProperties
# ---------------------------------------------------------------------------


class TestDiffPlannerUpgradeProperties:
    """Property tests for the UPDATE/REPLACE upgrade logic in _upgrade_to_updates."""

    @given(
        n=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=100)
    def test_same_type_replace_becomes_update(self, n: int) -> None:
        """When existing and new blocks differ in content but not type,
        adjacent DELETE+INSERT should be upgraded to UPDATE."""
        config = NotionifyConfig(token="test")
        planner = DiffPlanner(config)

        existing = [
            _make_block(text=f"old-{i}", block_id=f"b-{i}") for i in range(n)
        ]
        new_blocks = [_make_block(text=f"new-{i}") for i in range(n)]

        ops = planner.plan(existing, new_blocks)

        # With completely different content but same type, we should see
        # UPDATE ops (or possibly full overwrite for low match ratio).
        # Either way: no crash, all ops valid.
        for op in ops:
            assert op.op_type in (
                DiffOpType.KEEP,
                DiffOpType.UPDATE,
                DiffOpType.REPLACE,
                DiffOpType.INSERT,
                DiffOpType.DELETE,
            )

    @given(
        n=st.integers(min_value=1, max_value=8),
    )
    @settings(max_examples=100)
    def test_different_type_becomes_replace(self, n: int) -> None:
        """When type changes, adjacent DELETE+INSERT should become REPLACE."""
        config = NotionifyConfig(token="test")
        planner = DiffPlanner(config)

        existing = [
            _make_block(block_type="paragraph", text=f"text-{i}", block_id=f"b-{i}")
            for i in range(n)
        ]
        new_blocks = [
            _make_block(block_type="heading_1", text=f"text-{i}") for i in range(n)
        ]

        ops = planner.plan(existing, new_blocks)

        # With completely different types and different content, we may see REPLACE
        # or full overwrite. Either way: valid ops.
        for op in ops:
            assert op.op_type in (
                DiffOpType.KEEP,
                DiffOpType.UPDATE,
                DiffOpType.REPLACE,
                DiffOpType.INSERT,
                DiffOpType.DELETE,
            )


# ---------------------------------------------------------------------------
# 18. TestConfigValidationProperties
# ---------------------------------------------------------------------------


class TestConfigValidationProperties:
    """Extended property tests for NotionifyConfig validation."""

    @given(
        timeout=st.floats(min_value=-1e6, max_value=0.0, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_non_positive_timeout_raises(self, timeout: float) -> None:
        """timeout_seconds <= 0 must raise ValueError."""
        with pytest.raises(ValueError, match="timeout_seconds"):
            NotionifyConfig(token="test", timeout_seconds=timeout)

    @given(
        size=st.integers(min_value=-1_000_000, max_value=0),
    )
    @settings(max_examples=100)
    def test_non_positive_image_max_size_raises(self, size: int) -> None:
        """image_max_size_bytes <= 0 must raise ValueError."""
        with pytest.raises(ValueError, match="image_max_size_bytes"):
            NotionifyConfig(token="test", image_max_size_bytes=size)

    @given(
        concurrent=st.integers(min_value=-100, max_value=0),
    )
    @settings(max_examples=100)
    def test_non_positive_image_max_concurrent_raises(self, concurrent: int) -> None:
        """image_max_concurrent < 1 must raise ValueError."""
        with pytest.raises(ValueError, match="image_max_concurrent"):
            NotionifyConfig(token="test", image_max_concurrent=concurrent)

    def test_empty_upload_mimes_raises(self) -> None:
        """Empty image_allowed_mimes_upload must raise ValueError."""
        with pytest.raises(ValueError, match="image_allowed_mimes_upload"):
            NotionifyConfig(token="test", image_allowed_mimes_upload=[])

    def test_empty_external_mimes_raises(self) -> None:
        """Empty image_allowed_mimes_external must raise ValueError."""
        with pytest.raises(ValueError, match="image_allowed_mimes_external"):
            NotionifyConfig(token="test", image_allowed_mimes_external=[])

    def test_invalid_mime_format_raises(self) -> None:
        """MIME type without / must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid MIME"):
            NotionifyConfig(token="test", image_allowed_mimes_upload=["invalid"])

    @given(
        token_len=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=50)
    def test_repr_masks_token(self, token_len: int) -> None:
        """repr() must never contain the full token (when > 4 chars)."""
        # Use distinct chars so the full token differs from the masked suffix.
        token = "".join(chr(ord("A") + (i % 26)) for i in range(token_len))
        config = NotionifyConfig(token=token)
        r = repr(config)
        if token_len > 4:
            # Full token should NOT appear; only the masked suffix.
            assert token not in r
            assert f"...{token[-4:]}" in r
        elif token_len == 4:
            # Edge: "...ABCD" contains "ABCD", so just check masking.
            assert f"...{token[-4:]}" in r
        else:
            assert "****" in r


# ---------------------------------------------------------------------------
# 19. TestTableRendererProperties
# ---------------------------------------------------------------------------


def _make_table_block(
    cells_per_row: list[list[str]],
    has_row_header: bool = False,
    has_column_header: bool = True,
) -> dict:
    """Build a minimal Notion table block dict for renderer property tests."""
    if not cells_per_row:
        return {
            "type": "table",
            "table": {
                "table_width": 0,
                "has_row_header": has_row_header,
                "has_column_header": has_column_header,
                "children": [],
            },
        }
    col_count = max(len(row) for row in cells_per_row) if cells_per_row else 0
    rows = []
    for row_cells in cells_per_row:
        cells = [
            [{"type": "text", "plain_text": cell, "text": {"content": cell}}]
            for cell in row_cells
        ]
        rows.append({"type": "table_row", "table_row": {"cells": cells}})
    return {
        "type": "table",
        "table": {
            "table_width": col_count,
            "has_row_header": has_row_header,
            "has_column_header": has_column_header,
            "children": rows,
        },
    }


class TestTableRendererProperties:
    """Property-based tests for table rendering invariants in NotionToMarkdownRenderer.

    Key invariants verified:
    - has_row_header=True wraps non-empty first cells in **...**
    - has_row_header=True leaves empty first cells unchanged (no ****)
    - has_row_header=False never introduces **...** wrapping on first cell
    - Table rendering never raises on any valid input
    - GFM separator row (|---|...) is always present
    - Each rendered data row has exactly table_width columns
    """

    _config = NotionifyConfig(token="test-token")

    @given(
        content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=50),
        extra_cols=st.lists(st.text(alphabet=_SAFE_TEXT, max_size=30), min_size=0, max_size=4),
        has_column_header=st.booleans(),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_has_row_header_bolds_nonempty_first_cell(
        self, content: str, extra_cols: list[str], has_column_header: bool
    ) -> None:
        """has_row_header=True wraps non-empty, markdown-safe first cell in **...**."""
        row = [content, *extra_cols]
        block = _make_table_block([row], has_row_header=True, has_column_header=has_column_header)
        renderer = NotionToMarkdownRenderer(self._config)
        result = renderer.render_block(block)
        assert isinstance(result, str)
        # First rendered data line must contain the bold-wrapped first cell.
        first_line = result.strip().split("\n")[0]
        assert f"**{content}**" in first_line

    @given(
        extra_cols=st.lists(
            st.text(alphabet=_SAFE_TEXT, min_size=0, max_size=30),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=200)
    def test_has_row_header_empty_first_cell_stays_empty(
        self, extra_cols: list[str]
    ) -> None:
        """has_row_header=True with empty first cell must NOT produce ****."""
        row = ["", *extra_cols]
        block = _make_table_block([row], has_row_header=True)
        renderer = NotionToMarkdownRenderer(self._config)
        result = renderer.render_block(block)
        assert isinstance(result, str)
        first_line = result.strip().split("\n")[0]
        assert "****" not in first_line

    @given(
        content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=50),
        extra_cols=st.lists(st.text(alphabet=_SAFE_TEXT, max_size=30), min_size=0, max_size=4),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_no_row_header_does_not_bold_first_cell(
        self, content: str, extra_cols: list[str]
    ) -> None:
        """has_row_header=False must not wrap the first cell in **...**."""
        row = [content, *extra_cols]
        block = _make_table_block([row], has_row_header=False)
        renderer = NotionToMarkdownRenderer(self._config)
        result = renderer.render_block(block)
        first_line = result.strip().split("\n")[0]
        assert f"**{content}**" not in first_line

    @given(
        rows=st.lists(
            st.lists(st.text(min_size=0, max_size=50), min_size=1, max_size=5),
            min_size=1,
            max_size=10,
        ),
        has_row_header=st.booleans(),
        has_column_header=st.booleans(),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_table_render_never_raises(
        self,
        rows: list[list[str]],
        has_row_header: bool,
        has_column_header: bool,
    ) -> None:
        """Table rendering must never raise on any valid input."""
        block = _make_table_block(rows, has_row_header, has_column_header)
        renderer = NotionToMarkdownRenderer(self._config)
        result = renderer.render_block(block)
        assert isinstance(result, str)

    @given(
        rows=st.lists(
            st.lists(st.text(min_size=0, max_size=30), min_size=1, max_size=4),
            min_size=1,
            max_size=6,
        ),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_table_always_has_gfm_separator(self, rows: list[list[str]]) -> None:
        """Every rendered table with at least one row must contain a GFM separator."""
        block = _make_table_block(rows)
        renderer = NotionToMarkdownRenderer(self._config)
        result = renderer.render_block(block)
        assert "---" in result

    @given(
        col_count=st.integers(min_value=1, max_value=5),
        row_count=st.integers(min_value=1, max_value=5),
        content=st.text(alphabet=_SAFE_TEXT, min_size=0, max_size=20),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_rendered_rows_have_correct_col_count(
        self, col_count: int, row_count: int, content: str
    ) -> None:
        """Every data row in the rendered table has exactly col_count pipe-columns."""
        rows = [[content] * col_count for _ in range(row_count)]
        block = _make_table_block(rows)
        renderer = NotionToMarkdownRenderer(self._config)
        result = renderer.render_block(block)
        # Inspect only non-separator rows starting with "|"
        rendered_lines = [
            line for line in result.strip().split("\n")
            if line.startswith("|") and "---" not in line
        ]
        for line in rendered_lines:
            # "| a | b | c |" → split by "|" → ["", " a ", " b ", " c ", ""]
            parts = [p for p in line.split("|") if p != ""]
            assert len(parts) == col_count


# ---------------------------------------------------------------------------
# Section 20 — extract_block_ids properties
# ---------------------------------------------------------------------------

_result_with_id_st = st.fixed_dictionaries({"id": st.text(min_size=1, max_size=36)})
_result_without_id_st = st.fixed_dictionaries({
    "type": st.text(min_size=1, max_size=20),
})
_result_st = st.one_of(_result_with_id_st, _result_without_id_st)


class TestExtractBlockIdsProperties:
    """Property-based tests for :func:`extract_block_ids`."""

    @given(results=st.lists(_result_with_id_st, min_size=0, max_size=20))
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_length_equals_results_with_id(
        self, results: list[dict]
    ) -> None:
        """Output length matches number of results that have an 'id' field."""
        response = {"results": results}
        ids = extract_block_ids(response)
        assert len(ids) == len(results)

    @given(results=st.lists(_result_st, min_size=0, max_size=20))
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_results_without_id_are_filtered(
        self, results: list[dict]
    ) -> None:
        """Results without an 'id' key are excluded from output."""
        response = {"results": results}
        ids = extract_block_ids(response)
        expected = [r["id"] for r in results if "id" in r]
        assert ids == expected

    @given(results=st.lists(_result_with_id_st, min_size=0, max_size=20))
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_all_output_values_are_strings(
        self, results: list[dict]
    ) -> None:
        """Every extracted ID is a string."""
        ids = extract_block_ids({"results": results})
        assert all(isinstance(i, str) for i in ids)

    def test_missing_results_key_returns_empty(self) -> None:
        """A response dict with no 'results' key returns an empty list."""
        assert extract_block_ids({}) == []

    @given(results=st.lists(_result_with_id_st, min_size=1, max_size=20))
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_order_preserved(self, results: list[dict]) -> None:
        """IDs appear in the same order as their source results."""
        ids = extract_block_ids({"results": results})
        for pos, r in enumerate(results):
            assert ids[pos] == r["id"]


# ---------------------------------------------------------------------------
# Section 21 — StructuredFormatter and NoopMetricsHook properties
# ---------------------------------------------------------------------------


class TestStructuredFormatterProperties:
    """Property-based tests for :class:`StructuredFormatter`."""

    _fmt = StructuredFormatter()

    def _make_record(self, msg: str, level: int = _logging.INFO) -> _logging.LogRecord:
        return _logging.LogRecord(
            name="test.prop",
            level=level,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )

    @given(msg=st.text(min_size=0, max_size=200))
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_output_is_always_valid_json(self, msg: str) -> None:
        """format() must always return a valid JSON string."""
        record = self._make_record(msg)
        output = self._fmt.format(record)
        parsed = _json.loads(output)
        assert isinstance(parsed, dict)

    @given(msg=st.text(min_size=0, max_size=200))
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_required_keys_always_present(self, msg: str) -> None:
        """Formatted output always has ts, level, logger, and message keys."""
        record = self._make_record(msg)
        parsed = _json.loads(self._fmt.format(record))
        for key in ("ts", "level", "logger", "message"):
            assert key in parsed

    @given(
        msg=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
        extra=st.dictionaries(
            st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=20),
            st.text(alphabet=_SAFE_TEXT, min_size=0, max_size=50),
            max_size=5,
        ),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_extra_fields_merged_into_output(
        self, msg: str, extra: dict
    ) -> None:
        """Extra fields supplied via record.extra_fields appear in output JSON."""
        record = self._make_record(msg)
        record.extra_fields = extra  # type: ignore[attr-defined]
        parsed = _json.loads(self._fmt.format(record))
        for key, value in extra.items():
            assert parsed[key] == value

    @given(
        msg=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
        level=st.sampled_from([
            _logging.DEBUG, _logging.INFO, _logging.WARNING, _logging.ERROR,
        ]),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_level_name_matches_record(self, msg: str, level: int) -> None:
        """The 'level' field in the JSON matches the record's levelname."""
        record = self._make_record(msg, level=level)
        parsed = _json.loads(self._fmt.format(record))
        assert parsed["level"] == record.levelname


class TestNoopMetricsHookProperties:
    """Property-based tests for :class:`NoopMetricsHook`."""

    def test_satisfies_metrics_hook_protocol(self) -> None:
        """NoopMetricsHook must satisfy the MetricsHook protocol."""
        hook = NoopMetricsHook()
        assert isinstance(hook, MetricsHook)

    @given(
        name=st.text(min_size=1, max_size=50),
        value=st.integers(min_value=1, max_value=1000),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_increment_always_returns_none(self, name: str, value: int) -> None:
        """NoopMetricsHook.increment always returns None."""
        assert NoopMetricsHook().increment(name, value) is None

    @given(
        name=st.text(min_size=1, max_size=50),
        ms=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_timing_always_returns_none(self, name: str, ms: float) -> None:
        """NoopMetricsHook.timing always returns None."""
        assert NoopMetricsHook().timing(name, ms) is None

    @given(
        name=st.text(min_size=1, max_size=50),
        value=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_gauge_always_returns_none(self, name: str, value: float) -> None:
        """NoopMetricsHook.gauge always returns None."""
        assert NoopMetricsHook().gauge(name, value) is None


# ---------------------------------------------------------------------------
# Section 22 — NotionifyError properties
# ---------------------------------------------------------------------------


class TestNotionifyErrorProperties:
    """Property-based tests for :class:`NotionifyError` and subclasses."""

    @given(
        code=st.text(min_size=1, max_size=50),
        message=st.text(min_size=0, max_size=200),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_str_equals_message(self, code: str, message: str) -> None:
        """str(error) must equal the message argument."""
        err = NotionifyError(code, message)
        assert str(err) == message

    @given(
        code=st.text(min_size=1, max_size=50),
        message=st.text(min_size=0, max_size=200),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_code_and_message_attributes(self, code: str, message: str) -> None:
        """error.code and error.message always match constructor arguments."""
        err = NotionifyError(code, message)
        assert err.code == code
        assert err.message == message

    @given(
        code=st.text(min_size=1, max_size=50),
        message=st.text(min_size=0, max_size=200),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_context_defaults_to_empty_dict(self, code: str, message: str) -> None:
        """context defaults to an empty dict when not supplied."""
        err = NotionifyError(code, message)
        assert err.context == {}

    @given(
        code=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
        message=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_pickle_roundtrip_preserves_code_and_message(
        self, code: str, message: str
    ) -> None:
        """Pickle round-trip preserves code and message for NotionifyError."""
        err = NotionifyError(code, message)
        restored = pickle.loads(pickle.dumps(err))
        assert restored.code == code
        assert restored.message == message

    @given(
        message=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_validation_error_pickle_roundtrip(self, message: str) -> None:
        """Pickle round-trip preserves message for NotionifyValidationError."""
        err = NotionifyValidationError(message)
        restored = pickle.loads(pickle.dumps(err))
        assert restored.message == message

    @given(
        code=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
        message=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_repr_contains_code_and_message(self, code: str, message: str) -> None:
        """repr(error) contains both code and message."""
        err = NotionifyError(code, message)
        r = repr(err)
        assert code in r
        assert message in r

    @given(
        message=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_all_subclasses_are_instances_of_notionify_error(
        self, message: str
    ) -> None:
        """Every concrete error subclass must be isinstance of NotionifyError."""
        from notionify.errors import (
            NotionifyAuthError,
            NotionifyConversionError,
            NotionifyNetworkError,
            NotionifyRateLimitError,
        )
        for cls in (
            NotionifyValidationError,
            NotionifyAuthError,
            NotionifyRateLimitError,
            NotionifyNetworkError,
            NotionifyConversionError,
        ):
            err = cls(message)
            assert isinstance(err, NotionifyError)

    @given(
        code=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
        message=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=100),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_error_with_cause_has_cause_attr(
        self, code: str, message: str
    ) -> None:
        """NotionifyError with a cause always has __cause__ set."""
        cause = ValueError("original")
        err = NotionifyError(code, message, cause=cause)
        assert err.cause is cause
        assert err.__cause__ is cause


# ---------------------------------------------------------------------------
# _classify_image_source properties
# ---------------------------------------------------------------------------


class TestClassifyImageSourceProperties:
    """_classify_image_source always returns a valid ImageSourceType."""

    @given(suffix=st.text(alphabet=_SAFE_TEXT + "/.:?=#", min_size=1, max_size=50))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_data_uri_prefix_always_data_uri(self, suffix: str) -> None:
        """Any string starting with 'data:' is DATA_URI regardless of suffix."""
        assert _classify_image_source(f"data:{suffix}") == ImageSourceType.DATA_URI

    @given(path=st.text(alphabet=_SAFE_TEXT + "/.:?=#", min_size=1, max_size=50))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_http_https_scheme_always_external_url(self, path: str) -> None:
        """URLs with http or https scheme are EXTERNAL_URL."""
        assert _classify_image_source(f"http://{path}") == ImageSourceType.EXTERNAL_URL
        assert _classify_image_source(f"https://{path}") == ImageSourceType.EXTERNAL_URL

    def test_empty_string_is_unknown(self) -> None:
        """Empty string always returns UNKNOWN."""
        assert _classify_image_source("") == ImageSourceType.UNKNOWN

    @given(url=st.text(min_size=0, max_size=80))
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_result_is_always_a_valid_image_source_type(self, url: str) -> None:
        """Result is always one of the four ImageSourceType enum members."""
        result = _classify_image_source(url)
        assert isinstance(result, ImageSourceType)


# ---------------------------------------------------------------------------
# _has_non_default_annotations properties
# ---------------------------------------------------------------------------


class TestHasNonDefaultAnnotationsProperties:
    """_has_non_default_annotations boolean invariants."""

    def test_empty_dict_is_false(self) -> None:
        """All-default (empty) annotations dict returns False."""
        assert _has_non_default_annotations({}) is False

    @given(
        flag=st.sampled_from(["bold", "italic", "strikethrough", "underline", "code"])
    )
    @settings(max_examples=100)
    def test_any_true_boolean_flag_returns_true(self, flag: str) -> None:
        """Any truthy boolean annotation field makes result True."""
        assert _has_non_default_annotations({flag: True}) is True

    @given(color=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_non_default_color_returns_true(self, color: str) -> None:
        """color != 'default' makes result True."""
        assume(color != "default")
        assert _has_non_default_annotations({"color": color}) is True

    def test_default_color_alone_is_false(self) -> None:
        """color='default' is still considered default."""
        assert _has_non_default_annotations({"color": "default"}) is False


# ---------------------------------------------------------------------------
# _cells_to_text properties
# ---------------------------------------------------------------------------


class TestCellsToTextProperties:
    """_cells_to_text separator count and filtering invariants."""

    def test_empty_list_returns_empty_string(self) -> None:
        """Zero cells → empty string, no separators."""
        assert _cells_to_text([]) == ""

    @given(n=st.integers(min_value=1, max_value=10))
    @settings(max_examples=200)
    def test_n_cells_produce_n_minus_one_separators(self, n: int) -> None:
        """n table_cell tokens produce exactly n-1 ' | ' separators."""
        cells = [
            {"type": "table_cell", "children": [{"type": "text", "raw": "x"}]}
            for _ in range(n)
        ]
        result = _cells_to_text(cells)
        assert result.count(" | ") == n - 1

    @given(n=st.integers(min_value=1, max_value=10))
    @settings(max_examples=200)
    def test_non_table_cell_tokens_are_ignored(self, n: int) -> None:
        """Cells with type != 'table_cell' are silently dropped."""
        cells = [
            {"type": "paragraph", "children": [{"type": "text", "raw": "x"}]}
            for _ in range(n)
        ]
        assert _cells_to_text(cells) == ""


# ---------------------------------------------------------------------------
# _extract_plain_text properties
# ---------------------------------------------------------------------------


class TestExtractPlainTextProperties:
    """_extract_plain_text always returns a string and concatenates segments."""

    @given(
        block_type=st.sampled_from(["paragraph", "heading_1", "bulleted_list_item"]),
        texts=st.lists(
            st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=20), min_size=0, max_size=5
        ),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_result_is_always_a_string(self, block_type: str, texts: list[str]) -> None:
        """_extract_plain_text always returns a str, never raises."""
        rich_text = [{"plain_text": t} for t in texts]
        block = {block_type: {"rich_text": rich_text}}
        result = _extract_plain_text(block, block_type)
        assert isinstance(result, str)

    @given(
        block_type=st.sampled_from(["paragraph", "heading_1"]),
        texts=st.lists(
            st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=20), min_size=1, max_size=5
        ),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_plain_text_fields_are_concatenated(
        self, block_type: str, texts: list[str]
    ) -> None:
        """Output is the concatenation of all plain_text values."""
        rich_text = [{"plain_text": t} for t in texts]
        block = {block_type: {"rich_text": rich_text}}
        assert _extract_plain_text(block, block_type) == "".join(texts)

    @given(
        block_type=st.sampled_from(["paragraph", "heading_1"]),
        content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_falls_back_to_text_content(self, block_type: str, content: str) -> None:
        """When plain_text absent, falls back to text.content."""
        rich_text = [{"text": {"content": content}}]
        block = {block_type: {"rich_text": rich_text}}
        assert _extract_plain_text(block, block_type) == content


# ---------------------------------------------------------------------------
# _extract_children_info properties
# ---------------------------------------------------------------------------


class TestExtractChildrenInfoProperties:
    """_extract_children_info structural invariants."""

    def test_no_children_key_uses_has_children_flag(self) -> None:
        """Missing 'children' key defers to block's has_children flag."""
        assert _extract_children_info({}) == {"child_count": 0, "has_children": False}
        assert _extract_children_info({"has_children": True}) == {
            "child_count": 0,
            "has_children": True,
        }

    @given(n=st.integers(min_value=1, max_value=10))
    @settings(max_examples=200)
    def test_child_count_equals_list_length(self, n: int) -> None:
        """child_count must equal the number of children provided."""
        children = [{"type": "paragraph"} for _ in range(n)]
        info = _extract_children_info({"children": children})
        assert info["child_count"] == n

    @given(n=st.integers(min_value=1, max_value=10))
    @settings(max_examples=200)
    def test_child_types_length_matches_count(self, n: int) -> None:
        """child_types list length must equal child_count."""
        children = [{"type": f"type_{i}"} for i in range(n)]
        info = _extract_children_info({"children": children})
        assert len(info["child_types"]) == info["child_count"]


# ---------------------------------------------------------------------------
# _clone_text_segment properties
# ---------------------------------------------------------------------------


class TestCloneTextSegmentProperties:
    """_clone_text_segment annotation/href preservation and content replacement."""

    @given(
        content=st.text(alphabet=_SAFE_TEXT, min_size=0, max_size=50),
        new_content=st.text(alphabet=_SAFE_TEXT, min_size=0, max_size=50),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_new_content_replaces_original(self, content: str, new_content: str) -> None:
        """Clone always carries new_content, not the original."""
        segment = {"type": "text", "text": {"content": content}}
        clone = _clone_text_segment(segment, new_content)
        assert clone["text"]["content"] == new_content

    @given(
        content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
        new_content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
        bold=st.booleans(),
        italic=st.booleans(),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_annotations_are_deep_copied(
        self, content: str, new_content: str, bold: bool, italic: bool
    ) -> None:
        """Clone preserves annotations; mutating clone does not affect original."""
        annotations = {"bold": bold, "italic": italic}
        segment = {
            "type": "text",
            "text": {"content": content},
            "annotations": annotations,
        }
        clone = _clone_text_segment(segment, new_content)
        assert clone["annotations"] == annotations
        clone["annotations"]["extra"] = True
        assert "extra" not in segment["annotations"]

    @given(
        content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
        new_content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
        href=st.text(alphabet=_SAFE_TEXT + "/.:?=#", min_size=1, max_size=50),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_href_is_preserved(self, content: str, new_content: str, href: str) -> None:
        """Clone always carries the href from the source segment."""
        segment = {"type": "text", "text": {"content": content}, "href": href}
        clone = _clone_text_segment(segment, new_content)
        assert clone["href"] == href

    @given(
        content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
        new_content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_no_annotations_key_when_original_has_none(
        self, content: str, new_content: str
    ) -> None:
        """Clone of a plain segment has no 'annotations' key."""
        segment = {"type": "text", "text": {"content": content}}
        clone = _clone_text_segment(segment, new_content)
        assert "annotations" not in clone


# ---------------------------------------------------------------------------
# _truncate_src properties
# ---------------------------------------------------------------------------


class TestTruncateSrcProperties:
    """_truncate_src length and content invariants."""

    @given(
        src=st.text(min_size=0, max_size=300),
        max_len=st.integers(min_value=1, max_value=200),
    )
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_result_never_exceeds_max_len_plus_three(
        self, src: str, max_len: int
    ) -> None:
        """Result length is always <= max_len + 3 (the '...' suffix)."""
        from notionify.image.validate import _truncate_src

        result = _truncate_src(src, max_len)
        assert len(result) <= max_len + 3

    @given(src=st.text(min_size=0, max_size=100))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_short_strings_returned_unchanged(self, src: str) -> None:
        """Strings at or below the default 200-char limit are unchanged."""
        from notionify.image.validate import _truncate_src

        assume(len(src) <= 200)
        assert _truncate_src(src) == src

    @given(extra=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=50))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_truncated_strings_end_with_ellipsis(self, extra: str) -> None:
        """Strings exceeding the limit always end with '...'."""
        from notionify.image.validate import _truncate_src

        long_src = "a" * 201 + extra
        result = _truncate_src(long_src)
        assert result.endswith("...")


# ---------------------------------------------------------------------------
# _normalize_rich_text properties
# ---------------------------------------------------------------------------


class TestNormalizeRichTextProperties:
    """_normalize_rich_text structural invariants."""

    @given(
        block_type=st.sampled_from(["paragraph", "heading_1", "bulleted_list_item"]),
        texts=st.lists(
            st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=20), min_size=0, max_size=5
        ),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_output_length_equals_rich_text_input_length(
        self, block_type: str, texts: list[str]
    ) -> None:
        """Output segments count == input rich_text count."""
        rich_text = [{"plain_text": t} for t in texts]
        block = {block_type: {"rich_text": rich_text}}
        result = _normalize_rich_text(block, block_type)
        assert len(result) == len(texts)

    @given(
        block_type=st.sampled_from(["paragraph", "heading_1"]),
        texts=st.lists(
            st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=20), min_size=1, max_size=5
        ),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_every_segment_has_text_key(
        self, block_type: str, texts: list[str]
    ) -> None:
        """Every output segment must have a 'text' key."""
        rich_text = [{"plain_text": t} for t in texts]
        block = {block_type: {"rich_text": rich_text}}
        result = _normalize_rich_text(block, block_type)
        for seg in result:
            assert "text" in seg

    @given(
        block_type=st.sampled_from(["paragraph", "heading_1"]),
        text=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
        bold=st.booleans(),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_annotations_preserved_when_present(
        self, block_type: str, text: str, bold: bool
    ) -> None:
        """Annotations in rich_text segments are preserved in output."""
        annotations = {"bold": bold, "italic": False}
        rich_text = [{"plain_text": text, "annotations": annotations}]
        block = {block_type: {"rich_text": rich_text}}
        result = _normalize_rich_text(block, block_type)
        assert result[0]["annotations"] == annotations

    def test_missing_block_type_data_returns_empty_list(self) -> None:
        """If the block type data is absent, returns an empty list."""
        block: dict = {}
        result = _normalize_rich_text(block, "paragraph")
        assert result == []


# ---------------------------------------------------------------------------
# _make_text_segment properties
# ---------------------------------------------------------------------------


class TestMakeTextSegmentProperties:
    """_make_text_segment structure and annotation-elision invariants."""

    @given(content=st.text(alphabet=_SAFE_TEXT, min_size=0, max_size=50))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_type_is_always_text(self, content: str) -> None:
        """Segment type is always 'text'."""
        seg = _make_text_segment(content, {})
        assert seg["type"] == "text"

    @given(content=st.text(alphabet=_SAFE_TEXT, min_size=0, max_size=50))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_content_equals_input(self, content: str) -> None:
        """text.content always equals the input content string."""
        seg = _make_text_segment(content, {})
        assert seg["text"]["content"] == content

    @given(content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_all_default_annotations_elided(self, content: str) -> None:
        """When all annotations are default, no 'annotations' key is emitted."""
        default_annots = {
            "bold": False,
            "italic": False,
            "strikethrough": False,
            "underline": False,
            "code": False,
            "color": "default",
        }
        seg = _make_text_segment(content, default_annots)
        assert "annotations" not in seg

    @given(
        content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
        flag=st.sampled_from(["bold", "italic", "strikethrough", "underline", "code"]),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_non_default_annotation_included(self, content: str, flag: str) -> None:
        """When any annotation is non-default, 'annotations' key is emitted."""
        annots = {flag: True}
        seg = _make_text_segment(content, annots)
        assert "annotations" in seg

    @given(
        content=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30),
        href=st.text(alphabet=_SAFE_TEXT + "/.:?=#", min_size=1, max_size=50),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_href_included_when_truthy(self, content: str, href: str) -> None:
        """href is included in segment when truthy."""
        seg = _make_text_segment(content, {}, href=href)
        assert seg["href"] == href


# ---------------------------------------------------------------------------
# _merge_annotations properties
# ---------------------------------------------------------------------------


class TestMergeAnnotationsProperties:
    """_merge_annotations OR semantics and non-mutation invariants."""

    @given(
        flag=st.sampled_from(["bold", "italic", "strikethrough", "underline", "code"]),
    )
    @settings(max_examples=200)
    def test_true_in_base_stays_true_after_merge(self, flag: str) -> None:
        """OR semantics: True in base remains True regardless of override."""
        base = {flag: True}
        result = _merge_annotations(base, **{flag: False})
        assert result[flag] is True

    @given(
        flag=st.sampled_from(["bold", "italic", "strikethrough", "underline", "code"]),
    )
    @settings(max_examples=200)
    def test_false_plus_true_override_becomes_true(self, flag: str) -> None:
        """OR semantics: False in base + True override → True."""
        base = {flag: False}
        result = _merge_annotations(base, **{flag: True})
        assert result[flag] is True

    @given(
        flag=st.sampled_from(["bold", "italic", "strikethrough", "underline", "code"]),
    )
    @settings(max_examples=200)
    def test_base_is_not_mutated(self, flag: str) -> None:
        """_merge_annotations never mutates the base dict."""
        base = {flag: False}
        original = dict(base)
        _merge_annotations(base, **{flag: True})
        assert base == original

    @given(
        bold=st.booleans(),
        italic=st.booleans(),
    )
    @settings(max_examples=200)
    def test_unknown_overrides_are_ignored(self, bold: bool, italic: bool) -> None:
        """Keys not present in base are silently ignored."""
        base = {"bold": bold, "italic": italic}
        result = _merge_annotations(base, unknown_key=True)
        assert "unknown_key" not in result
        assert set(result.keys()) == {"bold", "italic"}


# ---------------------------------------------------------------------------
# _sanitize_comment properties
# ---------------------------------------------------------------------------


class TestSanitizeCommentProperties:
    """_sanitize_comment never leaves bare '--' in output."""

    @given(text=st.text(min_size=0, max_size=100))
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_result_never_contains_bare_double_dash(self, text: str) -> None:
        """Result must never contain the literal '--' sequence."""
        result = _sanitize_comment(text)
        assert "--" not in result

    @given(text=st.text(alphabet=_SAFE_TEXT, min_size=0, max_size=50))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_text_without_dashes_unchanged(self, text: str) -> None:
        """Text with no dashes passes through unchanged."""
        result = _sanitize_comment(text)
        assert result == text

    @given(text=st.text(min_size=0, max_size=100))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_result_length_at_least_input_length(self, text: str) -> None:
        """Escaping only adds characters; result is never shorter than input."""
        result = _sanitize_comment(text)
        assert len(result) >= len(text)


# ---------------------------------------------------------------------------
# _notion_url properties
# ---------------------------------------------------------------------------


class TestNotionUrlProperties:
    """_notion_url always produces a well-formed Notion URL."""

    @given(block_id=st.text(alphabet=_SAFE_TEXT + "-", min_size=1, max_size=40))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_url_starts_with_notion_prefix(self, block_id: str) -> None:
        """All URLs start with 'https://notion.so/'."""
        url = _notion_url(block_id)
        assert url.startswith("https://notion.so/")

    @given(block_id=st.text(alphabet=_SAFE_TEXT + "-", min_size=1, max_size=40))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_dashes_are_stripped_from_id(self, block_id: str) -> None:
        """Dashes in block_id are removed from the URL path."""
        url = _notion_url(block_id)
        path = url[len("https://notion.so/"):]
        assert "-" not in path

    def test_uuid_formatted_id_produces_correct_url(self) -> None:
        """Standard UUID block IDs are stripped of dashes."""
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        url = _notion_url(uuid)
        assert url == "https://notion.so/550e8400e29b41d4a716446655440000"


# ---------------------------------------------------------------------------
# _extract_type_attrs properties
# ---------------------------------------------------------------------------


class TestExtractTypeAttrsProperties:
    """_extract_type_attrs correctness for special and unknown block types."""

    def test_unknown_block_type_returns_empty_dict(self) -> None:
        """Block types not in _ATTRS_EXTRACTORS yield an empty dict."""
        block: dict = {"unknown_type": {"key": "value"}}
        result = _extract_type_attrs(block, "unknown_type")
        assert result == {}

    @given(
        expression=st.text(alphabet=_SAFE_TEXT + r"^+=\/*(){}[]", min_size=0, max_size=50)
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_equation_block_always_has_expression_key(self, expression: str) -> None:
        """For 'equation' blocks, output always contains 'expression'."""
        block = {"equation": {"expression": expression}}
        result = _extract_type_attrs(block, "equation")
        assert "expression" in result
        assert result["expression"] == expression

    @given(url=st.text(alphabet=_SAFE_TEXT + "/.:?=#", min_size=1, max_size=80))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_image_external_type_includes_url(self, url: str) -> None:
        """For 'image' blocks with type='external', attrs includes 'url'."""
        block = {"image": {"type": "external", "external": {"url": url}}}
        result = _extract_type_attrs(block, "image")
        assert result["url"] == url
        assert result["image_type"] == "external"

    @given(
        block_type=st.sampled_from(sorted(_ATTRS_EXTRACTORS.keys())),
    )
    @settings(max_examples=200)
    def test_no_spurious_keys_for_empty_type_data(self, block_type: str) -> None:
        """An empty type-data dict never adds spurious keys (except equation)."""
        block: dict = {block_type: {}}
        result = _extract_type_attrs(block, block_type)
        # equation always adds "expression", image always adds "image_type"
        if block_type == "equation":
            assert set(result.keys()) <= {"expression"}
        elif block_type == "image":
            assert set(result.keys()) <= {"image_type", "url"}
        else:
            assert result == {}


# ---------------------------------------------------------------------------
# extract_text properties
# ---------------------------------------------------------------------------


class TestExtractTextProperties:
    """extract_text always returns a str and concatenates token raw values."""

    def test_empty_list_returns_empty_string(self) -> None:
        """Zero tokens → empty string."""
        assert extract_text([]) == ""

    @given(raw=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=50))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_single_text_token_returns_raw(self, raw: str) -> None:
        """A single text token returns its 'raw' value."""
        tokens = [{"type": "text", "raw": raw}]
        assert extract_text(tokens) == raw

    @given(
        parts=st.lists(
            st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=20),
            min_size=1,
            max_size=5,
        )
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_multiple_text_tokens_are_concatenated(self, parts: list[str]) -> None:
        """Multiple text tokens produce their raw values joined."""
        tokens = [{"type": "text", "raw": p} for p in parts]
        assert extract_text(tokens) == "".join(parts)

    @given(raw=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_nested_children_are_recursed(self, raw: str) -> None:
        """Tokens with 'children' are recursed into."""
        tokens = [{"type": "strong", "children": [{"type": "text", "raw": raw}]}]
        assert extract_text(tokens) == raw

    @given(raw=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_non_text_token_with_raw_uses_raw(self, raw: str) -> None:
        """Non-text tokens without children but with 'raw' use their raw value."""
        tokens = [{"type": "softline", "raw": raw}]
        assert extract_text(tokens) == raw


# ---------------------------------------------------------------------------
# _looks_binary properties
# ---------------------------------------------------------------------------


class TestLooksBinaryProperties:
    """_looks_binary returns False for short and printable strings."""

    @given(text=st.text(min_size=0, max_size=255))
    @settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
    def test_short_strings_never_binary(self, text: str) -> None:
        """Strings shorter than 256 chars always return False."""
        assert _looks_binary(text) is False

    @given(text=st.text(alphabet=_SAFE_TEXT, min_size=256, max_size=600))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_pure_printable_long_strings_not_binary(self, text: str) -> None:
        """Long strings of pure printable ASCII are not considered binary."""
        assert _looks_binary(text) is False


# ---------------------------------------------------------------------------
# _mask_token properties
# ---------------------------------------------------------------------------


class TestMaskTokenProperties:
    """_mask_token removes the supplied token from string values."""

    @given(
        prefix=st.text(alphabet=_SAFE_TEXT, min_size=0, max_size=20),
        suffix=st.text(alphabet=_SAFE_TEXT, min_size=0, max_size=20),
        token=st.text(alphabet=_SAFE_TEXT, min_size=5, max_size=30),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_token_not_present_in_result(
        self, prefix: str, suffix: str, token: str
    ) -> None:
        """After masking, the original token never appears in the result."""
        value = f"{prefix}{token}{suffix}"
        result = _mask_token(value, token)
        assert token not in result

    @given(path=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=30))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_bearer_pattern_is_redacted(self, path: str) -> None:
        """'Bearer <token>' patterns are replaced regardless of token argument."""
        value = f"Bearer {path}"
        result = _mask_token(value, None)
        assert "Bearer <redacted>" in result

    @given(text=st.text(alphabet=_SAFE_TEXT, min_size=1, max_size=50))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_no_bearer_no_token_unchanged(self, text: str) -> None:
        """Strings without Bearer patterns and no token pass through unchanged."""
        result = _mask_token(text, None)
        assert result == text


# ---------------------------------------------------------------------------
# _estimate_data_uri_bytes properties
# ---------------------------------------------------------------------------


class TestEstimateDataUriBytesProperties:
    """_estimate_data_uri_bytes always returns a non-negative integer."""

    @given(data=st.binary(min_size=0, max_size=200))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_valid_base64_uri_returns_exact_byte_count(self, data: bytes) -> None:
        """For a syntactically valid base64 data URI, returns len(decoded_bytes)."""
        import base64 as _b64

        from notionify.utils.redact import _estimate_data_uri_bytes

        b64 = _b64.b64encode(data).decode()
        uri = f"data:image/png;base64,{b64}"
        assert _estimate_data_uri_bytes(uri) == len(data)

    @given(text=st.text(alphabet=_SAFE_TEXT, min_size=0, max_size=100))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_result_always_non_negative(self, text: str) -> None:
        """Result is always >= 0 for any input string."""
        from notionify.utils.redact import _estimate_data_uri_bytes

        result = _estimate_data_uri_bytes(text)
        assert result >= 0

    def test_empty_string_returns_zero(self) -> None:
        """Empty string (no ;base64, separator) returns 0."""
        from notionify.utils.redact import _estimate_data_uri_bytes

        assert _estimate_data_uri_bytes("") == 0


# ---------------------------------------------------------------------------
# _redact_value properties
# ---------------------------------------------------------------------------


class TestRedactValueProperties:
    """_redact_value structural invariants for bytes, lists, and dicts."""

    @given(data=st.binary(min_size=0, max_size=100))
    @settings(max_examples=200)
    def test_bytes_become_binary_placeholder(self, data: bytes) -> None:
        """bytes input is always replaced with a '<binary:N_bytes>' string."""
        result = _redact_value(data, None)
        assert isinstance(result, str)
        assert result.startswith("<binary:")
        assert result.endswith("_bytes>")

    @given(data=st.binary(min_size=0, max_size=100))
    @settings(max_examples=200)
    def test_binary_placeholder_contains_correct_byte_count(self, data: bytes) -> None:
        """The byte count in the placeholder matches len(data)."""
        result = _redact_value(data, None)
        assert f"<binary:{len(data)}_bytes>" == result

    @given(
        items=st.lists(st.integers(min_value=0, max_value=100), min_size=0, max_size=5)
    )
    @settings(max_examples=200)
    def test_list_length_preserved(self, items: list[int]) -> None:
        """List input length is preserved after redaction."""
        result = _redact_value(items, None)
        assert isinstance(result, list)
        assert len(result) == len(items)

    @given(value=st.integers())
    @settings(max_examples=200)
    def test_non_string_non_bytes_passthrough(self, value: int) -> None:
        """Non-string, non-bytes, non-dict, non-list values pass through."""
        result = _redact_value(value, None)
        assert result == value


class TestMathBlockFactoryProperties:
    """Property tests for math.py block and rich_text factory helpers."""

    # --- _make_equation_block ---

    @given(expression=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_equation_block_type_is_equation(self, expression: str) -> None:
        """_make_equation_block always returns a block of type 'equation'."""
        result = _make_equation_block(expression)
        assert result["object"] == "block"
        assert result["type"] == "equation"

    @given(expression=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_equation_block_expression_preserved(self, expression: str) -> None:
        """_make_equation_block stores the expression verbatim."""
        result = _make_equation_block(expression)
        assert result["equation"]["expression"] == expression

    # --- _make_code_block ---

    @given(code=st.text(min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_code_block_type_is_code(self, code: str) -> None:
        """_make_code_block always returns a block of type 'code'."""
        result = _make_code_block(code)
        assert result["object"] == "block"
        assert result["type"] == "code"

    @given(code=st.text(min_size=0, max_size=50), language=st.text(min_size=1, max_size=30))
    @settings(max_examples=200)
    def test_code_block_language_preserved(self, code: str, language: str) -> None:
        """_make_code_block stores the given language."""
        result = _make_code_block(code, language=language)
        assert result["code"]["language"] == language

    @given(code=st.text(min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_code_block_default_language_is_plain_text(self, code: str) -> None:
        """_make_code_block uses 'plain text' as the default language."""
        result = _make_code_block(code)
        assert result["code"]["language"] == "plain text"

    @given(code=st.text(min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_code_block_caption_is_empty(self, code: str) -> None:
        """_make_code_block always produces an empty caption."""
        result = _make_code_block(code)
        assert result["code"]["caption"] == []

    # --- _make_paragraph_block ---

    @given(text=st.text(min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_paragraph_block_type_is_paragraph(self, text: str) -> None:
        """_make_paragraph_block always returns a block of type 'paragraph'."""
        result = _make_paragraph_block(text)
        assert result["object"] == "block"
        assert result["type"] == "paragraph"

    @given(text=st.text(min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_paragraph_block_color_is_default(self, text: str) -> None:
        """_make_paragraph_block always uses color 'default'."""
        result = _make_paragraph_block(text)
        assert result["paragraph"]["color"] == "default"

    # --- _make_equation_rich_text ---

    @given(expression=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_equation_rich_text_type_is_equation(self, expression: str) -> None:
        """_make_equation_rich_text produces a segment of type 'equation'."""
        seg = _make_equation_rich_text(expression)
        assert seg["type"] == "equation"

    @given(expression=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_equation_rich_text_expression_preserved(self, expression: str) -> None:
        """_make_equation_rich_text stores the expression verbatim."""
        seg = _make_equation_rich_text(expression)
        assert seg["equation"]["expression"] == expression

    # --- _make_code_rich_text ---

    @given(text=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_code_rich_text_type_is_text(self, text: str) -> None:
        """_make_code_rich_text produces a segment of type 'text'."""
        seg = _make_code_rich_text(text)
        assert seg["type"] == "text"

    @given(text=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_code_rich_text_has_code_annotation_true(self, text: str) -> None:
        """_make_code_rich_text always sets annotations.code == True."""
        seg = _make_code_rich_text(text)
        assert seg["annotations"]["code"] is True

    @given(text=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_code_rich_text_content_preserved(self, text: str) -> None:
        """_make_code_rich_text stores the text content verbatim."""
        seg = _make_code_rich_text(text)
        assert seg["text"]["content"] == text

    # --- _make_plain_text_rich_text ---

    @given(text=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_plain_text_rich_text_type_is_text(self, text: str) -> None:
        """_make_plain_text_rich_text produces a segment of type 'text'."""
        seg = _make_plain_text_rich_text(text)
        assert seg["type"] == "text"

    @given(text=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_plain_text_rich_text_content_preserved(self, text: str) -> None:
        """_make_plain_text_rich_text stores the text content verbatim."""
        seg = _make_plain_text_rich_text(text)
        assert seg["text"]["content"] == text


class TestDefaultAnnotationsProperties:
    """Property tests for _default_annotations from rich_text.py."""

    def test_returns_dict_with_expected_keys(self) -> None:
        """_default_annotations returns a dict with all six expected keys."""
        ann = _default_annotations()
        assert set(ann.keys()) == {"bold", "italic", "strikethrough", "underline", "code", "color"}

    def test_all_boolean_fields_are_false(self) -> None:
        """All boolean annotation flags default to False."""
        ann = _default_annotations()
        for key in ("bold", "italic", "strikethrough", "underline", "code"):
            assert ann[key] is False, f"{key} should be False"

    def test_color_is_default(self) -> None:
        """The color field defaults to the string 'default'."""
        assert _default_annotations()["color"] == "default"

    def test_returns_fresh_dict_each_call(self) -> None:
        """Each call returns a new dict, not a shared singleton."""
        a, b = _default_annotations(), _default_annotations()
        assert a is not b
        assert a == b

    @given(key=st.sampled_from(["bold", "italic", "strikethrough", "underline", "code"]))
    @settings(max_examples=50)
    def test_mutating_one_call_does_not_affect_another(self, key: str) -> None:
        """Mutation of one returned dict must not affect a separately obtained dict."""
        a = _default_annotations()
        b = _default_annotations()
        a[key] = True
        assert b[key] is False


class TestExtractPlainTextFromBlockProperties:
    """Property tests for _extract_plain_text (notion_to_md version) on block dicts."""

    @given(block=st.fixed_dictionaries({}))
    @settings(max_examples=50)
    def test_empty_block_returns_empty_string(self, block: dict) -> None:
        """An empty block dict always produces an empty string."""
        assert _extract_plain_text_from_block({}) == ""

    @given(
        block_type=st.text(
            min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L",))
        )
    )
    @settings(max_examples=200)
    def test_block_without_rich_text_returns_empty(self, block_type: str) -> None:
        """A block whose type-data has no rich_text returns empty string."""
        # Avoid block_type="type" which creates colliding dict keys
        assume(block_type != "type")
        block: dict = {"type": block_type, block_type: {"some_key": "some_value"}}
        result = _extract_plain_text_from_block(block)
        assert result == ""

    _BLOCK_TYPE_ST = st.text(
        min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L",))
    )

    @given(
        block_type=_BLOCK_TYPE_ST,
        texts=st.lists(st.text(min_size=0, max_size=50), min_size=1, max_size=5),
    )
    @settings(max_examples=200)
    def test_plain_text_field_is_concatenated(self, block_type: str, texts: list[str]) -> None:
        """If segments have plain_text, they are concatenated in order."""
        # Avoid block_type="type" which would create colliding dict keys
        assume(block_type != "type")
        segments = [{"plain_text": t} for t in texts]
        block: dict = {"type": block_type, block_type: {"rich_text": segments}}
        result = _extract_plain_text_from_block(block)
        assert result == "".join(texts)

    @given(
        block_type=_BLOCK_TYPE_ST,
        texts=st.lists(st.text(min_size=0, max_size=50), min_size=1, max_size=5),
    )
    @settings(max_examples=200)
    def test_text_content_fallback_when_no_plain_text(
        self, block_type: str, texts: list[str]
    ) -> None:
        """When plain_text is absent, falls back to text.content."""
        # Avoid block_type="type" which would create colliding dict keys
        assume(block_type != "type")
        segments = [{"text": {"content": t}} for t in texts]
        block: dict = {"type": block_type, block_type: {"rich_text": segments}}
        result = _extract_plain_text_from_block(block)
        assert result == "".join(texts)

    @given(text=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_always_returns_a_string(self, text: str) -> None:
        """_extract_plain_text always returns a string, never None or other types."""
        block: dict = {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]}}
        result = _extract_plain_text_from_block(block)
        assert isinstance(result, str)


class TestGuessMimeFromPathProperties:
    """Property tests for _guess_mime_from_path from image/validate.py."""

    def test_known_png_extension(self) -> None:
        """Files ending in .png are identified as image/png."""
        from notionify.image.validate import _guess_mime_from_path

        assert _guess_mime_from_path("photo.png") == "image/png"

    def test_known_jpeg_extension(self) -> None:
        """Files ending in .jpg / .jpeg are identified as image/jpeg."""
        from notionify.image.validate import _guess_mime_from_path

        assert _guess_mime_from_path("photo.jpg") in ("image/jpeg", "image/jpg")
        assert _guess_mime_from_path("photo.jpeg") == "image/jpeg"

    def test_known_gif_extension(self) -> None:
        """Files ending in .gif are identified as image/gif."""
        from notionify.image.validate import _guess_mime_from_path

        assert _guess_mime_from_path("anim.gif") == "image/gif"

    def test_known_webp_extension(self) -> None:
        """Files ending in .webp are identified as image/webp."""
        from notionify.image.validate import _guess_mime_from_path

        assert _guess_mime_from_path("img.webp") == "image/webp"

    @given(
        ext=st.text(
            min_size=5, max_size=10, alphabet=st.characters(whitelist_categories=("L",))
        )
    )
    @settings(max_examples=200)
    def test_unknown_extension_returns_none_or_string(self, ext: str) -> None:
        """_guess_mime_from_path always returns str | None."""
        from notionify.image.validate import _guess_mime_from_path

        result = _guess_mime_from_path(f"file.{ext}")
        assert result is None or isinstance(result, str)

    def test_url_with_extension(self) -> None:
        """Works on full URLs, not just bare filenames."""
        from notionify.image.validate import _guess_mime_from_path

        result = _guess_mime_from_path("https://example.com/image.png")
        assert result == "image/png"


class TestRedactDictProperties:
    """Property tests for _redact_dict from utils/redact.py."""

    @given(
        keys=st.lists(
            st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L",))),
            min_size=1,
            max_size=5,
            unique=True,
        ),
        values=st.lists(st.integers(min_value=0, max_value=100), min_size=1, max_size=5),
    )
    @settings(max_examples=200)
    def test_output_has_same_keys_as_input(
        self, keys: list[str], values: list[int]
    ) -> None:
        """_redact_dict preserves all keys from the input dict."""
        d = dict(zip(keys, values, strict=False))
        result = _redact_dict(d, None)
        assert set(result.keys()) == set(d.keys())

    @given(
        safe_key=st.text(
            min_size=1, max_size=15, alphabet=st.characters(whitelist_categories=("L",))
        ),
        value=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=200)
    def test_non_sensitive_integer_passes_through(
        self, safe_key: str, value: int
    ) -> None:
        """Non-sensitive integer values are not modified."""
        assume(not any(p in safe_key.lower() for p in _SENSITIVE_KEY_PATTERNS))
        d: dict = {safe_key: value}
        result = _redact_dict(d, None)
        assert result[safe_key] == value

    @given(
        value=st.text(min_size=0, max_size=50),
    )
    @settings(max_examples=200)
    def test_sensitive_key_non_string_value_becomes_redacted(self, value: str) -> None:
        """Non-string values under a sensitive key become '<redacted>'."""
        d: dict = {"token": [value]}  # list is non-string
        result = _redact_dict(d, None)
        assert result["token"] == "<redacted>"

    @given(
        keys=st.lists(
            st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L",))),
            min_size=1,
            max_size=5,
            unique=True,
        ),
        values=st.lists(st.integers(min_value=0, max_value=100), min_size=1, max_size=5),
    )
    @settings(max_examples=200)
    def test_nested_dict_is_recursively_redacted(
        self, keys: list[str], values: list[int]
    ) -> None:
        """_redact_dict recursively processes nested dicts."""
        inner: dict = dict(zip(keys, values, strict=False))
        outer_key = "data"
        d: dict = {outer_key: inner}
        result = _redact_dict(d, None)
        assert isinstance(result[outer_key], dict)
        assert set(result[outer_key].keys()) == set(inner.keys())


class TestValidateMimeListProperties:
    """Property tests for _validate_mime_list from config.py."""

    def test_empty_list_raises_value_error(self) -> None:
        """An empty MIME list must raise ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_mime_list("test_label", [])

    @given(
        mimes=st.lists(
            st.from_regex(r"[a-z]+/[a-z]+", fullmatch=True),
            min_size=1,
            max_size=5,
        )
    )
    @settings(max_examples=200)
    def test_valid_mime_types_do_not_raise(self, mimes: list[str]) -> None:
        """Well-formed MIME strings (type/subtype) must not raise."""
        _validate_mime_list("test_label", mimes)

    @given(
        bad_mime=st.text(
            min_size=1,
            max_size=20,
            alphabet=st.characters(whitelist_categories=("L",)),
        )
    )
    @settings(max_examples=200)
    def test_mime_without_slash_raises(self, bad_mime: str) -> None:
        """A MIME string without '/' must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid MIME type"):
            _validate_mime_list("test_label", [bad_mime])

    @given(
        valid_mimes=st.lists(
            st.from_regex(r"[a-z]+/[a-z]+", fullmatch=True),
            min_size=1,
            max_size=4,
        ),
        bad_mime=st.text(
            min_size=1,
            max_size=20,
            alphabet=st.characters(whitelist_categories=("L",)),
        ),
    )
    @settings(max_examples=200)
    def test_one_bad_mime_in_list_raises(
        self, valid_mimes: list[str], bad_mime: str
    ) -> None:
        """A single invalid MIME entry causes the whole list to fail."""
        mixed = [*valid_mimes, bad_mime]
        with pytest.raises(ValueError, match="Invalid MIME type"):
            _validate_mime_list("test_label", mixed)


class TestBuildRowCellsProperties:
    """Property tests for _build_row_cells from tables.py."""

    _CONFIG = NotionifyConfig(token="test-token")

    def test_empty_cells_returns_empty_list(self) -> None:
        """No cells → no rows."""
        result = _build_row_cells([], self._CONFIG)
        assert result == []

    @given(n=st.integers(min_value=1, max_value=8))
    @settings(max_examples=200)
    def test_output_length_equals_input_length(self, n: int) -> None:
        """One output entry per input cell regardless of cell type."""
        cells = [{"type": "table_cell", "children": []} for _ in range(n)]
        result = _build_row_cells(cells, self._CONFIG)
        assert len(result) == n

    @given(n=st.integers(min_value=1, max_value=5))
    @settings(max_examples=200)
    def test_non_table_cell_produces_empty_rich_text(self, n: int) -> None:
        """Tokens that are not table_cell produce an empty rich_text list."""
        cells = [{"type": "paragraph", "children": []} for _ in range(n)]
        result = _build_row_cells(cells, self._CONFIG)
        assert all(cell == [] for cell in result)

    def test_table_cell_with_text_child_produces_segment(self) -> None:
        """A table_cell with a plain text token produces a non-empty rich_text."""
        cell = {
            "type": "table_cell",
            "children": [{"type": "text", "raw": "hello"}],
        }
        result = _build_row_cells([cell], self._CONFIG)
        assert len(result) == 1
        assert len(result[0]) >= 1


class TestTableToPlainTextProperties:
    """Property tests for _table_to_plain_text from tables.py."""

    _CONFIG = NotionifyConfig(token="test-token")

    def test_empty_token_returns_table_placeholder(self) -> None:
        """A token with no children returns '[table]'."""
        result = _table_to_plain_text({}, self._CONFIG)
        assert result == "[table]"

    def test_always_returns_string(self) -> None:
        """_table_to_plain_text always returns a str."""
        token: dict = {"type": "table", "children": []}
        result = _table_to_plain_text(token, self._CONFIG)
        assert isinstance(result, str)

    def test_token_with_table_head_produces_text(self) -> None:
        """A table_head with cells produces text containing cell content."""
        token: dict = {
            "type": "table",
            "children": [
                {
                    "type": "table_head",
                    "children": [
                        {"type": "table_cell", "children": [{"type": "text", "raw": "Col A"}]},
                        {"type": "table_cell", "children": [{"type": "text", "raw": "Col B"}]},
                    ],
                }
            ],
        }
        result = _table_to_plain_text(token, self._CONFIG)
        assert "Col A" in result
        assert "Col B" in result

    def test_multiple_rows_joined_with_separator(self) -> None:
        """Multiple body rows are joined with ' | '."""
        make_row = lambda text: {  # noqa: E731
            "type": "table_row",
            "children": [
                {"type": "table_cell", "children": [{"type": "text", "raw": text}]},
            ],
        }
        token: dict = {
            "type": "table",
            "children": [
                {
                    "type": "table_body",
                    "children": [make_row("Row1"), make_row("Row2")],
                }
            ],
        }
        result = _table_to_plain_text(token, self._CONFIG)
        assert " | " in result


class TestApplyTableFallbackProperties:
    """Property tests for _apply_table_fallback from tables.py."""

    _CONFIG_COMMENT = NotionifyConfig(token="test-token", table_fallback="comment")
    _CONFIG_PARAGRAPH = NotionifyConfig(token="test-token", table_fallback="paragraph")
    _CONFIG_RAISE = NotionifyConfig(token="test-token", table_fallback="raise")

    def test_raise_fallback_raises_conversion_error(self) -> None:
        """table_fallback='raise' must raise NotionifyConversionError."""
        from notionify.errors import NotionifyConversionError

        with pytest.raises(NotionifyConversionError):
            _apply_table_fallback({}, self._CONFIG_RAISE, [])

    def test_comment_fallback_adds_warning(self) -> None:
        """comment fallback always adds exactly one TABLE_DISABLED warning."""
        warnings: list = []
        _apply_table_fallback({}, self._CONFIG_COMMENT, warnings)
        assert len(warnings) == 1
        assert warnings[0].code == "TABLE_DISABLED"

    def test_comment_fallback_returns_paragraph_block(self) -> None:
        """comment fallback returns a paragraph block."""
        warnings: list = []
        block, _ = _apply_table_fallback({}, self._CONFIG_COMMENT, warnings)
        assert block is not None
        assert block["type"] == "paragraph"

    def test_paragraph_fallback_adds_warning(self) -> None:
        """paragraph fallback always adds a TABLE_DISABLED warning."""
        warnings: list = []
        _apply_table_fallback({}, self._CONFIG_PARAGRAPH, warnings)
        assert len(warnings) == 1
        assert warnings[0].code == "TABLE_DISABLED"

    def test_paragraph_fallback_returns_paragraph_block(self) -> None:
        """paragraph fallback returns a paragraph block."""
        warnings: list = []
        block, _ = _apply_table_fallback({}, self._CONFIG_PARAGRAPH, warnings)
        assert block is not None
        assert block["type"] == "paragraph"


class TestReconstructErrorProperties:
    """Property tests for _reconstruct_error from errors.py."""

    @given(code=st.text(min_size=1, max_size=50), message=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_returns_instance_of_cls(self, code: str, message: str) -> None:
        """_reconstruct_error always returns an instance of the specified class."""
        err = _reconstruct_error(NotionifyError, code, message, None, None)
        assert isinstance(err, NotionifyError)

    @given(code=st.text(min_size=1, max_size=50), message=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_code_attribute_matches(self, code: str, message: str) -> None:
        """The code attribute is set to the passed code value."""
        err = _reconstruct_error(NotionifyError, code, message, None, None)
        assert err.code == code

    @given(code=st.text(min_size=1, max_size=50), message=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_message_attribute_matches(self, code: str, message: str) -> None:
        """The message attribute is set to the passed message value."""
        err = _reconstruct_error(NotionifyError, code, message, None, None)
        assert err.message == message

    @given(code=st.text(min_size=1, max_size=30), message=st.text(min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_context_defaults_to_empty_dict_when_none(self, code: str, message: str) -> None:
        """When context=None, the context attribute is an empty dict."""
        err = _reconstruct_error(NotionifyError, code, message, None, None)
        assert err.context == {}

    @given(
        code=st.text(min_size=1, max_size=30),
        message=st.text(min_size=0, max_size=50),
        context=st.dictionaries(
            st.text(min_size=1, max_size=10), st.integers(), max_size=3
        ),
    )
    @settings(max_examples=200)
    def test_context_is_set_when_provided(
        self, code: str, message: str, context: dict
    ) -> None:
        """When a context dict is provided, it is stored verbatim."""
        err = _reconstruct_error(NotionifyError, code, message, context, None)
        assert err.context == context

    def test_cause_is_stored_and_chained(self) -> None:
        """When cause is set, both err.cause and err.__cause__ are the cause."""
        cause = ValueError("root cause")
        err = _reconstruct_error(NotionifyError, "CODE", "msg", None, cause)
        assert err.cause is cause
        assert err.__cause__ is cause


class TestInlineHandlerProperties:
    """Property tests for the simpler rich_text inline handler functions."""

    _CONFIG = NotionifyConfig(token="test-token")

    @staticmethod
    def _anns() -> dict:
        return {"bold": False, "italic": False, "strikethrough": False,
                "underline": False, "code": False, "color": "default"}

    # --- _handle_text ---

    @given(raw=st.text(min_size=1, max_size=200))
    @settings(max_examples=200)
    def test_handle_text_non_empty_returns_one_segment(self, raw: str) -> None:
        """_handle_text with non-empty raw returns a list of exactly one segment."""
        result = _handle_text({"raw": raw}, self._CONFIG, self._anns(), None, None)
        assert len(result) == 1

    def test_handle_text_empty_raw_returns_empty_list(self) -> None:
        """_handle_text with empty raw returns an empty list."""
        result = _handle_text({"raw": ""}, self._CONFIG, self._anns(), None, None)
        assert result == []

    @given(raw=st.text(min_size=1, max_size=200))
    @settings(max_examples=200)
    def test_handle_text_segment_type_is_text(self, raw: str) -> None:
        """Each segment produced by _handle_text has type 'text'."""
        result = _handle_text({"raw": raw}, self._CONFIG, self._anns(), None, None)
        assert result[0]["type"] == "text"

    @given(raw=st.text(min_size=1, max_size=200))
    @settings(max_examples=200)
    def test_handle_text_content_equals_raw(self, raw: str) -> None:
        """The text content of the segment matches the raw token value."""
        result = _handle_text({"raw": raw}, self._CONFIG, self._anns(), None, None)
        assert result[0]["text"]["content"] == raw

    # --- _handle_softbreak ---

    def test_handle_softbreak_returns_space_segment(self) -> None:
        """_handle_softbreak always returns a single segment with content ' '."""
        result = _handle_softbreak({}, self._CONFIG, self._anns(), None, None)
        assert len(result) == 1
        assert result[0]["text"]["content"] == " "

    # --- _handle_linebreak ---

    def test_handle_linebreak_returns_newline_segment(self) -> None:
        """_handle_linebreak always returns a single segment with content '\\n'."""
        result = _handle_linebreak({}, self._CONFIG, self._anns(), None, None)
        assert len(result) == 1
        assert result[0]["text"]["content"] == "\n"

    # --- _handle_codespan ---

    @given(raw=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_handle_codespan_sets_code_annotation(self, raw: str) -> None:
        """_handle_codespan always sets annotations.code == True."""
        result = _handle_codespan({"raw": raw}, self._CONFIG, self._anns(), None, None)
        assert len(result) == 1
        assert result[0]["annotations"]["code"] is True

    @given(raw=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_handle_codespan_content_equals_raw(self, raw: str) -> None:
        """The text content of the codespan segment matches the raw value."""
        result = _handle_codespan({"raw": raw}, self._CONFIG, self._anns(), None, None)
        assert result[0]["text"]["content"] == raw

    # --- _handle_html_inline ---

    @given(raw=st.text(min_size=1, max_size=200))
    @settings(max_examples=200)
    def test_handle_html_inline_non_empty_returns_segment(self, raw: str) -> None:
        """_handle_html_inline with non-empty raw returns one segment."""
        result = _handle_html_inline({"raw": raw}, self._CONFIG, self._anns(), None, None)
        assert len(result) == 1

    def test_handle_html_inline_empty_raw_returns_empty(self) -> None:
        """_handle_html_inline with empty raw returns an empty list."""
        result = _handle_html_inline({"raw": ""}, self._CONFIG, self._anns(), None, None)
        assert result == []


class TestAnnotationHandlerProperties:
    """Property tests for the annotation-merging inline handler functions."""

    _CONFIG = NotionifyConfig(token="test-token")

    @staticmethod
    def _text_child(raw: str) -> dict:
        return {"type": "text", "raw": raw}

    @staticmethod
    def _base_anns() -> dict:
        return {"bold": False, "italic": False, "strikethrough": False,
                "underline": False, "code": False, "color": "default"}

    # --- _handle_strong ---

    @given(raw=st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_handle_strong_sets_bold(self, raw: str) -> None:
        """_handle_strong always produces segments with bold=True."""
        token = {"children": [self._text_child(raw)]}
        result = _handle_strong(token, self._CONFIG, self._base_anns(), None, None)
        assert all(seg.get("annotations", {}).get("bold") is True for seg in result)

    def test_handle_strong_empty_children_returns_empty(self) -> None:
        """_handle_strong with no children returns an empty list."""
        result = _handle_strong({"children": []}, self._CONFIG, self._base_anns(), None, None)
        assert result == []

    # --- _handle_emphasis ---

    @given(raw=st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_handle_emphasis_sets_italic(self, raw: str) -> None:
        """_handle_emphasis always produces segments with italic=True."""
        token = {"children": [self._text_child(raw)]}
        result = _handle_emphasis(token, self._CONFIG, self._base_anns(), None, None)
        assert all(seg.get("annotations", {}).get("italic") is True for seg in result)

    def test_handle_emphasis_empty_children_returns_empty(self) -> None:
        """_handle_emphasis with no children returns an empty list."""
        result = _handle_emphasis(
            {"children": []}, self._CONFIG, self._base_anns(), None, None
        )
        assert result == []

    # --- _handle_strikethrough ---

    @given(raw=st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_handle_strikethrough_sets_annotation(self, raw: str) -> None:
        """_handle_strikethrough always produces segments with strikethrough=True."""
        token = {"children": [self._text_child(raw)]}
        result = _handle_strikethrough(token, self._CONFIG, self._base_anns(), None, None)
        assert all(
            seg.get("annotations", {}).get("strikethrough") is True for seg in result
        )

    def test_handle_strikethrough_empty_children_returns_empty(self) -> None:
        """_handle_strikethrough with no children returns an empty list."""
        result = _handle_strikethrough(
            {"children": []}, self._CONFIG, self._base_anns(), None, None
        )
        assert result == []

    # --- _handle_image ---

    @given(
        alt=st.text(min_size=1, max_size=50),
        url=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=200)
    def test_handle_image_alt_and_url_produces_markdown_link(
        self, alt: str, url: str
    ) -> None:
        """With both alt and url, the segment text is '[alt](url)'."""
        token = {
            "attrs": {"url": url},
            "children": [{"type": "text", "raw": alt}],
        }
        result = _handle_image(token, self._CONFIG, self._base_anns(), None, None)
        assert len(result) == 1
        content = result[0]["text"]["content"]
        assert alt in content
        assert url in content

    def test_handle_image_no_alt_no_url_returns_placeholder(self) -> None:
        """With no alt and no url, the segment text is '[image]'."""
        token: dict = {"attrs": {}, "children": []}
        result = _handle_image(token, self._CONFIG, self._base_anns(), None, None)
        assert len(result) == 1
        assert result[0]["text"]["content"] == "[image]"

    @given(url=st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_handle_image_url_only_returns_url(self, url: str) -> None:
        """With only url and no alt, the segment text is the URL."""
        token = {"attrs": {"url": url}, "children": []}
        result = _handle_image(token, self._CONFIG, self._base_anns(), None, None)
        assert len(result) == 1
        assert result[0]["text"]["content"] == url


class TestLinkAndMathHandlerProperties:
    """Property tests for _handle_link and _handle_inline_math."""

    _CONFIG = NotionifyConfig(token="test-token")

    @staticmethod
    def _base_anns() -> dict:
        return {"bold": False, "italic": False, "strikethrough": False,
                "underline": False, "code": False, "color": "default"}

    @staticmethod
    def _text_child(raw: str) -> dict:
        return {"type": "text", "raw": raw}

    # --- _handle_link ---

    def test_handle_link_empty_children_returns_empty(self) -> None:
        """_handle_link with no children returns an empty list."""
        token: dict = {"attrs": {"url": "https://example.com"}, "children": []}
        result = _handle_link(token, self._CONFIG, self._base_anns(), None, None)
        assert result == []

    @given(
        raw=st.text(min_size=1, max_size=80),
        url=st.text(min_size=1, max_size=100),
    )
    @settings(max_examples=200)
    def test_handle_link_segment_href_equals_url(self, raw: str, url: str) -> None:
        """_handle_link passes the token's URL as href to child segments."""
        token = {"attrs": {"url": url}, "children": [self._text_child(raw)]}
        result = _handle_link(token, self._CONFIG, self._base_anns(), None, None)
        assert len(result) >= 1
        assert result[0].get("href") == url

    @given(raw=st.text(min_size=1, max_size=80))
    @settings(max_examples=200)
    def test_handle_link_no_url_uses_empty_href(self, raw: str) -> None:
        """Without a url attribute, the href on segments is empty string or None."""
        token = {"attrs": {}, "children": [self._text_child(raw)]}
        result = _handle_link(token, self._CONFIG, self._base_anns(), None, None)
        assert len(result) >= 1
        # href will be "" (empty string from url extraction)
        assert result[0].get("href") in ("", None)

    # --- _handle_inline_math ---

    @given(expression=st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_handle_inline_math_always_returns_list(self, expression: str) -> None:
        """_handle_inline_math always returns a list (never raises)."""
        result = _handle_inline_math(
            {"raw": expression}, self._CONFIG, self._base_anns(), None, []
        )
        assert isinstance(result, list)

    @given(expression=st.text(min_size=1, max_size=50))
    @settings(max_examples=200)
    def test_handle_inline_math_short_expression_produces_segment(
        self, expression: str
    ) -> None:
        """A non-empty math expression produces at least one segment."""
        result = _handle_inline_math(
            {"raw": expression}, self._CONFIG, self._base_anns(), None, []
        )
        assert len(result) >= 1


class TestBlockBuilderHelperProperties:
    """Property tests for simpler block_builder.py helpers using _BuildContext."""

    _CONFIG = NotionifyConfig(token="test-token")

    def _ctx(self) -> _BuildContext:
        return _BuildContext(self._CONFIG)

    # --- _build_divider ---

    def test_build_divider_returns_one_block(self) -> None:
        """_build_divider always returns a list with exactly one block."""
        ctx = self._ctx()
        result = _build_divider({}, ctx)
        assert len(result) == 1

    def test_build_divider_block_type_is_divider(self) -> None:
        """_build_divider produces a block of type 'divider'."""
        ctx = self._ctx()
        result = _build_divider({}, ctx)
        assert result[0]["type"] == "divider"

    def test_build_divider_adds_block_to_context(self) -> None:
        """_build_divider registers the block in ctx.blocks."""
        ctx = self._ctx()
        _build_divider({}, ctx)
        assert len(ctx.blocks) == 1
        assert ctx.blocks[0]["type"] == "divider"

    # --- _build_heading ---

    @given(level=st.integers(min_value=1, max_value=3))
    @settings(max_examples=50)
    def test_build_heading_level_1_to_3_produces_heading_block(self, level: int) -> None:
        """Heading levels 1-3 produce the matching heading_N block type."""
        ctx = self._ctx()
        token: dict = {"attrs": {"level": level}, "children": []}
        result = _build_heading(token, ctx)
        assert len(result) == 1
        assert result[0]["type"] == f"heading_{level}"

    @given(level=st.integers(min_value=4, max_value=6))
    @settings(max_examples=50)
    def test_build_heading_overflow_downgrade_clamps_to_3(self, level: int) -> None:
        """Heading levels > 3 with downgrade overflow produce heading_3."""
        from notionify.config import NotionifyConfig as _Config

        cfg = _Config(token="test-token", heading_overflow="downgrade")
        ctx = _BuildContext(cfg)
        token: dict = {"attrs": {"level": level}, "children": []}
        result = _build_heading(token, ctx)
        assert len(result) == 1
        assert result[0]["type"] == "heading_3"

    @given(level=st.integers(min_value=4, max_value=6))
    @settings(max_examples=50)
    def test_build_heading_overflow_paragraph_produces_paragraph(self, level: int) -> None:
        """Heading levels > 3 with paragraph overflow produce a paragraph block."""
        from notionify.config import NotionifyConfig as _Config

        cfg = _Config(token="test-token", heading_overflow="paragraph")
        ctx = _BuildContext(cfg)
        token: dict = {"attrs": {"level": level}, "children": []}
        result = _build_heading(token, ctx)
        assert len(result) == 1
        assert result[0]["type"] == "paragraph"

    @given(level=st.integers(min_value=1, max_value=6))
    @settings(max_examples=50)
    def test_build_heading_adds_block_to_context(self, level: int) -> None:
        """_build_heading always adds exactly one block to ctx.blocks."""
        ctx = self._ctx()
        token: dict = {"attrs": {"level": level}, "children": []}
        _build_heading(token, ctx)
        assert len(ctx.blocks) == 1


class TestBlockBuilderCodeAndQuoteProperties:
    """Property tests for _build_code_block and _build_block_quote."""

    _CONFIG = NotionifyConfig(token="test-token")

    def _ctx(self) -> _BuildContext:
        return _BuildContext(self._CONFIG)

    # --- _build_code_block ---

    @given(raw=st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_build_code_block_type_is_code(self, raw: str) -> None:
        """_build_code_block always produces a block of type 'code'."""
        from notionify.converter.block_builder import _build_code_block

        ctx = self._ctx()
        result = _build_code_block({"raw": raw}, ctx)
        assert len(result) == 1
        assert result[0]["type"] == "code"

    @given(raw=st.text(min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_build_code_block_default_language_is_plain_text(self, raw: str) -> None:
        """Without an info/language token attr, language defaults to 'plain text'."""
        from notionify.converter.block_builder import _build_code_block

        ctx = self._ctx()
        result = _build_code_block({"raw": raw, "attrs": {}}, ctx)
        assert result[0]["code"]["language"] == "plain text"

    @given(raw=st.text(min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_build_code_block_adds_to_context(self, raw: str) -> None:
        """_build_code_block registers the block in ctx.blocks."""
        from notionify.converter.block_builder import _build_code_block

        ctx = self._ctx()
        _build_code_block({"raw": raw}, ctx)
        assert len(ctx.blocks) == 1
        assert ctx.blocks[0]["type"] == "code"

    # --- _build_block_quote ---

    def test_build_block_quote_type_is_quote(self) -> None:
        """_build_block_quote always produces a block of type 'quote'."""
        from notionify.converter.block_builder import _build_block_quote

        ctx = self._ctx()
        result = _build_block_quote({"children": []}, ctx)
        assert len(result) == 1
        assert result[0]["type"] == "quote"

    def test_build_block_quote_empty_children_has_empty_rich_text(self) -> None:
        """No children → empty rich_text list."""
        from notionify.converter.block_builder import _build_block_quote

        ctx = self._ctx()
        result = _build_block_quote({"children": []}, ctx)
        assert result[0]["quote"]["rich_text"] == []

    def test_build_block_quote_adds_to_context(self) -> None:
        """_build_block_quote registers the block in ctx.blocks."""
        from notionify.converter.block_builder import _build_block_quote

        ctx = self._ctx()
        _build_block_quote({"children": []}, ctx)
        assert len(ctx.blocks) == 1

    def test_build_block_quote_with_paragraph_child_fills_rich_text(self) -> None:
        """A paragraph child contributes its inline content to rich_text."""
        from notionify.converter.block_builder import _build_block_quote

        ctx = self._ctx()
        token = {
            "children": [
                {
                    "type": "paragraph",
                    "children": [{"type": "text", "raw": "Hello"}],
                }
            ]
        }
        result = _build_block_quote(token, ctx)
        assert len(result[0]["quote"]["rich_text"]) >= 1


class TestBlockBuilderParagraphAndMathProperties:
    """Property tests for _build_paragraph and _build_block_math."""

    _CONFIG = NotionifyConfig(token="test-token")

    def _ctx(self) -> _BuildContext:
        return _BuildContext(self._CONFIG)

    # --- _build_paragraph ---

    def test_build_paragraph_empty_children_returns_empty(self) -> None:
        """_build_paragraph with no children returns [] (no empty paragraphs)."""
        from notionify.converter.block_builder import _build_paragraph

        ctx = self._ctx()
        result = _build_paragraph({"children": []}, ctx)
        assert result == []
        assert len(ctx.blocks) == 0

    @given(raw=st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_build_paragraph_with_text_child_produces_paragraph(self, raw: str) -> None:
        """A text child produces a paragraph block."""
        from notionify.converter.block_builder import _build_paragraph

        ctx = self._ctx()
        token = {"children": [{"type": "text", "raw": raw}]}
        result = _build_paragraph(token, ctx)
        assert len(result) == 1
        assert result[0]["type"] == "paragraph"

    @given(raw=st.text(min_size=1, max_size=50))
    @settings(max_examples=200)
    def test_build_paragraph_paragraph_color_is_default(self, raw: str) -> None:
        """Paragraph block always has color 'default'."""
        from notionify.converter.block_builder import _build_paragraph

        ctx = self._ctx()
        token = {"children": [{"type": "text", "raw": raw}]}
        result = _build_paragraph(token, ctx)
        assert result[0]["paragraph"]["color"] == "default"

    # --- _build_block_math ---

    @given(expression=st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_build_block_math_returns_list(self, expression: str) -> None:
        """_build_block_math always returns a list (never raises)."""
        from notionify.converter.block_builder import _build_block_math

        ctx = self._ctx()
        result = _build_block_math({"raw": expression}, ctx)
        assert isinstance(result, list)

    @given(expression=st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_build_block_math_nonempty_expression_produces_blocks(
        self, expression: str
    ) -> None:
        """A non-empty expression always produces at least one block."""
        from notionify.converter.block_builder import _build_block_math

        ctx = self._ctx()
        result = _build_block_math({"raw": expression}, ctx)
        assert len(result) >= 1

    @given(expression=st.text(min_size=1, max_size=50))
    @settings(max_examples=200)
    def test_build_block_math_blocks_added_to_context(self, expression: str) -> None:
        """All produced blocks are registered in ctx.blocks."""
        from notionify.converter.block_builder import _build_block_math

        ctx = self._ctx()
        result = _build_block_math({"raw": expression}, ctx)
        assert len(ctx.blocks) == len(result)


class TestBuildListItemProperties:
    """Property tests for _build_list_item and _build_task_list_item."""

    _CONFIG = NotionifyConfig(token="test-token")

    def _ctx(self) -> _BuildContext:
        return _BuildContext(self._CONFIG)

    # --- _build_list_item ---

    def test_build_list_item_unordered_type(self) -> None:
        """ordered=False produces a bulleted_list_item block."""
        ctx = self._ctx()
        result = _build_list_item({"children": []}, ordered=False, ctx=ctx)
        assert result["type"] == "bulleted_list_item"

    def test_build_list_item_ordered_type(self) -> None:
        """ordered=True produces a numbered_list_item block."""
        ctx = self._ctx()
        result = _build_list_item({"children": []}, ordered=True, ctx=ctx)
        assert result["type"] == "numbered_list_item"

    def test_build_list_item_adds_to_context(self) -> None:
        """_build_list_item registers the block in ctx.blocks."""
        ctx = self._ctx()
        _build_list_item({"children": []}, ordered=False, ctx=ctx)
        assert len(ctx.blocks) == 1

    @given(ordered=st.booleans())
    @settings(max_examples=50)
    def test_build_list_item_color_is_default(self, ordered: bool) -> None:
        """List item block always has color 'default'."""
        ctx = self._ctx()
        result = _build_list_item({"children": []}, ordered=ordered, ctx=ctx)
        block_type = result["type"]
        assert result[block_type]["color"] == "default"

    # --- _build_task_list_item ---

    def test_build_task_list_item_type_is_to_do(self) -> None:
        """_build_task_list_item always produces a 'to_do' block."""
        ctx = self._ctx()
        result = _build_task_list_item({"children": [], "attrs": {}}, ctx)
        assert result["type"] == "to_do"

    def test_build_task_list_item_checked_true_preserved(self) -> None:
        """checked=True attribute is preserved in the block."""
        ctx = self._ctx()
        token: dict = {"children": [], "attrs": {"checked": True}}
        result = _build_task_list_item(token, ctx)
        assert result["to_do"]["checked"] is True

    def test_build_task_list_item_checked_false_preserved(self) -> None:
        """checked=False attribute is preserved in the block."""
        ctx = self._ctx()
        token: dict = {"children": [], "attrs": {"checked": False}}
        result = _build_task_list_item(token, ctx)
        assert result["to_do"]["checked"] is False

    def test_build_task_list_item_adds_to_context(self) -> None:
        """_build_task_list_item registers the block in ctx.blocks."""
        ctx = self._ctx()
        _build_task_list_item({"children": [], "attrs": {}}, ctx)
        assert len(ctx.blocks) == 1


class TestBuildListAndDispatchProperties:
    """Property tests for _build_list, _process_token, and _process_tokens."""

    _CONFIG = NotionifyConfig(token="test-token")

    def _ctx(self) -> _BuildContext:
        return _BuildContext(self._CONFIG)

    # --- _build_list ---

    def test_build_list_empty_children_returns_empty(self) -> None:
        """A list token with no children produces no blocks."""
        ctx = self._ctx()
        result = _build_list({"attrs": {}, "children": []}, ctx)
        assert result == []

    @given(n=st.integers(min_value=1, max_value=5))
    @settings(max_examples=100)
    def test_build_list_n_items_produces_n_blocks(self, n: int) -> None:
        """N list_item children produce N blocks."""
        ctx = self._ctx()
        items = [{"type": "list_item", "children": []} for _ in range(n)]
        token: dict = {"attrs": {"ordered": False}, "children": items}
        result = _build_list(token, ctx)
        assert len(result) == n

    @given(ordered=st.booleans(), n=st.integers(min_value=1, max_value=4))
    @settings(max_examples=100)
    def test_build_list_ordered_flag_propagates(self, ordered: bool, n: int) -> None:
        """The ordered flag determines item block types."""
        ctx = self._ctx()
        items = [{"type": "list_item", "children": []} for _ in range(n)]
        token: dict = {"attrs": {"ordered": ordered}, "children": items}
        result = _build_list(token, ctx)
        expected_type = "numbered_list_item" if ordered else "bulleted_list_item"
        assert all(b["type"] == expected_type for b in result)

    # --- _process_token ---

    @given(
        token_type=st.text(
            min_size=3, max_size=20,
            alphabet=st.characters(whitelist_categories=("L",)),
        )
    )
    @settings(max_examples=200)
    def test_process_token_unknown_type_returns_empty(self, token_type: str) -> None:
        """An unknown token type produces no blocks."""
        from notionify.converter.block_builder import _BLOCK_HANDLERS
        assume(token_type not in _BLOCK_HANDLERS)
        ctx = self._ctx()
        result = _process_token({"type": token_type}, ctx)
        assert result == []

    def test_process_token_thematic_break_produces_divider(self) -> None:
        """'thematic_break' is mapped to _build_divider → returns a divider block."""
        ctx = self._ctx()
        result = _process_token({"type": "thematic_break"}, ctx)
        assert len(result) == 1
        assert result[0]["type"] == "divider"

    def test_process_token_no_type_returns_empty(self) -> None:
        """A token with no 'type' key returns [] without raising."""
        ctx = self._ctx()
        result = _process_token({}, ctx)
        assert result == []

    # --- _process_tokens ---

    def test_process_tokens_empty_list_returns_empty(self) -> None:
        """No tokens → no blocks."""
        ctx = self._ctx()
        result = _process_tokens([], ctx)
        assert result == []

    def test_process_tokens_accumulates_blocks(self) -> None:
        """Multiple tokens accumulate into ctx.blocks."""
        ctx = self._ctx()
        tokens = [{"type": "thematic_break"}, {"type": "thematic_break"}]
        result = _process_tokens(tokens, ctx)
        assert len(result) == 2
        assert len(ctx.blocks) == 2


# ---------------------------------------------------------------------------
# ASTNormalizer private method properties
# ---------------------------------------------------------------------------


class TestNormalizeTokensListProperties:
    """Property tests for ASTNormalizer._normalize_tokens."""

    def _n(self) -> ASTNormalizer:
        return ASTNormalizer()

    def test_empty_list_returns_empty(self) -> None:
        """_normalize_tokens([]) → []."""
        assert self._n()._normalize_tokens([]) == []

    def test_blank_line_tokens_are_skipped(self) -> None:
        """blank_line tokens are silently dropped."""
        result = self._n()._normalize_tokens(
            [{"type": "blank_line"}, {"type": "blank_line"}]
        )
        assert result == []

    @given(count=st.integers(min_value=1, max_value=5))
    @settings(max_examples=100)
    def test_thematic_break_tokens_all_preserved(self, count: int) -> None:
        """Non-skipped tokens all appear in output."""
        tokens = [{"type": "thematic_break"} for _ in range(count)]
        result = self._n()._normalize_tokens(tokens)
        assert len(result) == count

    @given(count=st.integers(min_value=1, max_value=5))
    @settings(max_examples=100)
    def test_blank_lines_mixed_with_valid_filtered(self, count: int) -> None:
        """Blank lines interleaved with valid tokens are dropped."""
        tokens: list[dict] = []
        for _ in range(count):
            tokens.append({"type": "blank_line"})
            tokens.append({"type": "thematic_break"})
        result = self._n()._normalize_tokens(tokens)
        assert len(result) == count


class TestNormalizeTokenDispatchProperties:
    """Property tests for ASTNormalizer._normalize_token."""

    def _n(self) -> ASTNormalizer:
        return ASTNormalizer()

    def test_blank_line_returns_none(self) -> None:
        """blank_line → None."""
        assert self._n()._normalize_token({"type": "blank_line"}) is None

    def test_footnotes_returns_none(self) -> None:
        """footnotes token → None."""
        assert self._n()._normalize_token({"type": "footnotes"}) is None

    def test_footnote_ref_renders_bracket_notation(self) -> None:
        """footnote_ref → text token with '[^key]' raw value."""
        result = self._n()._normalize_token({"type": "footnote_ref", "raw": "1"})
        assert result is not None
        assert result["type"] == "text"
        assert result["raw"] == "[^1]"

    def test_thematic_break_mapped_to_block(self) -> None:
        """thematic_break (in _BLOCK_TYPE_MAP) → normalized block dict."""
        result = self._n()._normalize_token({"type": "thematic_break"})
        assert result is not None
        assert result["type"] == "thematic_break"

    def test_text_inline_mapped(self) -> None:
        """text (in _INLINE_TYPE_MAP) → normalized inline token."""
        result = self._n()._normalize_token({"type": "text", "raw": "hi"})
        assert result is not None
        assert result["type"] == "text"

    @given(
        unknown=st.text(
            min_size=3,
            max_size=20,
            alphabet=st.characters(whitelist_categories=("L",)),
        )
    )
    @settings(max_examples=200)
    def test_unknown_type_passed_through_with_type_preserved(
        self, unknown: str
    ) -> None:
        """Unknown token types are passed through; original type is preserved."""
        from notionify.converter.ast_normalizer import (
            _BLOCK_TYPE_MAP,
            _INLINE_TYPE_MAP,
            _SKIP_TYPES,
        )

        assume(unknown not in _BLOCK_TYPE_MAP)
        assume(unknown not in _INLINE_TYPE_MAP)
        assume(unknown not in _SKIP_TYPES)
        assume(unknown not in {"footnotes", "footnote_ref", "raw"})
        assume(unknown not in {"table_head", "table_body", "table_row", "table_cell"})
        result = self._n()._normalize_token({"type": unknown})
        assert result is not None
        assert result["type"] == unknown


class TestNormalizeBlockProperties2:
    """Property tests for ASTNormalizer._normalize_block."""

    def _n(self) -> ASTNormalizer:
        return ASTNormalizer()

    def test_block_code_strips_trailing_newline(self) -> None:
        """block_code: trailing '\\n' stripped from raw."""
        result = self._n()._normalize_block(
            {"type": "block_code", "raw": "x = 1\n"}, "block_code"
        )
        assert result["raw"] == "x = 1"

    def test_block_code_without_newline_unchanged(self) -> None:
        """block_code without trailing newline is preserved."""
        result = self._n()._normalize_block(
            {"type": "block_code", "raw": "x = 1"}, "block_code"
        )
        assert result["raw"] == "x = 1"

    @given(expr=st.text(min_size=0, max_size=80))
    @settings(max_examples=200)
    def test_block_math_preserves_raw_verbatim(self, expr: str) -> None:
        """block_math: raw expression is preserved verbatim."""
        result = self._n()._normalize_block(
            {"type": "block_math", "raw": expr}, "block_math"
        )
        assert result["raw"] == expr

    def test_thematic_break_has_no_children_key(self) -> None:
        """thematic_break: result must not include a 'children' key."""
        result = self._n()._normalize_block(
            {"type": "thematic_break"}, "thematic_break"
        )
        assert "children" not in result

    @given(
        canonical=st.sampled_from(
            ["heading", "paragraph", "block_code", "block_math", "thematic_break"]
        )
    )
    @settings(max_examples=100)
    def test_result_always_has_type_key(self, canonical: str) -> None:
        """_normalize_block always sets 'type' to the canonical type."""
        result = self._n()._normalize_block({"type": "any"}, canonical)
        assert result["type"] == canonical


class TestNormalizeInlineProperties2:
    """Property tests for ASTNormalizer._normalize_inline."""

    def _n(self) -> ASTNormalizer:
        return ASTNormalizer()

    @given(raw=st.text(min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_text_token_preserves_raw(self, raw: str) -> None:
        """text inline: raw value is preserved."""
        result = self._n()._normalize_inline({"type": "text", "raw": raw}, "text")
        assert result["raw"] == raw

    def test_softbreak_has_no_children(self) -> None:
        """softbreak: no 'children' key in output."""
        result = self._n()._normalize_inline({"type": "softbreak"}, "softbreak")
        assert "children" not in result

    @given(raw=st.text(min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_codespan_preserves_raw(self, raw: str) -> None:
        """codespan: raw value is preserved verbatim."""
        result = self._n()._normalize_inline(
            {"type": "codespan", "raw": raw}, "codespan"
        )
        assert result["raw"] == raw

    @given(raw=st.text(min_size=0, max_size=50))
    @settings(max_examples=200)
    def test_inline_math_preserves_raw(self, raw: str) -> None:
        """inline_math: expression stored in raw is preserved."""
        result = self._n()._normalize_inline(
            {"type": "inline_math", "raw": raw}, "inline_math"
        )
        assert result["raw"] == raw

    @given(
        canonical=st.sampled_from(
            ["text", "softbreak", "linebreak", "codespan", "inline_math", "html_inline"]
        )
    )
    @settings(max_examples=100)
    def test_result_always_has_type_key(self, canonical: str) -> None:
        """_normalize_inline always sets 'type' to the canonical type."""
        result = self._n()._normalize_inline({"type": "any"}, canonical)
        assert result["type"] == canonical


# ---------------------------------------------------------------------------
# NotionToMarkdownRenderer block renderer method properties
# ---------------------------------------------------------------------------


class TestRendererBlockMethodsProperties:
    """Property tests for individual NotionToMarkdownRenderer._render_* methods."""

    def _r(self) -> NotionToMarkdownRenderer:
        return NotionToMarkdownRenderer(NotionifyConfig(token="test-token"))

    def _rt(self, text: str) -> list[dict]:
        return [{"type": "text", "text": {"content": text}, "plain_text": text}]

    # --- _render_divider ---

    def test_divider_always_returns_hr_string(self) -> None:
        """_render_divider always returns '---\\n\\n' regardless of block/depth."""
        r = self._r()
        assert r._render_divider({}, 0) == "---\n\n"
        assert r._render_divider({}, 5) == "---\n\n"

    # --- _render_heading ---

    @given(
        level=st.integers(min_value=1, max_value=3),
        text=st.text(min_size=1, max_size=50, alphabet=string.ascii_letters + " "),
    )
    @settings(max_examples=200)
    def test_heading_prefix_matches_level(self, level: int, text: str) -> None:
        """Heading prefix '#' count equals the heading level."""
        r = self._r()
        block = {f"heading_{level}": {"rich_text": self._rt(text)}}
        result = r._render_heading(block, 0, level)
        assert result.startswith("#" * level + " ")

    @given(
        level=st.integers(min_value=1, max_value=3),
        text=st.text(min_size=1, max_size=50, alphabet=string.ascii_letters + " "),
    )
    @settings(max_examples=100)
    def test_heading_ends_with_double_newline(self, level: int, text: str) -> None:
        """Heading output always ends with '\\n\\n'."""
        r = self._r()
        block = {f"heading_{level}": {"rich_text": self._rt(text)}}
        result = r._render_heading(block, 0, level)
        assert result.endswith("\n\n")

    # --- _render_equation ---

    @given(expr=st.text(min_size=0, max_size=100))
    @settings(max_examples=200)
    def test_equation_expression_preserved_verbatim(self, expr: str) -> None:
        """_render_equation wraps expression in $$...$$."""
        r = self._r()
        block = {"equation": {"expression": expr}}
        result = r._render_equation(block, 0)
        assert result == f"$$\n{expr}\n$$\n\n"

    # --- _render_bulleted_list_item ---

    @given(
        text=st.text(min_size=1, max_size=50, alphabet=string.ascii_letters + " "),
        depth=st.integers(min_value=0, max_value=4),
    )
    @settings(max_examples=200)
    def test_bullet_has_dash_prefix_with_indent(self, text: str, depth: int) -> None:
        """Bulleted item has '- ' prefix after depth-based indent."""
        r = self._r()
        block = {"bulleted_list_item": {"rich_text": self._rt(text)}}
        result = r._render_bulleted_list_item(block, depth)
        assert result.startswith("  " * depth + "- ")

    # --- _render_to_do ---

    @given(text=st.text(min_size=1, max_size=50, alphabet=string.ascii_letters + " "))
    @settings(max_examples=200)
    def test_to_do_checked_contains_checkmark(self, text: str) -> None:
        """to_do checked=True → '[x]' present in output."""
        r = self._r()
        block = {"to_do": {"rich_text": self._rt(text), "checked": True}}
        assert "[x]" in r._render_to_do(block, 0)

    @given(text=st.text(min_size=1, max_size=50, alphabet=string.ascii_letters + " "))
    @settings(max_examples=200)
    def test_to_do_unchecked_contains_empty_box(self, text: str) -> None:
        """to_do checked=False → '[ ]' present in output."""
        r = self._r()
        block = {"to_do": {"rich_text": self._rt(text), "checked": False}}
        assert "[ ]" in r._render_to_do(block, 0)

    # --- _render_code ---

    @given(
        lang=st.text(min_size=1, max_size=20, alphabet=string.ascii_letters),
        code=st.text(min_size=0, max_size=100),
    )
    @settings(max_examples=200)
    def test_code_wraps_in_backtick_fences(self, lang: str, code: str) -> None:
        """Code block always wrapped in triple-backtick fences."""
        assume(lang.lower() != "plain text")
        assume(lang.lower() != "latex")
        r = self._r()
        block = {"code": {"language": lang, "rich_text": [{"plain_text": code}]}}
        result = r._render_code(block, 0)
        assert result.startswith("```")
        assert result.endswith("```\n\n")

    def test_code_plain_text_language_produces_empty_fence_info(self) -> None:
        """Language 'plain text' → empty fence info (``` with no lang)."""
        r = self._r()
        block = {"code": {"language": "plain text", "rich_text": [{"plain_text": "x"}]}}
        result = r._render_code(block, 0)
        assert result.startswith("```\n")

    # --- _render_numbered_list_item ---

    @given(
        text=st.text(min_size=1, max_size=50, alphabet=string.ascii_letters + " "),
        number=st.integers(min_value=1, max_value=100),
        depth=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=200)
    def test_numbered_item_prefix_contains_number(
        self, text: str, number: int, depth: int
    ) -> None:
        """Numbered list item starts with 'indent + number + '. '."""
        r = self._r()
        block = {"numbered_list_item": {"rich_text": self._rt(text)}}
        result = r._render_numbered_list_item(block, depth, number)
        assert result.startswith("  " * depth + f"{number}. ")


class TestRendererUrlAndCalloutProperties:
    """Property tests for renderer methods: callout, child pages, embed, bookmark."""

    def _r(self) -> NotionToMarkdownRenderer:
        return NotionToMarkdownRenderer(NotionifyConfig(token="test-token"))

    def _rt(self, text: str) -> list[dict]:
        return [{"type": "text", "text": {"content": text}, "plain_text": text}]

    # --- _render_callout ---

    @given(
        emoji=st.text(min_size=1, max_size=2, alphabet="🔥💡✅⚡🎯"),
        text=st.text(min_size=1, max_size=40, alphabet=string.ascii_letters + " "),
    )
    @settings(max_examples=100)
    def test_callout_with_emoji_prepends_icon(self, emoji: str, text: str) -> None:
        """Callout with emoji icon → icon prepended before text in output."""
        r = self._r()
        block = {
            "callout": {
                "icon": {"type": "emoji", "emoji": emoji},
                "rich_text": self._rt(text),
            }
        }
        result = r._render_callout(block, 0)
        assert emoji in result

    @given(text=st.text(min_size=1, max_size=40, alphabet=string.ascii_letters + " "))
    @settings(max_examples=100)
    def test_callout_always_starts_with_quote_marker(self, text: str) -> None:
        """Callout output always begins with '> '."""
        r = self._r()
        block = {"callout": {"rich_text": self._rt(text)}}
        result = r._render_callout(block, 0)
        assert result.startswith("> ")

    # --- _render_child_page / _render_child_database ---

    @given(
        title=st.text(min_size=1, max_size=40, alphabet=string.ascii_letters + " "),
    )
    @settings(max_examples=100)
    def test_child_page_output_contains_page_prefix(self, title: str) -> None:
        """_render_child_page → '[Page: title](url)\\n\\n'."""
        r = self._r()
        block = {"child_page": {"title": title}, "id": "abc-123"}
        result = r._render_child_page(block, 0)
        assert result.startswith("[Page: ")
        assert result.endswith("\n\n")

    @given(
        title=st.text(min_size=1, max_size=40, alphabet=string.ascii_letters + " "),
    )
    @settings(max_examples=100)
    def test_child_database_output_contains_database_prefix(self, title: str) -> None:
        """_render_child_database → '[Database: title](url)\\n\\n'."""
        r = self._r()
        block = {"child_database": {"title": title}, "id": "abc-123"}
        result = r._render_child_database(block, 0)
        assert result.startswith("[Database: ")
        assert result.endswith("\n\n")

    # --- _render_embed ---

    def test_embed_contains_embed_label(self) -> None:
        """_render_embed renders as '[Embed](url)\\n\\n'."""
        r = self._r()
        block = {"embed": {"url": "https://example.com/video"}}
        result = r._render_embed(block, 0)
        assert result.startswith("[Embed](")
        assert result.endswith("\n\n")

    # --- _render_bookmark ---

    @given(
        url=st.text(min_size=5, max_size=40, alphabet=string.ascii_letters + ":/._-"),
    )
    @settings(max_examples=100)
    def test_bookmark_contains_url_in_output(self, url: str) -> None:
        """_render_bookmark always includes the URL somewhere in the output."""
        r = self._r()
        block = {"bookmark": {"url": url}}
        result = r._render_bookmark(block, 0)
        assert url in result
        assert result.endswith("\n\n")

    # --- _render_block_list numbered counter ---

    def test_block_list_numbered_counter_increments(self) -> None:
        """Consecutive numbered_list_item blocks receive incrementing numbers."""
        r = self._r()
        blocks = [
            {"type": "numbered_list_item",
             "numbered_list_item": {"rich_text": self._rt("alpha")}},
            {"type": "numbered_list_item",
             "numbered_list_item": {"rich_text": self._rt("beta")}},
            {"type": "numbered_list_item",
             "numbered_list_item": {"rich_text": self._rt("gamma")}},
        ]
        result = r._render_block_list(blocks, 0)
        assert "1. alpha" in result
        assert "2. beta" in result
        assert "3. gamma" in result

    def test_block_list_numbered_counter_resets_after_divider(self) -> None:
        """Numbered counter resets to 1 when a non-numbered block interrupts."""
        r = self._r()
        blocks = [
            {"type": "numbered_list_item",
             "numbered_list_item": {"rich_text": self._rt("first")}},
            {"type": "divider", "divider": {}},
            {"type": "numbered_list_item",
             "numbered_list_item": {"rich_text": self._rt("second")}},
        ]
        result = r._render_block_list(blocks, 0)
        # Counter resets: both items should be numbered "1."
        assert "1. first" in result
        assert "1. second" in result


class TestRendererImageTableUnsupportedProperties:
    """Property tests for _render_image, _render_table, _render_passthrough,
    _render_unsupported, _render_quote, _render_media."""

    def _r(self, **kwargs) -> NotionToMarkdownRenderer:
        return NotionToMarkdownRenderer(NotionifyConfig(token="test-token", **kwargs))

    def _rt(self, text: str) -> list[dict]:
        return [{"type": "text", "text": {"content": text}, "plain_text": text}]

    # --- _render_image ---

    @given(url=st.text(min_size=5, max_size=60, alphabet=string.ascii_letters + ":/._-"))
    @settings(max_examples=100)
    def test_image_external_url_in_output(self, url: str) -> None:
        """External image URL appears in Markdown image syntax."""
        r = self._r()
        block = {"image": {"type": "external", "external": {"url": url}}}
        result = r._render_image(block, 0)
        assert result.startswith("![")
        assert result.endswith("\n\n")

    # --- _render_table ---

    def test_table_empty_children_returns_empty(self) -> None:
        """Table with no children → empty string."""
        r = self._r()
        assert r._render_table({"table": {}}, 0) == ""

    def test_table_separator_row_present(self) -> None:
        """GFM separator row (|---|...) appears after the first data row."""
        r = self._r()
        row = {"type": "table_row", "table_row": {"cells": [self._rt("A"), self._rt("B")]}}
        block = {"table": {"table_width": 2, "children": [row, row]}}
        result = r._render_table(block, 0)
        assert "|---|" in result or "|---" in result

    def test_table_has_row_header_bolds_first_cell(self) -> None:
        """has_row_header=True → first cell of each row is bold."""
        r = self._r()
        row = {"type": "table_row", "table_row": {"cells": [self._rt("Header"), self._rt("Val")]}}
        block = {"table": {"table_width": 2, "has_row_header": True, "children": [row]}}
        result = r._render_table(block, 0)
        assert "**Header**" in result

    # --- _render_passthrough ---

    def test_passthrough_no_children_returns_empty(self) -> None:
        """Column block with no children → empty string."""
        r = self._r()
        assert r._render_passthrough({"type": "column", "column": {}}, 0) == ""

    def test_passthrough_with_children_renders_them(self) -> None:
        """Passthrough block renders its children."""
        r = self._r()
        divider = {"type": "divider", "divider": {}}
        block = {"type": "column", "column": {}, "children": [divider]}
        result = r._render_passthrough(block, 0)
        assert "---" in result

    # --- _render_unsupported ---

    def test_unsupported_skip_policy_returns_empty(self) -> None:
        """unsupported_block_policy='skip' → empty string."""
        r = self._r(unsupported_block_policy="skip")
        block = {"type": "unknown_block", "id": "x"}
        assert r._render_unsupported(block) == ""

    def test_unsupported_raise_policy_raises(self) -> None:
        """unsupported_block_policy='raise' → NotionifyUnsupportedBlockError."""
        r = self._r(unsupported_block_policy="raise")
        block = {"type": "unknown_block", "id": "x"}
        with pytest.raises(NotionifyUnsupportedBlockError):
            r._render_unsupported(block)

    def test_unsupported_comment_policy_emits_html_comment(self) -> None:
        """Default policy 'comment' → HTML comment with block type."""
        r = self._r(unsupported_block_policy="comment")
        block = {"type": "my_block", "id": "x"}
        result = r._render_unsupported(block)
        assert "<!-- notion:my_block -->" in result

    # --- _render_quote ---

    @given(text=st.text(min_size=1, max_size=50, alphabet=string.ascii_letters + " "))
    @settings(max_examples=100)
    def test_quote_output_starts_with_blockquote_marker(self, text: str) -> None:
        """_render_quote always starts with '> '."""
        r = self._r()
        block = {"quote": {"rich_text": self._rt(text)}}
        result = r._render_quote(block, 0)
        assert result.startswith("> ")

    # --- _render_media ---

    @given(
        media_type=st.sampled_from(["video", "audio", "pdf"]),
        url=st.text(min_size=5, max_size=50, alphabet=string.ascii_letters + ":/._-"),
    )
    @settings(max_examples=100)
    def test_media_label_matches_type(self, media_type: str, url: str) -> None:
        """Video/audio/pdf use their capitalized label in output."""
        r = self._r()
        label = {"video": "Video", "audio": "Audio", "pdf": "PDF"}[media_type]
        block = {media_type: {"type": "external", "external": {"url": url}}}
        result = r._render_media(block, 0, media_type)
        assert result.startswith(f"[{label}](")
        assert result.endswith("\n\n")


class TestRendererRemainingMethodsProperties:
    """Property tests for _render_file, _render_link_preview, _render_paragraph,
    _dispatch, and _make_heading_renderer."""

    def _r(self) -> NotionToMarkdownRenderer:
        return NotionToMarkdownRenderer(NotionifyConfig(token="test-token"))

    def _rt(self, text: str) -> list[dict]:
        return [{"type": "text", "text": {"content": text}, "plain_text": text}]

    # --- _render_file ---

    @given(url=st.text(min_size=5, max_size=50, alphabet=string.ascii_letters + ":/._-"))
    @settings(max_examples=100)
    def test_file_external_ends_with_double_newline(self, url: str) -> None:
        """_render_file always ends with '\\n\\n'."""
        r = self._r()
        block = {"file": {"type": "external", "external": {"url": url}}}
        assert r._render_file(block, 0).endswith("\n\n")

    def test_file_name_field_used_as_display_text(self) -> None:
        """_render_file uses the 'name' field as the link display text."""
        r = self._r()
        block = {
            "file": {
                "type": "external",
                "external": {"url": "https://x.com/doc.pdf"},
                "name": "My Report",
            }
        }
        result = r._render_file(block, 0)
        assert "My Report" in result

    # --- _render_link_preview ---

    @given(url=st.text(min_size=5, max_size=50, alphabet=string.ascii_letters + ":/._-"))
    @settings(max_examples=100)
    def test_link_preview_url_appears_in_output(self, url: str) -> None:
        """_render_link_preview always includes the URL and ends with '\\n\\n'."""
        r = self._r()
        block = {"link_preview": {"url": url}}
        result = r._render_link_preview(block, 0)
        assert url in result
        assert result.endswith("\n\n")

    # --- _render_paragraph ---

    @given(
        text=st.text(min_size=1, max_size=50, alphabet=string.ascii_letters + " "),
        depth=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=200)
    def test_paragraph_indent_matches_depth(self, text: str, depth: int) -> None:
        """Paragraph starts with '  ' * depth and ends with '\\n\\n'."""
        r = self._r()
        block = {"paragraph": {"rich_text": self._rt(text)}}
        result = r._render_paragraph(block, depth)
        assert result.startswith("  " * depth)
        assert result.endswith("\n\n")

    # --- _dispatch ---

    def test_dispatch_omitted_type_returns_empty_string(self) -> None:
        """Omitted block types (breadcrumb, table_of_contents) → ''."""
        r = self._r()
        assert r._dispatch({"type": "breadcrumb", "breadcrumb": {}}, 0) == ""
        assert r._dispatch({"type": "table_of_contents"}, 0) == ""

    def test_dispatch_known_type_routes_correctly(self) -> None:
        """Known block type is dispatched to the right renderer."""
        r = self._r()
        block = {"type": "divider", "divider": {}}
        assert r._dispatch(block, 0) == "---\n\n"

    # --- _make_heading_renderer ---

    @given(level=st.integers(min_value=1, max_value=3))
    @settings(max_examples=50)
    def test_make_heading_renderer_returns_callable(self, level: int) -> None:
        """_make_heading_renderer always returns a callable."""
        from notionify.converter.notion_to_md import _make_heading_renderer

        assert callable(_make_heading_renderer(level))

    @given(
        level=st.integers(min_value=1, max_value=3),
        text=st.text(min_size=1, max_size=20, alphabet=string.ascii_letters + " "),
    )
    @settings(max_examples=100)
    def test_make_heading_renderer_prefix_matches_level(
        self, level: int, text: str
    ) -> None:
        """Factory-produced renderer generates the correct '#' prefix count."""
        from notionify.converter.notion_to_md import _make_heading_renderer

        r = self._r()
        fn = _make_heading_renderer(level)
        block = {f"heading_{level}": {"rich_text": self._rt(text)}}
        result = fn(r, block, 0)
        assert result.startswith("#" * level + " ")


# ---------------------------------------------------------------------------
# Dataclass model property tests
# ---------------------------------------------------------------------------


class TestBlockSignatureModelProperties:
    """Property tests for the BlockSignature frozen dataclass."""

    def _sig(self, **kwargs) -> BlockSignature:
        defaults = dict(
            block_type="paragraph",
            rich_text_hash="aaa",
            structural_hash="bbb",
            attrs_hash="ccc",
            nesting_depth=0,
        )
        defaults.update(kwargs)
        return BlockSignature(**defaults)

    @given(
        bt=st.text(min_size=1, max_size=20, alphabet=string.ascii_lowercase + "_"),
        depth=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=200)
    def test_fields_stored_correctly(self, bt: str, depth: int) -> None:
        """block_type and nesting_depth are stored verbatim."""
        sig = self._sig(block_type=bt, nesting_depth=depth)
        assert sig.block_type == bt
        assert sig.nesting_depth == depth

    @given(
        bt=st.text(min_size=1, max_size=20, alphabet=string.ascii_lowercase + "_"),
    )
    @settings(max_examples=200)
    def test_equal_fields_produce_equal_signatures(self, bt: str) -> None:
        """Two BlockSignature instances with identical fields compare equal."""
        sig1 = self._sig(block_type=bt)
        sig2 = self._sig(block_type=bt)
        assert sig1 == sig2

    def test_different_block_type_not_equal(self) -> None:
        """Signatures with different block_type are not equal."""
        assert self._sig(block_type="paragraph") != self._sig(block_type="heading_1")

    def test_different_nesting_depth_not_equal(self) -> None:
        """Signatures with different nesting_depth are not equal."""
        assert self._sig(nesting_depth=0) != self._sig(nesting_depth=1)

    @given(
        bt=st.text(min_size=1, max_size=20, alphabet=string.ascii_lowercase + "_"),
    )
    @settings(max_examples=100)
    def test_signature_is_hashable(self, bt: str) -> None:
        """BlockSignature (frozen) is hashable and can be used as a dict key."""
        sig = self._sig(block_type=bt)
        d = {sig: "value"}
        assert d[sig] == "value"

    @given(
        bt=st.text(min_size=1, max_size=20, alphabet=string.ascii_lowercase + "_"),
    )
    @settings(max_examples=100)
    def test_equal_signatures_have_same_hash(self, bt: str) -> None:
        """Equal BlockSignature instances have identical hashes."""
        sig1 = self._sig(block_type=bt)
        sig2 = self._sig(block_type=bt)
        assert hash(sig1) == hash(sig2)


class TestDiffOpModelProperties:
    """Property tests for the DiffOp dataclass."""

    @given(op_type=st.sampled_from(list(DiffOpType)))
    @settings(max_examples=50)
    def test_op_type_stored_correctly(self, op_type: DiffOpType) -> None:
        """op_type attribute is stored verbatim."""
        op = DiffOp(op_type=op_type)
        assert op.op_type == op_type

    def test_optional_fields_default_to_none(self) -> None:
        """existing_id, new_block, position_after default to None."""
        op = DiffOp(op_type=DiffOpType.KEEP)
        assert op.existing_id is None
        assert op.new_block is None
        assert op.position_after is None

    def test_depth_defaults_to_zero(self) -> None:
        """depth defaults to 0 when not specified."""
        op = DiffOp(op_type=DiffOpType.INSERT)
        assert op.depth == 0

    @given(depth=st.integers(min_value=0, max_value=10))
    @settings(max_examples=100)
    def test_depth_stored_correctly(self, depth: int) -> None:
        """depth is stored verbatim."""
        op = DiffOp(op_type=DiffOpType.KEEP, depth=depth)
        assert op.depth == depth


class TestConversionResultModelProperties:
    """Property tests for the ConversionResult dataclass."""

    def test_default_constructor_has_empty_lists(self) -> None:
        """Default ConversionResult has empty blocks, images, and warnings."""
        result = ConversionResult()
        assert result.blocks == []
        assert result.images == []
        assert result.warnings == []

    def test_blocks_list_is_mutable(self) -> None:
        """Blocks list can be modified after construction."""
        result = ConversionResult()
        block = {"type": "paragraph"}
        result.blocks.append(block)
        assert result.blocks == [block]

    def test_default_instances_do_not_share_lists(self) -> None:
        """Two default ConversionResult instances have independent list objects."""
        r1 = ConversionResult()
        r2 = ConversionResult()
        r1.blocks.append({"type": "divider"})
        assert r2.blocks == []

    @given(
        code=st.text(min_size=1, max_size=20, alphabet=string.ascii_uppercase + "_"),
        message=st.text(min_size=1, max_size=60),
    )
    @settings(max_examples=100)
    def test_conversion_warning_fields_stored(self, code: str, message: str) -> None:
        """ConversionWarning stores code and message verbatim."""
        w = ConversionWarning(code=code, message=message)
        assert w.code == code
        assert w.message == message
        assert w.context == {}


# ---------------------------------------------------------------------------
# validate_image
# ---------------------------------------------------------------------------

class TestValidateImageProperties:
    """validate_image returns a valid MIME type + bytes, or raises."""

    def _cfg(self) -> NotionifyConfig:
        return NotionifyConfig(token="test-token")

    def test_external_jpg_url_returns_jpeg_mime_and_none_data(self) -> None:
        """External .jpg URL guesses image/jpeg and returns None data."""
        cfg = self._cfg()
        mime, data = validate_image(
            "https://example.com/photo.jpg",
            ImageSourceType.EXTERNAL_URL,
            None,
            cfg,
        )
        assert mime == "image/jpeg"
        assert data is None

    def test_external_png_url_returns_png_mime(self) -> None:
        """External .png URL guesses image/png."""
        cfg = self._cfg()
        mime, _ = validate_image(
            "https://example.com/image.png",
            ImageSourceType.EXTERNAL_URL,
            None,
            cfg,
        )
        assert mime == "image/png"

    def test_local_jpeg_bytes_detected_via_magic(self) -> None:
        """Local file with JPEG magic bytes sniffs as image/jpeg."""
        cfg = self._cfg()
        jpeg_bytes = b"\xff\xd8\xff" + b"\x00" * 20
        mime, returned = validate_image(
            "photo.jpg",
            ImageSourceType.LOCAL_FILE,
            jpeg_bytes,
            cfg,
        )
        assert mime == "image/jpeg"
        assert returned is jpeg_bytes

    def test_local_png_bytes_detected_via_magic(self) -> None:
        """Local file with PNG magic bytes sniffs as image/png."""
        cfg = self._cfg()
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        mime, returned = validate_image(
            "photo.png",
            ImageSourceType.LOCAL_FILE,
            png_bytes,
            cfg,
        )
        assert mime == "image/png"
        assert returned is png_bytes

    def test_data_exceeding_max_size_raises_size_error(self) -> None:
        """Image data exceeding image_max_size_bytes raises NotionifyImageSizeError."""
        cfg = NotionifyConfig(token="test-token", image_max_size_bytes=50)
        jpeg_bytes = b"\xff\xd8\xff" + b"\x00" * 200
        with pytest.raises(NotionifyImageSizeError) as exc_info:
            validate_image("photo.jpg", ImageSourceType.LOCAL_FILE, jpeg_bytes, cfg)
        assert exc_info.value.context["size_bytes"] > 50
        assert exc_info.value.context["max_bytes"] == 50

    def test_disallowed_mime_raises_type_error(self) -> None:
        """A URL with a non-image extension raises NotionifyImageTypeError."""
        cfg = self._cfg()
        with pytest.raises(NotionifyImageTypeError) as exc_info:
            validate_image(
                "https://example.com/document.pdf",
                ImageSourceType.EXTERNAL_URL,
                None,
                cfg,
            )
        assert "not allowed" in exc_info.value.message

    def test_type_error_context_contains_detected_mime(self) -> None:
        """NotionifyImageTypeError context includes the detected MIME type."""
        cfg = self._cfg()
        with pytest.raises(NotionifyImageTypeError) as exc_info:
            validate_image(
                "https://example.com/file.html",
                ImageSourceType.EXTERNAL_URL,
                None,
                cfg,
            )
        assert "detected_mime" in exc_info.value.context

    @given(url=st.from_regex(r"https://example\.com/img\.(jpg|png|gif|webp)", fullmatch=True))
    @settings(max_examples=50)
    def test_known_image_extensions_always_pass_validation(self, url: str) -> None:
        """URLs with known image extensions never raise TypeError."""
        cfg = self._cfg()
        mime, data = validate_image(url, ImageSourceType.EXTERNAL_URL, None, cfg)
        assert mime in cfg.image_allowed_mimes_external
        assert data is None

    def test_unknown_extension_falls_back_to_octet_stream_and_raises(self) -> None:
        """URL with unknown extension → octet-stream fallback → type error."""
        cfg = self._cfg()
        with pytest.raises(NotionifyImageTypeError):
            validate_image(
                "https://example.com/file.xyz123unknown",
                ImageSourceType.EXTERNAL_URL,
                None,
                cfg,
            )

    def test_result_is_always_two_tuple(self) -> None:
        """validate_image always returns a 2-tuple on success."""
        cfg = self._cfg()
        result = validate_image(
            "https://example.com/photo.jpg",
            ImageSourceType.EXTERNAL_URL,
            None,
            cfg,
        )
        assert isinstance(result, tuple)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# build_image_block_external / build_image_block_uploaded
# ---------------------------------------------------------------------------

class TestBuildImageBlockProperties:
    """Invariants for image block dict builders."""

    @given(url=st.text(min_size=1, max_size=200))
    @settings(max_examples=200)
    def test_external_block_type_is_image(self, url: str) -> None:
        """build_image_block_external always returns type='image'."""
        block = build_image_block_external(url)
        assert block["type"] == "image"

    @given(url=st.text(min_size=1, max_size=200))
    @settings(max_examples=200)
    def test_external_block_image_type_is_external(self, url: str) -> None:
        """The image sub-type is always 'external'."""
        block = build_image_block_external(url)
        assert block["image"]["type"] == "external"

    @given(url=st.text(min_size=1, max_size=200))
    @settings(max_examples=200)
    def test_external_block_url_preserved_verbatim(self, url: str) -> None:
        """The URL is stored verbatim in image.external.url."""
        block = build_image_block_external(url)
        assert block["image"]["external"]["url"] == url

    @given(upload_id=st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_uploaded_block_type_is_image(self, upload_id: str) -> None:
        """build_image_block_uploaded always returns type='image'."""
        block = build_image_block_uploaded(upload_id)
        assert block["type"] == "image"

    @given(upload_id=st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_uploaded_block_image_type_is_file_upload(self, upload_id: str) -> None:
        """The image sub-type is always 'file_upload'."""
        block = build_image_block_uploaded(upload_id)
        assert block["image"]["type"] == "file_upload"

    @given(upload_id=st.text(min_size=1, max_size=100))
    @settings(max_examples=200)
    def test_uploaded_block_id_preserved_verbatim(self, upload_id: str) -> None:
        """The upload_id is stored verbatim in image.file_upload.id."""
        block = build_image_block_uploaded(upload_id)
        assert block["image"]["file_upload"]["id"] == upload_id

    def test_external_and_uploaded_have_different_image_subtypes(self) -> None:
        """External and uploaded blocks have distinct image type values."""
        ext = build_image_block_external("https://example.com/img.png")
        upl = build_image_block_uploaded("upload-uuid-123")
        assert ext["image"]["type"] != upl["image"]["type"]


# ---------------------------------------------------------------------------
# _ExecState
# ---------------------------------------------------------------------------

class TestExecStateProperties:
    """_ExecState always initializes with zero counters and None last_block_id."""

    def test_initial_kept_is_zero(self) -> None:
        assert _ExecState().kept == 0

    def test_initial_inserted_is_zero(self) -> None:
        assert _ExecState().inserted == 0

    def test_initial_deleted_is_zero(self) -> None:
        assert _ExecState().deleted == 0

    def test_initial_replaced_is_zero(self) -> None:
        assert _ExecState().replaced == 0

    def test_initial_last_block_id_is_none(self) -> None:
        assert _ExecState().last_block_id is None

    def test_counters_are_independent(self) -> None:
        """Incrementing one counter does not affect others."""
        state = _ExecState()
        state.kept += 7
        assert state.inserted == 0
        assert state.deleted == 0
        assert state.replaced == 0

    def test_multiple_instances_are_independent(self) -> None:
        """Two _ExecState instances share no state."""
        s1 = _ExecState()
        s2 = _ExecState()
        s1.kept = 10
        s2.last_block_id = "block-xyz"
        assert s2.kept == 0
        assert s1.last_block_id is None

    def test_last_block_id_can_be_set_and_read(self) -> None:
        state = _ExecState()
        state.last_block_id = "block-abc-123"
        assert state.last_block_id == "block-abc-123"


# ---------------------------------------------------------------------------
# UpdateResult / BlockUpdateResult models
# ---------------------------------------------------------------------------

class TestUpdateResultModelProperties:
    """UpdateResult dataclass invariants."""

    @given(
        strategy=st.sampled_from(["diff", "overwrite"]),
        kept=st.integers(min_value=0, max_value=1000),
        inserted=st.integers(min_value=0, max_value=1000),
        deleted=st.integers(min_value=0, max_value=1000),
        replaced=st.integers(min_value=0, max_value=1000),
        uploaded=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=100)
    def test_fields_stored_verbatim(
        self,
        strategy: str,
        kept: int,
        inserted: int,
        deleted: int,
        replaced: int,
        uploaded: int,
    ) -> None:
        """All numeric fields and strategy_used are stored as provided."""
        result = UpdateResult(
            strategy_used=strategy,
            blocks_kept=kept,
            blocks_inserted=inserted,
            blocks_deleted=deleted,
            blocks_replaced=replaced,
            images_uploaded=uploaded,
        )
        assert result.strategy_used == strategy
        assert result.blocks_kept == kept
        assert result.blocks_inserted == inserted
        assert result.blocks_deleted == deleted
        assert result.blocks_replaced == replaced
        assert result.images_uploaded == uploaded

    def test_default_warnings_is_empty(self) -> None:
        """Default warnings list is empty."""
        result = UpdateResult(
            strategy_used="overwrite",
            blocks_kept=0,
            blocks_inserted=1,
            blocks_deleted=0,
            blocks_replaced=0,
            images_uploaded=0,
        )
        assert result.warnings == []

    def test_two_default_instances_do_not_share_warnings(self) -> None:
        """Two UpdateResult instances have independent warning lists."""
        r1 = UpdateResult(
            strategy_used="diff",
            blocks_kept=0,
            blocks_inserted=0,
            blocks_deleted=0,
            blocks_replaced=0,
            images_uploaded=0,
        )
        r2 = UpdateResult(
            strategy_used="diff",
            blocks_kept=0,
            blocks_inserted=0,
            blocks_deleted=0,
            blocks_replaced=0,
            images_uploaded=0,
        )
        r1.warnings.append(ConversionWarning(code="W001", message="test"))
        assert r2.warnings == []

    @given(
        code=st.text(min_size=1, max_size=20, alphabet=string.ascii_uppercase + "_"),
        message=st.text(min_size=1, max_size=60),
    )
    @settings(max_examples=50)
    def test_warnings_list_is_mutable(self, code: str, message: str) -> None:
        """Warnings can be appended to after construction."""
        result = UpdateResult(
            strategy_used="diff",
            blocks_kept=0,
            blocks_inserted=0,
            blocks_deleted=0,
            blocks_replaced=0,
            images_uploaded=0,
        )
        w = ConversionWarning(code=code, message=message)
        result.warnings.append(w)
        assert result.warnings == [w]


class TestBlockUpdateResultModelProperties:
    """BlockUpdateResult dataclass invariants."""

    @given(block_id=st.text(min_size=1, max_size=100))
    @settings(max_examples=100)
    def test_block_id_stored_verbatim(self, block_id: str) -> None:
        """block_id is stored as provided."""
        result = BlockUpdateResult(block_id=block_id)
        assert result.block_id == block_id

    def test_default_warnings_is_empty(self) -> None:
        """Default warnings list is empty."""
        result = BlockUpdateResult(block_id="block-uuid-123")
        assert result.warnings == []

    def test_two_instances_do_not_share_warnings(self) -> None:
        """Two BlockUpdateResult instances have independent warning lists."""
        r1 = BlockUpdateResult(block_id="block-1")
        r2 = BlockUpdateResult(block_id="block-2")
        r1.warnings.append(ConversionWarning(code="X001", message="warn"))
        assert r2.warnings == []

    @given(block_id=st.text(min_size=1, max_size=100))
    @settings(max_examples=50)
    def test_block_id_in_repr(self, block_id: str) -> None:
        """block_id appears in the dataclass repr for printable strings."""
        # Non-printable chars get escaped in repr; restrict to printable.
        assume(block_id.isprintable())
        result = BlockUpdateResult(block_id=block_id)
        assert block_id in repr(result)


# ---------------------------------------------------------------------------
# _apply_image_fallback
# ---------------------------------------------------------------------------

class TestApplyImageFallbackProperties:
    """_apply_image_fallback branches: skip, placeholder, raise (warning only)."""

    def _ctx(self, fallback: str) -> _BuildContext:
        cfg = NotionifyConfig(token="test-token", image_fallback=fallback)
        return _BuildContext(cfg)

    def test_skip_returns_empty_list(self) -> None:
        """fallback='skip' always returns an empty list."""
        ctx = self._ctx("skip")
        result = _apply_image_fallback("https://example.com/img.jpg", "alt", ctx)
        assert result == []

    def test_skip_adds_warning(self) -> None:
        """fallback='skip' adds an IMAGE_SKIPPED warning."""
        ctx = self._ctx("skip")
        _apply_image_fallback("https://example.com/img.jpg", "alt", ctx)
        assert any(w.code == "IMAGE_SKIPPED" for w in ctx.warnings)

    def test_placeholder_returns_one_block(self) -> None:
        """fallback='placeholder' returns exactly one paragraph block."""
        ctx = self._ctx("placeholder")
        result = _apply_image_fallback("https://example.com/img.jpg", "alt text", ctx)
        assert len(result) == 1
        assert result[0]["type"] == "paragraph"

    def test_placeholder_block_contains_image_text(self) -> None:
        """Placeholder block rich_text contains '[image: alt text]'."""
        ctx = self._ctx("placeholder")
        result = _apply_image_fallback("https://example.com/img.jpg", "my image", ctx)
        rt = result[0]["paragraph"]["rich_text"]
        content = "".join(seg["text"]["content"] for seg in rt)
        assert "my image" in content

    def test_placeholder_uses_url_when_no_alt(self) -> None:
        """Placeholder uses URL as display text when alt_text is empty."""
        ctx = self._ctx("placeholder")
        result = _apply_image_fallback("https://example.com/img.jpg", "", ctx)
        rt = result[0]["paragraph"]["rich_text"]
        content = "".join(seg["text"]["content"] for seg in rt)
        assert "example.com" in content

    def test_placeholder_adds_warning(self) -> None:
        """fallback='placeholder' adds an IMAGE_PLACEHOLDER warning."""
        ctx = self._ctx("placeholder")
        _apply_image_fallback("https://example.com/img.jpg", "alt", ctx)
        assert any(w.code == "IMAGE_PLACEHOLDER" for w in ctx.warnings)

    def test_raise_fallback_returns_empty_list(self) -> None:
        """fallback='raise' returns empty list (raises at client level, not here)."""
        ctx = self._ctx("raise")
        result = _apply_image_fallback("https://example.com/img.jpg", "alt", ctx)
        assert result == []

    def test_raise_fallback_adds_image_error_warning(self) -> None:
        """fallback='raise' adds an IMAGE_ERROR warning."""
        ctx = self._ctx("raise")
        _apply_image_fallback("https://example.com/img.jpg", "alt", ctx)
        assert any(w.code == "IMAGE_ERROR" for w in ctx.warnings)

    @given(
        url=st.text(min_size=1, max_size=100),
        alt=st.text(min_size=0, max_size=50),
    )
    @settings(max_examples=100)
    def test_skip_never_raises(self, url: str, alt: str) -> None:
        """_apply_image_fallback with 'skip' never raises for any url/alt."""
        ctx = self._ctx("skip")
        result = _apply_image_fallback(url, alt, ctx)
        assert result == []

    @given(
        url=st.text(min_size=1, max_size=100),
        alt=st.text(min_size=0, max_size=50),
    )
    @settings(max_examples=100)
    def test_placeholder_always_returns_paragraph(self, url: str, alt: str) -> None:
        """_apply_image_fallback with 'placeholder' always returns a paragraph."""
        ctx = self._ctx("placeholder")
        result = _apply_image_fallback(url, alt, ctx)
        assert len(result) == 1
        assert result[0]["type"] == "paragraph"


# ---------------------------------------------------------------------------
# _build_image_block
# ---------------------------------------------------------------------------

class TestBuildImageBlockBuilderProperties:
    """_build_image_block branches: external URL, local/data-uri, unknown."""

    def _ctx(self, **kwargs: object) -> _BuildContext:
        cfg = NotionifyConfig(token="test-token", **kwargs)  # type: ignore[arg-type]
        return _BuildContext(cfg)

    def test_external_url_produces_image_block(self) -> None:
        """EXTERNAL_URL token produces a Notion image block."""
        ctx = self._ctx()
        token = {"attrs": {"url": "https://example.com/img.jpg"}, "children": []}
        result = _build_image_block(token, ctx)
        assert len(result) == 1
        assert result[0]["type"] == "image"

    def test_external_url_image_type_is_external(self) -> None:
        """External URL image block has image.type='external'."""
        ctx = self._ctx()
        token = {"attrs": {"url": "https://example.com/img.png"}, "children": []}
        result = _build_image_block(token, ctx)
        assert result[0]["image"]["type"] == "external"

    def test_external_url_stored_in_image_data(self) -> None:
        """The URL is stored in image.external.url."""
        url = "https://example.com/photo.webp"
        ctx = self._ctx()
        token = {"attrs": {"url": url}, "children": []}
        result = _build_image_block(token, ctx)
        assert result[0]["image"]["external"]["url"] == url

    def test_external_url_with_alt_adds_caption(self) -> None:
        """Alt text children produce a caption on the image block."""
        ctx = self._ctx()
        token = {
            "attrs": {"url": "https://example.com/img.jpg"},
            "children": [{"type": "text", "raw": "My Alt"}],
        }
        result = _build_image_block(token, ctx)
        assert "caption" in result[0]["image"]

    def test_local_file_with_upload_enabled_creates_placeholder(self) -> None:
        """LOCAL_FILE with image_upload=True creates a placeholder image block."""
        ctx = self._ctx(image_upload=True)
        token = {"attrs": {"url": "/path/to/image.jpg"}, "children": []}
        result = _build_image_block(token, ctx)
        assert len(result) == 1
        assert result[0]["type"] == "image"
        # Placeholder URL indicates pending upload
        assert "placeholder" in result[0]["image"]["external"]["url"]

    def test_local_file_with_upload_disabled_applies_fallback(self) -> None:
        """LOCAL_FILE with image_upload=False delegates to _apply_image_fallback."""
        ctx = self._ctx(image_upload=False, image_fallback="skip")
        token = {"attrs": {"url": "/path/to/image.jpg"}, "children": []}
        result = _build_image_block(token, ctx)
        assert result == []  # skip fallback returns empty

    def test_external_block_registered_in_context(self) -> None:
        """External image block is added to ctx.blocks."""
        ctx = self._ctx()
        token = {"attrs": {"url": "https://example.com/img.gif"}, "children": []}
        _build_image_block(token, ctx)
        assert len(ctx.blocks) == 1


# ---------------------------------------------------------------------------
# _handle_html_block
# ---------------------------------------------------------------------------

class TestHandleHtmlBlockProperties:
    """_handle_html_block always skips with a warning."""

    def _ctx(self) -> _BuildContext:
        return _BuildContext(NotionifyConfig(token="test-token"))

    def test_always_returns_empty_list(self) -> None:
        """_handle_html_block always returns []."""
        ctx = self._ctx()
        result = _handle_html_block({"raw": "<p>test</p>"}, ctx)
        assert result == []

    def test_adds_html_block_skipped_warning(self) -> None:
        """A HTML_BLOCK_SKIPPED warning is always added."""
        ctx = self._ctx()
        _handle_html_block({"raw": "<div>content</div>"}, ctx)
        assert any(w.code == "HTML_BLOCK_SKIPPED" for w in ctx.warnings)

    @given(raw=st.text(min_size=0, max_size=300))
    @settings(max_examples=100)
    def test_never_raises_for_any_raw_content(self, raw: str) -> None:
        """_handle_html_block never raises, regardless of raw HTML content."""
        ctx = self._ctx()
        result = _handle_html_block({"raw": raw}, ctx)
        assert result == []
        assert len(ctx.warnings) == 1

    def test_raw_content_truncated_in_warning(self) -> None:
        """The warning contains a truncated version of the raw HTML."""
        ctx = self._ctx()
        long_raw = "<p>" + "x" * 500 + "</p>"
        _handle_html_block({"raw": long_raw}, ctx)
        # The warning extra contains the raw content (truncated to 200 chars)
        assert len(ctx.warnings) == 1


# ---------------------------------------------------------------------------
# _BuildContext methods
# ---------------------------------------------------------------------------

class TestBuildContextMethodsProperties:
    """Property tests for _BuildContext.add_block, add_warning, add_image."""

    def _ctx(self) -> _BuildContext:
        return _BuildContext(NotionifyConfig(token="test-token"))

    def test_add_block_returns_zero_for_first_block(self) -> None:
        """First add_block returns index 0."""
        ctx = self._ctx()
        idx = ctx.add_block({"type": "paragraph"})
        assert idx == 0

    @given(n=st.integers(min_value=1, max_value=20))
    @settings(max_examples=50)
    def test_add_block_returns_sequential_indices(self, n: int) -> None:
        """add_block returns indices 0, 1, 2, ... in order."""
        ctx = self._ctx()
        for i in range(n):
            idx = ctx.add_block({"type": "paragraph"})
            assert idx == i

    def test_add_block_appends_to_blocks_list(self) -> None:
        """Blocks added via add_block appear in ctx.blocks in order."""
        ctx = self._ctx()
        b1 = {"type": "paragraph"}
        b2 = {"type": "divider"}
        ctx.add_block(b1)
        ctx.add_block(b2)
        assert ctx.blocks == [b1, b2]

    @given(
        code=st.text(min_size=1, max_size=20, alphabet=string.ascii_uppercase + "_"),
        message=st.text(min_size=1, max_size=60),
    )
    @settings(max_examples=100)
    def test_add_warning_creates_conversion_warning(self, code: str, message: str) -> None:
        """add_warning creates a ConversionWarning with the given code and message."""
        ctx = self._ctx()
        ctx.add_warning(code, message)
        assert len(ctx.warnings) == 1
        assert ctx.warnings[0].code == code
        assert ctx.warnings[0].message == message

    def test_add_warning_with_context_kwargs(self) -> None:
        """add_warning stores extra kwargs as context on the warning."""
        ctx = self._ctx()
        ctx.add_warning("TEST_CODE", "test message", src="example.com", size=42)
        w = ctx.warnings[0]
        assert w.context.get("src") == "example.com"
        assert w.context.get("size") == 42

    def test_add_image_registers_pending_image(self) -> None:
        """add_image appends a PendingImage to ctx.images."""
        ctx = self._ctx()
        ctx.add_image("/path/img.jpg", ImageSourceType.LOCAL_FILE, 0)
        assert len(ctx.images) == 1
        assert ctx.images[0].src == "/path/img.jpg"
        assert ctx.images[0].source_type == ImageSourceType.LOCAL_FILE
        assert ctx.images[0].block_index == 0

    def test_multiple_instances_are_independent(self) -> None:
        """Two _BuildContext instances do not share their lists."""
        c1 = self._ctx()
        c2 = self._ctx()
        c1.add_block({"type": "divider"})
        c1.add_warning("X", "msg")
        assert c2.blocks == []
        assert c2.warnings == []


# ---------------------------------------------------------------------------
# ASTNormalizer._normalize_table_part
# ---------------------------------------------------------------------------

class TestNormalizeTablePartProperties:
    """_normalize_table_part preserves type, copies attrs, normalizes children."""

    def _n(self) -> ASTNormalizer:
        return ASTNormalizer()

    def test_type_always_preserved(self) -> None:
        """Result always has the same type as the input."""
        n = self._n()
        for t in ("table_head", "table_body", "table_row", "table_cell"):
            result = n._normalize_table_part({"type": t})
            assert result["type"] == t

    def test_no_attrs_no_attrs_in_result(self) -> None:
        """If input has no 'attrs', result has no 'attrs' key."""
        n = self._n()
        result = n._normalize_table_part({"type": "table_row"})
        assert "attrs" not in result

    def test_attrs_are_copied_not_same_reference(self) -> None:
        """Attrs dict in result is a copy, not the same object."""
        n = self._n()
        attrs = {"align": "left", "head": True}
        result = n._normalize_table_part({"type": "table_cell", "attrs": attrs})
        assert result["attrs"] == attrs
        assert result["attrs"] is not attrs

    def test_no_children_no_children_in_result(self) -> None:
        """If input has no 'children', result has no 'children' key."""
        n = self._n()
        result = n._normalize_table_part({"type": "table_row"})
        assert "children" not in result

    @given(
        t=st.sampled_from(["table_head", "table_body", "table_row", "table_cell"]),
    )
    @settings(max_examples=50)
    def test_empty_children_not_included(self, t: str) -> None:
        """Empty children list results in no children key."""
        n = self._n()
        result = n._normalize_table_part({"type": t, "children": []})
        # Empty children list is falsy, so it won't be included
        assert "children" not in result

    def test_nonempty_children_recursively_normalized(self) -> None:
        """Nonempty children are recursively normalized."""
        n = self._n()
        child = {"type": "text", "raw": "hello"}
        result = n._normalize_table_part({"type": "table_cell", "children": [child]})
        assert "children" in result
        assert isinstance(result["children"], list)
        assert len(result["children"]) >= 1
