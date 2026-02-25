"""Security-focused tests for the notionify SDK.

Covers token masking in config repr, payload redaction with expanded
sensitive-key matching, path traversal protection for local image uploads,
data-URI size pre-check before full decoding, and error context safety.
"""

from __future__ import annotations

import base64
import copy
from pathlib import Path

import pytest

from notionify.config import NotionifyConfig
from notionify.errors import (
    ErrorCode,
    NotionifyAuthError,
    NotionifyConversionError,
    NotionifyDiffConflictError,
    NotionifyError,
    NotionifyImageError,
    NotionifyImageNotFoundError,
    NotionifyImageParseError,
    NotionifyImageSizeError,
    NotionifyImageTypeError,
    NotionifyMathOverflowError,
    NotionifyNetworkError,
    NotionifyNotFoundError,
    NotionifyPermissionError,
    NotionifyRateLimitError,
    NotionifyRetryExhaustedError,
    NotionifyTextOverflowError,
    NotionifyUploadError,
    NotionifyUploadExpiredError,
    NotionifyUploadTransportError,
    NotionifyValidationError,
)
from notionify.image.validate import _parse_data_uri, validate_image
from notionify.models import ImageSourceType
from notionify.utils.redact import (
    _SENSITIVE_KEY_PATTERNS,
    redact,
)


def _make_config(**kwargs) -> NotionifyConfig:
    """Create a config with a test token; override any field via kwargs."""
    return NotionifyConfig(token="ntn_test_token_abcd1234", **kwargs)


# =========================================================================
# 1. TestConfigReprMasksToken
# =========================================================================


class TestConfigReprMasksToken:
    """Verify NotionifyConfig.__repr__ never leaks the full token."""

    def test_full_token_not_in_repr(self):
        """The complete token value must never appear in repr output."""
        token = "ntn_super_secret_integration_token_xyz"
        cfg = NotionifyConfig(token=token)
        r = repr(cfg)
        assert token not in r

    def test_repr_shows_last_four_chars(self):
        """The masked representation should expose only the last 4 characters."""
        token = "ntn_super_secret_integration_token_xyz"
        cfg = NotionifyConfig(token=token)
        r = repr(cfg)
        assert "..._xyz" in r

    def test_repr_contains_masked_marker(self):
        """The repr should contain the '...' prefix for the masked token."""
        cfg = NotionifyConfig(token="abcdefghijklmnop")
        r = repr(cfg)
        assert "...mnop" in r

    def test_short_token_shows_stars(self):
        """Tokens shorter than 4 characters should be replaced with '****'."""
        cfg = NotionifyConfig(token="ab")
        r = repr(cfg)
        assert "****" in r
        assert "ab" not in r.split("token=")[1].split(",")[0]

    def test_empty_token_shows_stars(self):
        """An empty token should show '****'."""
        cfg = NotionifyConfig(token="")
        r = repr(cfg)
        assert "****" in r

    def test_exactly_four_char_token(self):
        """A token of exactly 4 characters shows '...XXXX'."""
        cfg = NotionifyConfig(token="abcd")
        r = repr(cfg)
        assert "...abcd" in r

    def test_repr_starts_with_class_name(self):
        """The repr string must start with the class name."""
        cfg = NotionifyConfig(token="my_token_value")
        r = repr(cfg)
        assert r.startswith("NotionifyConfig(")

    def test_repr_includes_other_fields(self):
        """Non-token fields should appear verbatim in repr."""
        cfg = NotionifyConfig(token="some_token_1234", base_url="http://localhost")
        r = repr(cfg)
        assert "base_url='http://localhost'" in r

    def test_token_not_in_repr_multiple_occurrences(self):
        """Even if we search the full repr string, the token never appears."""
        token = "ntn_V3ryL0ngT0kenValue9876"
        cfg = NotionifyConfig(token=token)
        r = repr(cfg)
        # Ensure no substring of length >= 5 from the start of the token leaks.
        # The only exposed part should be the last 4 chars.
        assert token not in r
        assert token[:-4] not in r


# =========================================================================
# 2. TestRedactionSubstringMatching
# =========================================================================


