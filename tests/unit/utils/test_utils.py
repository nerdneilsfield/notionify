"""Tests for utility functions.

Tests for: chunk_children, split_string, md5_hash, hash_dict, redact.
"""

import hashlib
import json

import pytest

from notionify.utils.chunk import chunk_children
from notionify.utils.hashing import hash_dict, md5_hash
from notionify.utils.redact import redact
from notionify.utils.text_split import split_string

# =========================================================================
# chunk_children tests
# =========================================================================

class TestChunkChildren:
    """Tests for chunk_children utility."""

    def test_empty_list(self):
        assert chunk_children([]) == []

    def test_under_limit(self):
        blocks = [{"type": "paragraph"}] * 50
        result = chunk_children(blocks)
        assert len(result) == 1
        assert len(result[0]) == 50

    def test_at_limit(self):
        blocks = [{"type": "paragraph"}] * 100
        result = chunk_children(blocks)
        assert len(result) == 1
        assert len(result[0]) == 100

    def test_over_limit(self):
        blocks = [{"type": "paragraph"}] * 250
        result = chunk_children(blocks)
        assert len(result) == 3
        assert len(result[0]) == 100
        assert len(result[1]) == 100
        assert len(result[2]) == 50

    def test_custom_size(self):
        blocks = [{"type": "paragraph"}] * 10
        result = chunk_children(blocks, size=3)
        assert len(result) == 4
        assert len(result[0]) == 3
        assert len(result[1]) == 3
        assert len(result[2]) == 3
        assert len(result[3]) == 1

    def test_size_1(self):
        blocks = [{"type": "paragraph"}] * 3
        result = chunk_children(blocks, size=1)
        assert len(result) == 3
        for chunk in result:
            assert len(chunk) == 1

    def test_invalid_size_raises(self):
        with pytest.raises(ValueError, match="size must be >= 1"):
            chunk_children([], size=0)

    def test_negative_size_raises(self):
        with pytest.raises(ValueError, match="size must be >= 1"):
            chunk_children([], size=-1)

    def test_preserves_block_content(self):
        blocks = [{"type": f"block_{i}"} for i in range(5)]
        result = chunk_children(blocks, size=2)
        flat = [item for chunk in result for item in chunk]
        assert flat == blocks

    def test_single_element(self):
        blocks = [{"type": "paragraph"}]
        result = chunk_children(blocks)
        assert len(result) == 1
        assert len(result[0]) == 1


# =========================================================================
# split_string tests
# =========================================================================

class TestSplitString:
    """Tests for split_string utility."""

    def test_empty_string(self):
        assert split_string("") == []

    def test_short_string(self):
        assert split_string("hello", 10) == ["hello"]

    def test_exact_limit(self):
        text = "x" * 2000
        assert split_string(text) == [text]

    def test_over_limit(self):
        text = "a" * 3000
        result = split_string(text)
        assert len(result) == 2
        assert len(result[0]) == 2000
        assert len(result[1]) == 1000
        assert result[0] + result[1] == text

    def test_custom_limit(self):
        result = split_string("hello world", 5)
        assert result == ["hello", " worl", "d"]

    def test_limit_1(self):
        result = split_string("abc", 1)
        assert result == ["a", "b", "c"]

    def test_invalid_limit_raises(self):
        with pytest.raises(ValueError, match="limit must be >= 1"):
            split_string("text", 0)

    def test_negative_limit_raises(self):
        with pytest.raises(ValueError, match="limit must be >= 1"):
            split_string("text", -1)

    def test_unicode_safety(self):
        """Multi-byte characters are never split mid-character."""
        # Each emoji is 1 Python code point
        text = "\U0001f600\U0001f601\U0001f602"
        result = split_string(text, 2)
        assert len(result) == 2
        assert result[0] == "\U0001f600\U0001f601"
        assert result[1] == "\U0001f602"
        assert result[0] + result[1] == text

    def test_cjk_characters(self):
        text = "\u4f60\u597d\u4e16\u754c\u548c\u5e73"
        result = split_string(text, 3)
        assert len(result) == 2
        assert result[0] + result[1] == text

    def test_concatenation_preserves_content(self):
        text = "The quick brown fox jumps over the lazy dog."
        result = split_string(text, 10)
        assert "".join(result) == text

    def test_default_limit_2000(self):
        text = "a" * 2001
        result = split_string(text)
        assert len(result) == 2
        assert len(result[0]) == 2000
        assert len(result[1]) == 1


# =========================================================================
# md5_hash tests
# =========================================================================

class TestMd5Hash:
    """Tests for md5_hash utility."""

    def test_known_hash(self):
        """md5('hello') is a well-known value."""
        assert md5_hash("hello") == "5d41402abc4b2a76b9719d911017c592"

    def test_empty_string(self):
        expected = hashlib.md5(b"").hexdigest()
        assert md5_hash("") == expected

    def test_unicode_input(self):
        text = "\u4f60\u597d"
        expected = hashlib.md5(text.encode("utf-8")).hexdigest()
        assert md5_hash(text) == expected

    def test_deterministic(self):
        assert md5_hash("test") == md5_hash("test")

    def test_different_inputs_different_hashes(self):
        assert md5_hash("a") != md5_hash("b")

    def test_hash_length(self):
        result = md5_hash("anything")
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)


# =========================================================================
# hash_dict tests
# =========================================================================

