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
from notionify.converter.md_to_notion import MarkdownToNotionConverter
from notionify.converter.rich_text import split_rich_text
from notionify.diff.signature import compute_signature
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
        with pytest.raises(ValueError):
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
        with pytest.raises(ValueError):
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
