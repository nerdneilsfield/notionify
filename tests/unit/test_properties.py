"""Property-based tests for notionify SDK using Hypothesis.

These tests verify algebraic / invariant properties of core utility and
converter functions.  They complement the example-based unit tests by
exercising the code with a wide range of randomly generated inputs.
"""

from __future__ import annotations

import copy
import re
import string
from datetime import datetime
from datetime import timezone as _tz

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from notionify.config import NotionifyConfig
from notionify.converter.ast_normalizer import ASTNormalizer
from notionify.converter.block_builder import (
    _LANGUAGE_ALIASES,
    _NOTION_LANGUAGES,
    _normalize_language,
)
from notionify.converter.inline_renderer import markdown_escape, render_rich_text
from notionify.converter.math import EQUATION_CHAR_LIMIT, build_block_math, build_inline_math
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import NotionToMarkdownRenderer
from notionify.converter.rich_text import build_rich_text, extract_text, split_rich_text
from notionify.converter.tables import build_table
from notionify.diff.conflict import detect_conflict, take_snapshot
from notionify.diff.lcs_matcher import lcs_match
from notionify.diff.planner import DiffPlanner
from notionify.diff.signature import compute_signature
from notionify.image.detect import detect_image_source, mime_to_extension
from notionify.models import BlockSignature, DiffOp, DiffOpType, ImageSourceType
from notionify.notion_api.retries import compute_backoff, should_retry
from notionify.utils.chunk import chunk_children
from notionify.utils.hashing import hash_dict, md5_hash
from notionify.utils.redact import _SENSITIVE_KEY_PATTERNS, redact
from notionify.utils.text_split import split_string

# ---------------------------------------------------------------------------
# Reusable strategies
# ---------------------------------------------------------------------------

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
            table_fallback="skip",
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
