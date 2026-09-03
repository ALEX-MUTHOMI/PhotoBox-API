"""Perceptual hash helpers for offline burst clustering (Phase 4)."""
from __future__ import annotations

from typing import Iterable

from PIL import Image as PILImage


def compute_phash_bytes(image: PILImage.Image, hash_size: int = 8) -> bytes:
    """Return 64-bit DCT pHash as 8 bytes (big-endian)."""
    import imagehash  # noqa: PLC0415

    prepared = image.convert("RGB")
    digest = imagehash.phash(prepared, hash_size=hash_size)
    # ImageHash string form is hex; int(digest) is not reliably available.
    return int(str(digest), 16).to_bytes(8, byteorder="big")


def hamming_distance(a: bytes, b: bytes) -> int:
    if len(a) != len(b):
        raise ValueError("phash length mismatch")
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).bit_count()


def lsh_band_keys(
    phash: bytes,
    *,
    bands: int = 8,
    rows: int = 8,
) -> list[tuple[int, int]]:
    """Split 64-bit hash into (band_index, band_bits) keys.

    Default 8×8 (not 16×4): random-pair collision ≈3% vs ≈64%, while
    Hamming≤8 bursts still match with ~96% probability.
    """
    if bands * rows != 64:
        raise ValueError("bands * rows must equal 64")
    value = int.from_bytes(phash, "big")
    keys: list[tuple[int, int]] = []
    mask = (1 << rows) - 1
    for band in range(bands):
        shift = 64 - (band + 1) * rows
        slice_bits = (value >> shift) & mask
        keys.append((band, slice_bits))
    return keys


def collect_lsh_candidate_pairs(
    photo_hashes: Iterable[tuple[object, bytes]],
    *,
    bands: int = 8,
    rows: int = 8,
) -> set[tuple[object, object]]:
    """Return unordered id pairs that share at least one LSH band."""
    buckets: dict[tuple[int, int], list[object]] = {}
    for photo_id, phash in photo_hashes:
        for key in lsh_band_keys(phash, bands=bands, rows=rows):
            buckets.setdefault(key, []).append(photo_id)

    pairs: set[tuple[object, object]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                pairs.add((a, b) if str(a) < str(b) else (b, a))
    return pairs