class TestHashDict:
    """Tests for hash_dict utility."""

    def test_deterministic(self):
        d = {"a": 1, "b": 2}
        assert hash_dict(d) == hash_dict(d)

    def test_key_order_independent(self):
        d1 = {"b": 2, "a": 1}
        d2 = {"a": 1, "b": 2}
        assert hash_dict(d1) == hash_dict(d2)

    def test_different_dicts_different_hashes(self):
        d1 = {"a": 1}
        d2 = {"a": 2}
        assert hash_dict(d1) != hash_dict(d2)

    def test_nested_dict(self):
        d = {"a": {"b": 1, "c": 2}}
        result = hash_dict(d)
        assert len(result) == 32

    def test_empty_dict(self):
        result = hash_dict({})
        expected = md5_hash(json.dumps({}, sort_keys=True, ensure_ascii=False))
        assert result == expected

    def test_unicode_values(self):
        d = {"name": "\u4f60\u597d"}
        result = hash_dict(d)
        assert len(result) == 32


# =========================================================================
# redact tests
# =========================================================================

class TestRedact:
    """Tests for redact utility."""

    def test_authorization_header_redacted(self):
        payload = {"Authorization": "Bearer ntn_secret123456"}
        result = redact(payload)
        assert "ntn_secret123456" not in result["Authorization"]
        assert "<redacted>" in result["Authorization"]

    def test_token_key_redacted_with_known_token(self):
        """When the token is provided, sensitive key values are scrubbed."""
        token = "my-secret-token"
        payload = {"token": token}
        result = redact(payload, token=token)
        assert token not in result["token"]
        # last 4 chars shown
        assert "oken" in result["token"]

    def test_token_key_with_bearer_prefix(self):
        """Sensitive keys with Bearer prefix are redacted even without explicit token."""
        payload = {"token": "Bearer ntn_abc123"}
        result = redact(payload)
        assert "ntn_abc123" not in result["token"]

    def test_password_key_redacted_with_token(self):
        """Password key is scrubbed when its value matches the known token."""
        token = "p@ssw0rd"
        payload = {"password": token}
        result = redact(payload, token=token)
        assert token not in result["password"]

    def test_sensitive_key_non_string_redacted(self):
        """Non-string sensitive values are replaced with <redacted>."""
        payload = {"password": 12345}
        result = redact(payload)
        assert result["password"] == "<redacted>"

    def test_data_uri_replaced(self):
        b64_data = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        payload = {"img": f"data:image/png;base64,{b64_data}"}
        result = redact(payload)
        assert "data:image/png;base64," not in result["img"]
        assert "<data_uri:" in result["img"]
        assert "_bytes>" in result["img"]

    def test_token_scrubbed_from_values(self):
        token = "ntn_secret_very_long_token_value"
        payload = {"url": f"https://api.notion.com?token={token}"}
        result = redact(payload, token=token)
        assert token not in result["url"]

    def test_original_not_mutated(self):
        payload = {"Authorization": "Bearer secret123"}
        original_value = payload["Authorization"]
        redact(payload)
        assert payload["Authorization"] == original_value

    def test_nested_dict_redacted(self):
        payload = {
            "headers": {
                "Authorization": "Bearer secret123",
            },
            "body": {"text": "safe"},
        }
        result = redact(payload)
        assert "secret123" not in str(result["headers"]["Authorization"])
        assert result["body"]["text"] == "safe"

    def test_list_values_redacted(self):
        token = "my_secret"
        payload = {"items": [f"prefix-{token}-suffix"]}
        result = redact(payload, token=token)
        assert token not in str(result["items"])

    def test_bytes_value_redacted(self):
        payload = {"data": b"\x89PNG\r\n\x1a\n" * 100}
        result = redact(payload)
        assert "<binary:" in result["data"]
        assert "_bytes>" in result["data"]

    def test_non_sensitive_keys_preserved(self):
        payload = {"name": "test", "count": 42}
        result = redact(payload)
        assert result["name"] == "test"
        assert result["count"] == 42

    def test_empty_dict(self):
        result = redact({})
        assert result == {}

    def test_bearer_pattern_in_sensitive_key(self):
        """Bearer pattern in a sensitive key is redacted without explicit token."""
        payload = {"Authorization": "Bearer sk_live_abcdef123456"}
        result = redact(payload)
        assert "sk_live_abcdef123456" not in result["Authorization"]
        assert "<redacted>" in result["Authorization"]

    def test_bearer_pattern_with_explicit_token(self):
        """Bearer pattern with explicit token scrubs the token from all values."""
        token = "sk_live_abcdef123456"
        payload = {"header": f"Bearer {token}"}
        result = redact(payload, token=token)
        assert token not in result["header"]

    def test_token_suffix_shown(self):
        """Redaction shows last 4 chars of a known token."""
        token = "ntn_abcdef123456"
        payload = {"api_key": token}
        result = redact(payload, token=token)
        # The last 4 chars should appear after "redacted:..."
        assert "3456" in result["api_key"]

    def test_invalid_base64_data_uri_falls_back_to_estimate(self):
        """_estimate_data_uri_bytes falls back to byte estimate on invalid base64."""
        from notionify.utils.redact import _estimate_data_uri_bytes
        # Construct a data URI with padding chars that fail strict validate=True
        bad_uri = "data:image/png;base64,!!!notvalidbase64!!!"
        # Should not raise — returns a rough estimate
        result = _estimate_data_uri_bytes(bad_uri)
        assert isinstance(result, int)
        assert result >= 0

    def test_binary_looking_string_redacted(self):
        """Strings with >10% non-printable chars are replaced with <binary:N_bytes>."""
        # Build a string > 256 chars with many non-printable characters
        binary_str = "\x00\x01\x02\x03" * 70  # 280 chars, all non-printable
        payload = {"data": binary_str}
        result = redact(payload)
        assert "<binary:" in result["data"]

    def test_short_binary_string_not_redacted(self):
        """Strings below the binary threshold are not replaced."""
        short_binary = "\x00\x01\x02"  # only 3 chars, below threshold of 256
        payload = {"data": short_binary}
        result = redact(payload)
        assert result["data"] == short_binary