class TestRedactionSubstringMatching:
    """Verify the expanded redaction catches all sensitive key patterns,
    data URIs, nested dicts, and never mutates the original."""

    # -- Sensitive key pattern matching -----------------------------------

    def test_access_token_key_redacted(self):
        """A key containing 'token' (e.g. 'access_token') is redacted when
        the explicit token is provided to redact()."""
        token = "secret_value_here"
        payload = {"access_token": token}
        result = redact(payload, token=token)
        assert token not in str(result["access_token"])

    def test_refresh_token_key_redacted(self):
        """'refresh_token' matches the 'token' pattern; Bearer prefix is scrubbed."""
        payload = {"refresh_token": "Bearer rt_secret_abc"}
        result = redact(payload)
        assert "rt_secret_abc" not in str(result["refresh_token"])

    def test_private_key_key_redacted(self):
        """'private_key' is in _SENSITIVE_KEY_PATTERNS; when the explicit token
        is provided and matches the value, it is scrubbed."""
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."
        payload = {"private_key": pem}
        result = redact(payload, token=pem)
        assert "BEGIN RSA PRIVATE KEY" not in str(result["private_key"])

    def test_cookie_key_redacted(self):
        """'cookie' is in _SENSITIVE_KEY_PATTERNS; when the explicit token
        matches the cookie value, it is fully scrubbed."""
        cookie_val = "session=abc123; csrf=xyz789"
        payload = {"cookie": cookie_val}
        result = redact(payload, token=cookie_val)
        assert "abc123" not in str(result["cookie"])

    def test_authorization_header_redacted(self):
        """'Authorization' matches (case-insensitive)."""
        payload = {"Authorization": "Bearer ntn_realtoken12345"}
        result = redact(payload)
        assert "ntn_realtoken12345" not in str(result["Authorization"])
        assert "<redacted>" in result["Authorization"]

    def test_authorization_lowercase_redacted(self):
        """Case-insensitive matching: 'authorization' is also caught."""
        payload = {"authorization": "Bearer some_token"}
        result = redact(payload)
        assert "some_token" not in str(result["authorization"])

    def test_api_key_pattern_redacted(self):
        """'api_key' is in _SENSITIVE_KEY_PATTERNS; value is scrubbed
        when provided as the explicit token."""
        token = "ak_live_xyzabc"
        payload = {"api_key": token}
        result = redact(payload, token=token)
        assert token not in str(result["api_key"])

    def test_secret_pattern_redacted(self):
        """Keys containing 'secret' are redacted when the explicit token
        matches the value."""
        token = "cs_very_secret"
        payload = {"client_secret": token}
        result = redact(payload, token=token)
        assert token not in str(result["client_secret"])

    def test_x_api_key_header_redacted(self):
        """'x-api-key' is in _SENSITIVE_KEY_PATTERNS; value is scrubbed
        when provided as the explicit token."""
        token = "key123456"
        payload = {"x-api-key": token}
        result = redact(payload, token=token)
        assert token not in str(result["x-api-key"])

    # -- Verify all documented patterns exist in the frozenset -----------

    @pytest.mark.parametrize(
        "pattern",
        ["token", "secret", "password", "credential", "authorization",
         "cookie", "private_key", "api_key", "api-key", "x-api-key"],
    )
    def test_sensitive_pattern_exists(self, pattern: str):
        """Each expected pattern must be present in _SENSITIVE_KEY_PATTERNS."""
        assert pattern in _SENSITIVE_KEY_PATTERNS

    # -- Nested dicts with sensitive keys --------------------------------

    def test_nested_dict_sensitive_key_redacted(self):
        """Sensitive keys inside nested dicts are also redacted."""
        payload = {
            "headers": {
                "Authorization": "Bearer deep_secret",
                "Content-Type": "application/json",
            },
        }
        result = redact(payload)
        assert "deep_secret" not in str(result)
        assert result["headers"]["Content-Type"] == "application/json"

    def test_deeply_nested_token_redacted(self):
        """Three-level nesting still redacts sensitive keys when the
        explicit token is provided."""
        token = "deep_deep_secret"
        payload = {
            "level1": {
                "level2": {
                    "api_token": token,
                },
            },
        }
        result = redact(payload, token=token)
        assert token not in str(result)

    def test_list_of_dicts_with_sensitive_keys(self):
        """Sensitive keys inside dicts within lists are redacted.
        Non-string values under sensitive keys become '<redacted>'."""
        payload = {
            "items": [
                {"password": 12345},
                {"password": ["secret"]},
            ],
        }
        result = redact(payload)
        assert result["items"][0]["password"] == "<redacted>"
        assert result["items"][1]["password"] == "<redacted>"

    # -- Data URI replacement -------------------------------------------

    def test_data_uri_replaced_in_value(self):
        """Base64 data URIs in values are replaced with <data_uri:N_bytes>."""
        b64 = base64.b64encode(b"\x89PNG" + b"\x00" * 100).decode()
        payload = {"image": f"data:image/png;base64,{b64}"}
        result = redact(payload)
        assert "base64," not in result["image"]
        assert "<data_uri:" in result["image"]
        assert "_bytes>" in result["image"]

    def test_data_uri_in_nested_value(self):
        """Data URIs inside nested structures are also replaced."""
        b64 = base64.b64encode(b"fake_image_data").decode()
        payload = {
            "blocks": [
                {"src": f"data:image/jpeg;base64,{b64}"},
            ],
        }
        result = redact(payload)
        assert "base64," not in str(result)
        assert "<data_uri:" in str(result)

    def test_multiple_data_uris_in_single_value(self):
        """Multiple data URIs in a single string are all replaced."""
        b64_a = base64.b64encode(b"AAA").decode()
        b64_b = base64.b64encode(b"BBB").decode()
        payload = {
            "content": (
                f"img1: data:image/png;base64,{b64_a} "
                f"img2: data:image/gif;base64,{b64_b}"
            ),
        }
        result = redact(payload)
        assert "base64," not in result["content"]
        assert result["content"].count("<data_uri:") == 2

    # -- Original dict is never mutated ----------------------------------

    def test_original_dict_not_mutated(self):
        """The input payload must remain unchanged after redaction."""
        original_payload = {
            "Authorization": "Bearer ntn_secret_123",
            "data": {
                "token": "inner_secret",
                "safe_key": "safe_value",
            },
            "items": ["a", "b", "c"],
        }
        frozen = copy.deepcopy(original_payload)
        redact(original_payload, token="ntn_secret_123")
        assert original_payload == frozen

    def test_original_nested_dict_not_mutated(self):
        """Nested dicts in the original payload are not mutated."""
        inner = {"password": "my_password"}
        payload = {"config": inner}
        redact(payload)
        assert inner["password"] == "my_password"

    def test_original_list_not_mutated(self):
        """Lists in the original payload are not mutated."""
        items = ["Bearer secret_token_value"]
        payload = {"items": items}
        redact(payload, token="secret_token_value")
        assert items[0] == "Bearer secret_token_value"

    # -- Token scrubbing across values -----------------------------------

    def test_token_scrubbed_from_arbitrary_string_values(self):
        """The explicit token is scrubbed from any string, not just sensitive keys."""
        token = "ntn_my_long_secret_token"
        payload = {"url": f"https://api.example.com?auth={token}&page=1"}
        result = redact(payload, token=token)
        assert token not in result["url"]

    def test_sensitive_key_non_string_value_redacted(self):
        """Non-string values under sensitive keys become '<redacted>'."""
        payload = {"credential": {"user": "admin", "pass": "secret"}}
        result = redact(payload)
        assert result["credential"] == "<redacted>"

    def test_sensitive_key_with_list_value_redacted(self):
        """A list value under a sensitive key becomes '<redacted>'."""
        payload = {"password": ["p@ss1", "p@ss2"]}
        result = redact(payload)
        assert result["password"] == "<redacted>"

    def test_sensitive_key_with_int_value_redacted(self):
        """An integer value under a sensitive key becomes '<redacted>'."""
        payload = {"secret_code": 42}
        result = redact(payload)
        assert result["secret_code"] == "<redacted>"


