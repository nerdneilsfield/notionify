"""Longest Common Subsequence matching over block signatures.

Uses the standard dynamic-programming LCS algorithm to find the longest
sequence of blocks that are identical between the existing page and the
new desired content.  The result drives the diff planner's decisions
about which blocks to keep, update, insert, or delete.
"""

from __future__ import annotations

from notionify.models import BlockSignature


def lcs_match(
    existing_sigs: list[BlockSignature],
    new_sigs: list[BlockSignature],
) -> list[tuple[int, int]]:
    """Compute LCS-based matched pairs between existing and new signatures.

    Two signatures match when they are structurally equal (all fields
    identical), which relies on :class:`BlockSignature` being a frozen
    dataclass with default ``__eq__`` and ``__hash__``.

    Parameters
    ----------
    existing_sigs:
        Signatures of the blocks currently on the Notion page.
    new_sigs:
        Signatures of the desired blocks (from the converter output).

    Returns
    -------
    list[tuple[int, int]]
        A list of ``(existing_idx, new_idx)`` pairs representing matched
        blocks, in order.  Unmatched indices on either side are candidates
        for INSERT (new-only) or DELETE (existing-only) operations.
    """
    m = len(existing_sigs)
    n = len(new_sigs)

    if m == 0 or n == 0:
        return []

    # Build the DP table.  dp[i][j] stores the length of the LCS of
    # existing_sigs[:i] and new_sigs[:j].
    dp: list[list[int]] = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if existing_sigs[i - 1] == new_sigs[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    # Backtrack to recover the actual matched pairs.
    pairs: list[tuple[int, int]] = []
    i, j = m, n
    while i > 0 and j > 0:
        if existing_sigs[i - 1] == new_sigs[j - 1]:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1

    pairs.reverse()
    return pairs
