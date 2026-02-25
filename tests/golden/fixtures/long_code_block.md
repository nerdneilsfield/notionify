# Long Code Block

A code block that exceeds the Notion 2000-character rich-text limit
must be split into multiple rich-text segments while preserving all content.

```python
# This code block is intentionally long to test the 2000-character splitting behaviour.
# The notionify SDK must split the rich_text array while keeping the language annotation.

import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple


def fibonacci(n: int) -> int:
    """Return the nth Fibonacci number using dynamic programming."""
    if n <= 1:
        return n
    dp = [0] * (n + 1)
    dp[1] = 1
    for i in range(2, n + 1):
        dp[i] = dp[i - 1] + dp[i - 2]
    return dp[n]


def is_prime(n: int) -> bool:
    """Check if a number is prime using trial division."""
    if n < 2:
        return False
    if n == 2:
        return True
    if n % 2 == 0:
        return False
    for i in range(3, int(n ** 0.5) + 1, 2):
        if n % i == 0:
            return False
    return True


def sieve_of_eratosthenes(limit: int) -> List[int]:
    """Return all primes up to limit using the Sieve of Eratosthenes."""
    sieve = [True] * (limit + 1)
    sieve[0] = sieve[1] = False
    for i in range(2, int(limit ** 0.5) + 1):
        if sieve[i]:
            for j in range(i * i, limit + 1, i):
                sieve[j] = False
    return [i for i in range(2, limit + 1) if sieve[i]]


class BinarySearchTree:
    """A simple binary search tree implementation."""

    def __init__(self) -> None:
        self.root: Optional[Dict[str, Any]] = None

    def insert(self, value: int) -> None:
        """Insert a value into the BST."""
        if self.root is None:
            self.root = {"value": value, "left": None, "right": None}
        else:
            self._insert_recursive(self.root, value)

    def _insert_recursive(self, node: Dict[str, Any], value: int) -> None:
        if value < node["value"]:
            if node["left"] is None:
                node["left"] = {"value": value, "left": None, "right": None}
            else:
                self._insert_recursive(node["left"], value)
        else:
            if node["right"] is None:
                node["right"] = {"value": value, "left": None, "right": None}
            else:
                self._insert_recursive(node["right"], value)

    def inorder(self) -> List[int]:
        """Return values in sorted order via inorder traversal."""
        result: List[int] = []
        self._inorder_recursive(self.root, result)
        return result

    def _inorder_recursive(
        self, node: Optional[Dict[str, Any]], result: List[int]
    ) -> None:
        if node is not None:
            self._inorder_recursive(node["left"], result)
            result.append(node["value"])
            self._inorder_recursive(node["right"], result)


def merge_sort(arr: List[int]) -> List[int]:
    """Sort a list using merge sort (O(n log n))."""
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return _merge(left, right)


def _merge(left: List[int], right: List[int]) -> List[int]:
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result


if __name__ == "__main__":
    print(f"fib(10) = {fibonacci(10)}")
    primes = sieve_of_eratosthenes(50)
    print(f"Primes up to 50: {primes}")
    bst = BinarySearchTree()
    for v in [5, 3, 7, 1, 4, 6, 8]:
        bst.insert(v)
    print(f"BST inorder: {bst.inorder()}")
    data = [64, 34, 25, 12, 22, 11, 90]
    print(f"Sorted: {merge_sort(data)}")
```
