"""MD5 helpers for content signatures.

These lightweight hashes are used by the diff engine to detect whether a
block's content has changed between the local Markdown source and the
remote Notion state.  They are **not** used for security purposes.
"""

from __future__ import annotations

import hashlib
import json


def md5_hash(data: str) -> str:
    """Return the hex-encoded MD5 digest of *data*.

    The string is encoded as UTF-8 before hashing.

    Parameters
    ----------
    data:
        Arbitrary string to hash.

    Returns
    -------
    str
        A 32-character lowercase hexadecimal string.

    Examples
    --------
    >>> md5_hash("hello")
    '5d41402abc4b2a76b9719d911017c592'
    """
    return hashlib.md5(data.encode("utf-8")).hexdigest()


def hash_dict(d: dict) -> str:
    """Return the hex-encoded MD5 of a JSON-serialized dict.

    The dictionary is serialized with **sorted keys** and
    ``ensure_ascii=False`` to produce a deterministic, Unicode-preserving
    representation.  The resulting JSON string is then hashed via
    :func:`md5_hash`.

    Parameters
    ----------
    d:
        Dictionary to hash.

    Returns
    -------
    str
        A 32-character lowercase hexadecimal string.

    Examples
    --------
    >>> hash_dict({"b": 2, "a": 1}) == hash_dict({"a": 1, "b": 2})
    True
    """
    return md5_hash(json.dumps(d, sort_keys=True, ensure_ascii=False))