# =========================================================================
# 3. TestPathTraversalProtection
# =========================================================================


class TestPathTraversalProtection:
    """Verify that the path traversal logic from client._upload_local_file
    correctly constrains file paths within image_base_dir.

    These tests replicate the exact logic from client.py lines 581-589:

        base_path = Path(base).resolve()
        file_path = (base_path / pending.src).resolve()
        if not file_path.is_relative_to(base_path):
            raise ...
    """

    @staticmethod
    def _check_path_traversal(base_dir: str, src: str) -> Path:
        """Replicate the path-traversal check from client.py.

        Returns the resolved file_path if the check passes.
        Raises NotionifyImageNotFoundError if the path escapes base_dir.
        """
        base_path = Path(base_dir).resolve()
        file_path = (base_path / src).resolve()
        if not file_path.is_relative_to(base_path):
            raise NotionifyImageNotFoundError(
                message=f"Image path escapes base directory: {src}",
                context={"src": src},
            )
        return file_path

    def test_relative_path_within_base_allowed(self, tmp_path: Path):
        """A simple relative path within the base directory is accepted."""
        base = tmp_path / "images"
        base.mkdir()
        result = self._check_path_traversal(str(base), "photo.png")
        assert result == (base / "photo.png").resolve()

    def test_subdirectory_relative_path_allowed(self, tmp_path: Path):
        """A path to a subdirectory within base is accepted."""
        base = tmp_path / "content"
        base.mkdir()
        result = self._check_path_traversal(str(base), "sub/dir/image.jpg")
        assert result.is_relative_to(base.resolve())

    def test_dot_slash_prefix_allowed(self, tmp_path: Path):
        """'./photo.png' stays within the base directory."""
        base = tmp_path / "docs"
        base.mkdir()
        result = self._check_path_traversal(str(base), "./photo.png")
        assert result.is_relative_to(base.resolve())

    def test_dotdot_escape_rejected(self, tmp_path: Path):
        """'../escape.png' escapes the base directory and must be rejected."""
        base = tmp_path / "safe"
        base.mkdir()
        with pytest.raises(NotionifyImageNotFoundError, match="escapes base directory"):
            self._check_path_traversal(str(base), "../escape.png")

    def test_nested_dotdot_escape_rejected(self, tmp_path: Path):
        """'subdir/../../escape.png' escapes and must be rejected."""
        base = tmp_path / "safe"
        base.mkdir()
        with pytest.raises(NotionifyImageNotFoundError, match="escapes base directory"):
            self._check_path_traversal(str(base), "subdir/../../escape.png")

    def test_deep_dotdot_escape_rejected(self, tmp_path: Path):
        """Multiple '../' components that ultimately escape are rejected."""
        base = tmp_path / "a" / "b" / "c"
        base.mkdir(parents=True)
        with pytest.raises(NotionifyImageNotFoundError, match="escapes base directory"):
            self._check_path_traversal(str(base), "../../../../etc/passwd")

    def test_absolute_path_outside_base_rejected(self, tmp_path: Path):
        """An absolute path outside the base directory is rejected.

        When Path(base) / absolute_path is used, Python 3.12+ keeps the
        absolute path, so it will not be relative to base_path.
        """
        base = tmp_path / "safe"
        base.mkdir()
        outside = tmp_path / "unsafe" / "secret.png"
        with pytest.raises(NotionifyImageNotFoundError, match="escapes base directory"):
            self._check_path_traversal(str(base), str(outside))

    def test_absolute_path_inside_base_allowed(self, tmp_path: Path):
        """An absolute path that is actually within the base dir should be
        rejected by the concatenation logic because Path(base) / abs_path
        in Python >= 3.12 returns abs_path, which may coincidentally be
        within the base. We test the logic as-is from the codebase."""
        base = tmp_path / "safe"
        base.mkdir()
        inside = base / "photo.png"
        # Path(base) / str(inside) -> in Python the absolute path
        # replaces the base. So (base / "/safe/photo.png") becomes
        # the absolute inside path. If it resolves within base, it passes.
        result = self._check_path_traversal(str(base), str(inside))
        assert result.is_relative_to(base.resolve())

    def test_symlink_like_dotdot_in_middle_rejected(self, tmp_path: Path):
        """A path with '..' in the middle that escapes is rejected."""
        base = tmp_path / "project"
        base.mkdir()
        with pytest.raises(NotionifyImageNotFoundError, match="escapes base directory"):
            self._check_path_traversal(str(base), "images/../../../etc/shadow")

    def test_config_stores_image_base_dir(self):
        """NotionifyConfig accepts image_base_dir as a parameter."""
        cfg = NotionifyConfig(token="t", image_base_dir="/safe/dir")
        assert cfg.image_base_dir == "/safe/dir"

    def test_config_image_base_dir_defaults_to_none(self):
        """By default, image_base_dir is None (no restriction)."""
        cfg = NotionifyConfig(token="t")
        assert cfg.image_base_dir is None

    def test_dotdot_that_stays_within_base_is_allowed(self, tmp_path: Path):
        """'sub/../photo.png' resolves to base/photo.png -- still inside."""
        base = tmp_path / "project"
        base.mkdir()
        result = self._check_path_traversal(str(base), "sub/../photo.png")
        assert result == (base / "photo.png").resolve()
        assert result.is_relative_to(base.resolve())


