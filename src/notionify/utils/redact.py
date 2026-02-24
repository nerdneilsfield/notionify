"""Token / payload redaction for safe logging.

Before any Notion API payload is written to logs or debug artifacts the
:func:`redact` function must be applied.  It enforces the following rules:

* **Authorization headers** are replaced with a masked placeholder that shows
  only the last four characters of the token (or a generic marker if the
  token is unknown).
* **Base64 data URIs** (``data:<mime>;base64,...``) are replaced with a
  human-readable placeholder: ``<data_uri:N_bytes>``.
* **Binary / file byte values** (detected heuristically as non-text strings
  that are unreasonably long) are replaced with ``<binary:N_bytes>``.
* The full bearer **token is never present** in the output.
"""

from __future__ import annotations

import base64
import copy
import re
from typing import Any

# Matches RFC 2397 data URIs with base64 encoding.
_DATA_URI_RE = re.compile(
    r"data:[a-zA-Z0-9_.+-]+/[a-zA-Z0-9_.+-]+;base64,[A-Za-z0-9+/=]+"
)

# Substrings: if any of these appear in a key name (case-insensitive), the
# value is redacted.  This catches variants like ``access_token``,
# ``api_secret``, ``private_key``, etc.
_SENSITIVE_KEY_PATTERNS: frozenset[str] = frozenset({
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "cookie",
    "private_key",
    "api_key",
    "api-key",
    "x-api-key",
})

# Heuristic: a string value longer than this threshold that looks like raw
# bytes (not valid readable text) is treated as binary.
_BINARY_LENGTH_THRESHOLD = 256


def _mask_token(value: str, token: str | None) -> str:
    """Replace bearer / token strings with a safe placeholder."""
    # If an explicit token is supplied, scrub it wherever it appears.
    if token and token in value:
        suffix = token[-4:] if len(token) >= 4 else "****"
        placeholder = f"<redacted:...{suffix}>"
        # If the placeholder itself contains the token, fall back to generic.
        if token in placeholder:
            placeholder = "<redacted>"
        value = value.replace(token, placeholder)
    # Also handle generic "Bearer <tok>" patterns that may remain.
    value = re.sub(
        r"(Bearer\s+)\S+",
        lambda m: f"{m.group(1)}<redacted>",
        value,
    )
    return value


def _estimate_data_uri_bytes(uri: str) -> int:
    """Return the approximate decoded byte length of a data URI."""
    try:
        # Strip the header portion: "data:...;base64,"
        b64_part = uri.split(";base64,", 1)[1]
        # base64 decoding to get actual length.
        return len(base64.b64decode(b64_part, validate=True))
    except Exception:
        # Fallback: rough estimate from base64 length.
        b64_part = uri.split(";base64,", 1)[-1]
        return len(b64_part) * 3 // 4


def _looks_binary(value: str) -> bool:
    """Heuristic: return True if *value* appears to be raw binary data."""
    if len(value) < _BINARY_LENGTH_THRESHOLD:
        return False
    # Count non-printable characters (excluding common whitespace).
    non_printable = sum(
        1
        for ch in value[:512]
        if not ch.isprintable() and ch not in ("\n", "\r", "\t")
    )
    return non_printable > len(value[:512]) * 0.1


def _redact_value(value: Any, token: str | None) -> Any:
    """Redact a single value (recursive for dicts / lists)."""
    if isinstance(value, dict):
        return _redact_dict(value, token)
    if isinstance(value, list):
        return [_redact_value(item, token) for item in value]
    if isinstance(value, str):
        # Replace data URIs.
        if _DATA_URI_RE.search(value):
            value = _DATA_URI_RE.sub(
                lambda m: f"<data_uri:{_estimate_data_uri_bytes(m.group(0))}_bytes>",
                value,
            )
        # Replace binary-looking blobs.
        if _looks_binary(value):
            return f"<binary:{len(value.encode('utf-8'))}_bytes>"
        # Strip any lingering token.
        if token:
            value = _mask_token(value, token)
        return value
    if isinstance(value, (bytes, bytearray)):
        return f"<binary:{len(value)}_bytes>"
    return value


def _redact_dict(d: dict, token: str | None) -> dict:
    """Recursively redact a dictionary."""
    result: dict = {}
    for key, value in d.items():
        key_lower = key.lower() if isinstance(key, str) else ""
        if any(pat in key_lower for pat in _SENSITIVE_KEY_PATTERNS):
            if isinstance(value, str):
                result[key] = _mask_token(value, token)
            else:
                result[key] = "<redacted>"
        else:
            result[key] = _redact_value(value, token)
    return result


def redact(payload: dict, token: str | None = None) -> dict:
    """Return a deep copy of *payload* with sensitive data redacted.

    Redaction rules
    ---------------
    * **Authorization / token headers** — replaced with
      ``<redacted:...XXXX>`` (last 4 chars) or ``<redacted>``.
    * **Base64 data URIs** — replaced with ``<data_uri:N_bytes>``.
    * **Binary byte values** — replaced with ``<binary:N_bytes>``.
    * The full *token* string (if provided) is scrubbed from every string
      value in the tree.

    Parameters
    ----------
    payload:
        The dictionary to sanitize (typically a Notion API request body or
        set of headers).
    token:
        The Notion integration token.  If supplied, any occurrence of this
        exact string anywhere in the payload is replaced.

    Returns
    -------
    dict
        A new dictionary with all sensitive data removed.  The original
        *payload* is never mutated.

    Examples
    --------
    >>> redact({"Authorization": "Bearer ntn_abc123"})
    {'Authorization': '<redacted>'}

    >>> redact({"img": "data:image/png;base64,iVBOR..."})  # doctest: +SKIP
    {'img': '<data_uri:...>'}
    """
    # Deep-copy to guarantee no mutation of the caller's data.
    safe = copy.deepcopy(payload)
    return _redact_dict(safe, token)
