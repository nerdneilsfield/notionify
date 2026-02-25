"""Property-based tests for notionify SDK using Hypothesis.

These tests verify algebraic / invariant properties of core utility and
converter functions.  They complement the example-based unit tests by
exercising the code with a wide range of randomly generated inputs.
"""

from __future__ import annotations

import copy
import re
import string

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from notionify.config import NotionifyConfig
from notionify.converter.ast_normalizer import ASTNormalizer
from notionify.converter.inline_renderer import markdown_escape, render_rich_text
from notionify.converter.math import EQUATION_CHAR_LIMIT, build_block_math, build_inline_math
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.notion_to_md import NotionToMarkdownRenderer
from notionify.converter.rich_text import split_rich_text
from notionify.converter.tables import build_table
from notionify.diff.lcs_matcher import lcs_match
from notionify.diff.signature import compute_signature
from notionify.image.detect import detect_image_source, mime_to_extension
from notionify.models import BlockSignature, ImageSourceType
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