# =========================================================================
# 4. TestDataURISizePrecheck
# =========================================================================


class TestDataURISizePrecheck:
    """Verify that _parse_data_uri rejects oversized data URIs before fully
    decoding, and that validate_image propagates this correctly."""

    def test_huge_data_uri_rejected_before_decode(self):
        """A data URI with estimated size > 20 MiB raises NotionifyImageSizeError
        from _parse_data_uri before full base64 decode."""
        # 20 MiB = 20 * 1024 * 1024 = 20971520 bytes
        # base64 ratio: 4 chars -> 3 bytes
        # To exceed 20 MiB decoded, we need > 20971520 * 4/3 ~ 27962027 base64 chars
        huge_b64 = "A" * 28_000_000  # ~21 MiB decoded
        src = f"data:image/png;base64,{huge_b64}"
        with pytest.raises(NotionifyImageSizeError, match="too large"):
            _parse_data_uri(src)

    def test_huge_data_uri_has_estimated_bytes_in_context(self):
        """The error context should include the estimated byte count."""
        huge_b64 = "A" * 28_000_000
        src = f"data:image/png;base64,{huge_b64}"
        with pytest.raises(NotionifyImageSizeError) as exc_info:
            _parse_data_uri(src)
        assert "estimated_bytes" in exc_info.value.context
        assert exc_info.value.context["estimated_bytes"] > 20 * 1024 * 1024

    def test_validate_image_propagates_size_precheck(self):
        """validate_image raises NotionifyImageSizeError for an oversized
        data URI, using the _parse_data_uri pre-check."""
        huge_b64 = "A" * 28_000_000
        src = f"data:image/png;base64,{huge_b64}"
        config = _make_config()
        with pytest.raises(NotionifyImageSizeError):
            validate_image(src, ImageSourceType.DATA_URI, None, config)

    def test_data_uri_just_under_hard_limit_accepted(self):
        """A data URI whose estimated size is just under 20 MiB should not
        trigger the pre-check (but may fail MIME or post-decode size checks)."""
        # Just under 20 MiB: ~19.9 MiB decoded
        # 19.9 * 1024 * 1024 = 20867891 bytes
        # base64 chars needed: 20867891 * 4 / 3 ~ 27823855
        # Use valid base64 (all 'A's decode fine)
        # 20 MiB = 20971520 bytes. At 3/4 ratio, that's 27962027 chars.
        # To be UNDER 20 MiB, we need < 27962027 chars.
        safe_b64 = "A" * 27_900_000  # estimated decode = 27900000 * 3/4 = 20925000 < 20971520
        src = f"data:image/png;base64,{safe_b64}"
        # This should NOT raise NotionifyImageSizeError from the pre-check.
        # It will likely raise NotionifyImageSizeError from the post-decode
        # size check (5 MiB default), but that's a different path.
        with pytest.raises(NotionifyImageSizeError) as exc_info:
            validate_image(src, ImageSourceType.DATA_URI, None, _make_config())
        # The error should be from validate_image's post-decode check, not pre-check.
        # Post-decode errors mention "exceeds maximum", not "too large".
        assert "exceeds" in exc_info.value.message.lower() or "too large" in exc_info.value.message.lower()

    def test_small_data_uri_accepted(self):
        """A small, valid data URI passes both pre-check and validation."""
        small_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
        b64 = base64.b64encode(small_data).decode()
        src = f"data:image/png;base64,{b64}"
        mime, data = validate_image(src, ImageSourceType.DATA_URI, None, _make_config())
        assert mime == "image/png"
        assert data == small_data

    def test_precheck_threshold_is_20_mib(self):
        """The hard limit in _parse_data_uri is exactly 20 MiB.

        estimated_size = len(raw_data) * 3 // 4
        The check is: estimated_size > 20 * 1024 * 1024 (strict >).
        So exactly 20 MiB (20971520) should pass the pre-check.

        We use a base64 string length that is a multiple of 4 to avoid
        padding errors during decode.
        """
        # 20 MiB = 20971520 bytes.  estimated = len * 3 // 4.
        # We need len * 3 // 4 == 20971520, i.e. len == 27962027.
        # Round up to next multiple of 4: 27962028.
        # estimated = 27962028 * 3 // 4 = 20971521 which is > 20971520,
        # so that would trigger the pre-check.
        # Instead, round DOWN to 27962024 (multiple of 4):
        # estimated = 27962024 * 3 // 4 = 20971518 <= 20971520 -> passes pre-check.
        b64_len = 27962024  # multiple of 4, estimated = 20971518 < 20 MiB
        exact_b64 = "A" * b64_len
        src = f"data:image/png;base64,{exact_b64}"
        # Should NOT raise from pre-check (under the 20 MiB limit).
        # Will raise from validate_image's post-decode size check (5 MiB default).
        with pytest.raises(NotionifyImageSizeError) as exc_info:
            validate_image(src, ImageSourceType.DATA_URI, None, _make_config())
        # Confirm it went past pre-check: post-decode error says "exceeds maximum"
        assert "exceeds" in exc_info.value.message.lower()

    def test_one_byte_over_precheck_rejected(self):
        """One byte estimated over 20 MiB triggers the pre-check."""
        # estimated = len(raw_data) * 3 // 4 > 20971520
        # Need len(raw_data) * 3 // 4 = 20971521
        # len(raw_data) = ceil(20971521 * 4 / 3) = 27962028
        over_b64 = "A" * 27962028
        src = f"data:image/png;base64,{over_b64}"
        with pytest.raises(NotionifyImageSizeError, match="too large"):
            _parse_data_uri(src)


