"""
Probabilistic Bloom Filter for Passwordless Magic Link Token Verification.

Problem: Under volumetric access spraying, querying PostgreSQL for thousands
of non-existent magic link tokens causes disk I/O saturation.

Solution:
  - O(1) Bit-Array Bloom filter pre-check.
  - Zero False-Negative Guarantee: If bloom returns False, the token is 100% invalid.
  - Drops invalid requests before touching PostgreSQL.
"""
from __future__ import annotations

import hashlib
from array import array
from typing import Iterator


class TokenBloomFilter:
    def __init__(self, size: int = 150_000, hash_count: int = 5):
        self.size = size
        self.hash_count = hash_count
        self._bits = array("b", [0] * size)

    def _hashes(self, item: str) -> Iterator[int]:
        item_bytes = item.strip().encode("utf-8")
        for i in range(self.hash_count):
            digest = hashlib.sha256(f"{i}:{item_bytes.hex()}".encode("ascii")).hexdigest()
            yield int(digest[:8], 16) % self.size

    def add(self, token_hash: str) -> None:
        """Insert token hash into the bloom filter. O(hash_count) = O(1)."""
        if not token_hash:
            return
        for pos in self._hashes(token_hash):
            self._bits[pos] = 1

    def __contains__(self, token_hash: str) -> bool:
        """Check if token might exist in O(1). Zero false-negatives."""
        if not token_hash:
            return False
        return all(self._bits[pos] for pos in self._hashes(token_hash))


# Global process-level singleton
_magic_token_bloom = TokenBloomFilter(size=250_000, hash_count=5)


def mark_token_active(token_hash: str) -> None:
    _magic_token_bloom.add(token_hash)


def might_be_valid_token(token_hash: str) -> bool:
    return token_hash in _magic_token_bloom