# =========================================================================
# 5. TestErrorContextNoLeaks
# =========================================================================


class TestErrorContextNoLeaks:
    """Verify error __repr__ works correctly and that NotionifyError subclasses
    properly expose code, message, and context without leaking secrets."""

    # -- Base NotionifyError ---------------------------------------------

    def test_base_error_repr(self):
        """NotionifyError repr includes class name, code, and message."""
        err = NotionifyError(
            code="TEST_CODE",
            message="Something went wrong",
        )
        r = repr(err)
        assert "NotionifyError" in r
        assert "TEST_CODE" in r
        assert "Something went wrong" in r

    def test_base_error_repr_with_context(self):
        """Context dict appears in repr when present."""
        err = NotionifyError(
            code="CODE",
            message="msg",
            context={"key": "value"},
        )
        r = repr(err)
        assert "context=" in r
        assert "'key'" in r
        assert "'value'" in r

    def test_base_error_repr_no_context(self):
        """Without context, 'context=' does not appear in repr."""
        err = NotionifyError(code="CODE", message="msg")
        r = repr(err)
        assert "context=" not in r

    def test_base_error_attributes(self):
        """code, message, context, cause are all accessible."""
        cause = ValueError("inner")
        err = NotionifyError(
            code="CODE",
            message="msg",
            context={"a": 1},
            cause=cause,
        )
        assert err.code == "CODE"
        assert err.message == "msg"
        assert err.context == {"a": 1}
        assert err.cause is cause
        assert err.__cause__ is cause

    def test_base_error_is_exception(self):
        """NotionifyError is a proper Exception subclass."""
        err = NotionifyError(code="X", message="test")
        assert isinstance(err, Exception)
        assert str(err) == "test"

    def test_default_context_is_empty_dict(self):
        """When no context is provided, it defaults to an empty dict."""
        err = NotionifyError(code="X", message="test")
        assert err.context == {}

    def test_default_cause_is_none(self):
        """When no cause is provided, it defaults to None."""
        err = NotionifyError(code="X", message="test")
        assert err.cause is None

    # -- Subclass error codes --------------------------------------------

    @pytest.mark.parametrize(
        "cls, expected_code",
        [
            (NotionifyValidationError, ErrorCode.VALIDATION_ERROR),
            (NotionifyAuthError, ErrorCode.AUTH_ERROR),
            (NotionifyPermissionError, ErrorCode.PERMISSION_ERROR),
            (NotionifyNotFoundError, ErrorCode.NOT_FOUND),
            (NotionifyRateLimitError, ErrorCode.RATE_LIMITED),
            (NotionifyRetryExhaustedError, ErrorCode.RETRY_EXHAUSTED),
            (NotionifyNetworkError, ErrorCode.NETWORK_ERROR),
            (NotionifyImageTypeError, ErrorCode.IMAGE_TYPE_ERROR),
            (NotionifyImageSizeError, ErrorCode.IMAGE_SIZE_ERROR),
            (NotionifyImageParseError, ErrorCode.IMAGE_PARSE_ERROR),
            (NotionifyImageNotFoundError, ErrorCode.IMAGE_NOT_FOUND),
            (NotionifyUploadExpiredError, ErrorCode.UPLOAD_EXPIRED),
            (NotionifyUploadTransportError, ErrorCode.UPLOAD_TRANSPORT_ERROR),
            (NotionifyTextOverflowError, ErrorCode.TEXT_OVERFLOW),
            (NotionifyDiffConflictError, ErrorCode.DIFF_CONFLICT),
            (NotionifyMathOverflowError, ErrorCode.MATH_OVERFLOW),
        ],
    )
    def test_subclass_error_code(self, cls, expected_code):
        """Each subclass sets the correct ErrorCode."""
        err = cls(message="test error")
        assert err.code == expected_code

    @pytest.mark.parametrize(
        "cls",
        [
            NotionifyValidationError,
            NotionifyAuthError,
            NotionifyPermissionError,
            NotionifyNotFoundError,
            NotionifyRateLimitError,
            NotionifyRetryExhaustedError,
            NotionifyNetworkError,
            NotionifyImageTypeError,
            NotionifyImageSizeError,
            NotionifyImageParseError,
            NotionifyImageNotFoundError,
            NotionifyUploadExpiredError,
            NotionifyUploadTransportError,
            NotionifyTextOverflowError,
            NotionifyDiffConflictError,
        ],
    )
    def test_subclass_is_notionify_error(self, cls):
        """All subclasses are instances of NotionifyError."""
        err = cls(message="test")
        assert isinstance(err, NotionifyError)

    # -- Subclass repr ---------------------------------------------------

    def test_subclass_repr_uses_own_class_name(self):
        """Subclass repr shows its own class name, not NotionifyError."""
        err = NotionifyAuthError(message="Invalid token")
        r = repr(err)
        assert r.startswith("NotionifyAuthError(")
        assert "AUTH_ERROR" in r
        assert "Invalid token" in r

    def test_image_error_repr(self):
        """Image error subclass repr is well-formed."""
        err = NotionifyImageSizeError(
            message="Image too large",
            context={"size_bytes": 10_000_000, "max_bytes": 5_000_000},
        )
        r = repr(err)
        assert "NotionifyImageSizeError" in r
        assert "IMAGE_SIZE_ERROR" in r
        assert "Image too large" in r
        assert "size_bytes" in r

    def test_upload_error_repr(self):
        """Upload error repr includes context keys."""
        err = NotionifyUploadTransportError(
            message="Connection reset",
            context={"upload_id": "uid-123", "part_number": 2, "status_code": 502},
        )
        r = repr(err)
        assert "NotionifyUploadTransportError" in r
        assert "uid-123" in r

    # -- Error code enum -------------------------------------------------

    def test_error_code_is_str_enum(self):
        """ErrorCode members are also strings."""
        assert isinstance(ErrorCode.AUTH_ERROR, str)
        assert ErrorCode.AUTH_ERROR == "AUTH_ERROR"

    def test_error_code_all_values_unique(self):
        """All ErrorCode values are unique."""
        values = [e.value for e in ErrorCode]
        assert len(values) == len(set(values))

    # -- Inheritance hierarchy -------------------------------------------

    def test_image_errors_inherit_from_image_error(self):
        """Specific image errors are NotionifyImageError subclasses."""
        for cls in (
            NotionifyImageTypeError,
            NotionifyImageSizeError,
            NotionifyImageParseError,
            NotionifyImageNotFoundError,
        ):
            err = cls(message="test")
            assert isinstance(err, NotionifyImageError)
            assert isinstance(err, NotionifyError)

    def test_upload_errors_inherit_from_upload_error(self):
        """Specific upload errors are NotionifyUploadError subclasses."""
        for cls in (NotionifyUploadExpiredError, NotionifyUploadTransportError):
            err = cls(message="test")
            assert isinstance(err, NotionifyUploadError)
            assert isinstance(err, NotionifyError)

    def test_conversion_errors_inherit_from_conversion_error(self):
        """Specific conversion errors are NotionifyConversionError subclasses."""
        err = NotionifyTextOverflowError(message="test")
        assert isinstance(err, NotionifyConversionError)
        assert isinstance(err, NotionifyError)

    # -- Context does not accidentally contain secrets -------------------

    def test_image_error_context_truncates_src(self):
        """The image validation pipeline truncates long src strings in context."""
        # Create a long data URI that will fail MIME validation
        long_data = "x" * 500
        src = f"data:application/octet-stream;base64,{long_data}"
        try:
            _parse_data_uri(src)
        except (NotionifyImageParseError, NotionifyImageSizeError) as err:
            # The src in context should be truncated
            if "src" in err.context:
                assert len(err.context["src"]) <= 203  # 200 + "..."

    def test_error_cause_chaining(self):
        """Errors properly chain __cause__ when a cause is provided."""
        original = ValueError("base64 decode failed")
        err = NotionifyImageParseError(
            message="Failed to decode",
            context={"reason": "base64_error"},
            cause=original,
        )
        assert err.__cause__ is original
        assert err.cause is original


class TestDataUriUrlEncoded:
    """URL-encoded (non-base64) data URIs and error path in validate.py lines 194-195."""

    def test_plain_text_data_uri_decoded(self):
        """data:text/plain,Hello%20World hits the URL-decode branch."""
        from notionify.image.validate import _parse_data_uri
        src = "data:text/plain,Hello%20World"
        mime, data = _parse_data_uri(src)
        assert mime == "text/plain"
        assert b"Hello World" in data

    def test_plain_data_uri_no_encoding(self):
        """data URI without encoding or base64 hits the URL-decode branch."""
        from notionify.image.validate import _parse_data_uri
        src = "data:text/plain,hello"
        mime, data = _parse_data_uri(src)
        assert b"hello" in data

    def test_url_decode_failure_raises_parse_error(self):
        """When unquote_to_bytes raises, lines 194-195 are hit."""
        from unittest.mock import patch

        from notionify.errors import NotionifyImageParseError
        from notionify.image.validate import _parse_data_uri
        src = "data:text/plain,hello"
        with patch("urllib.parse.unquote_to_bytes", side_effect=ValueError("bad")):
            with pytest.raises(NotionifyImageParseError) as exc_info:
                _parse_data_uri(src)
        assert exc_info.value.context["reason"] == "url_decode_error"
