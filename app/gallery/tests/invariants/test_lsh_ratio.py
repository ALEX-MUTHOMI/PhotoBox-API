"""Layer A: LSH pair-ratio and burst-recall invariants."""
from datetime import timedelta
from math import comb
from uuid import uuid4

import pytest
from django.test import SimpleTestCase
from django.utils import timezone

from gallery.burst_cluster import PhashRow, cluster_phash_rows
from gallery.phash import collect_lsh_candidate_pairs


@pytest.mark.dsa
class LshPairRatioInvariantTests(SimpleTestCase):
    def test_random_hashes_candidate_ratio_at_most_five_percent(self):
        n = 1_000
        # Deterministic pseudo-random 64-bit values (no secrets module needed)
        hashes = [
            ((i * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)).to_bytes(8, "big")
            for i in range(n)
        ]
        ids = [uuid4() for _ in range(n)]
        pairs = collect_lsh_candidate_pairs(
            zip(ids, hashes), bands=8, rows=8
        )
        ratio = len(pairs) / comb(n, 2)
        self.assertLessEqual(
            ratio,
            0.05,
            msg=f"LSH pair ratio {ratio:.4f} exceeds 5% ({len(pairs)} pairs)",
        )

    def test_burst_recall_within_time_window(self):
        now = timezone.now()
        a, b, c = uuid4(), uuid4(), uuid4()
        h1 = (0xAAAAAAAAAAAAAAAA).to_bytes(8, "big")
        h2 = (0xAAAAAAABAAAAAAAA).to_bytes(8, "big")
        h3 = (0xAAAAAAABAABAAAAA).to_bytes(8, "big")
        rows = [
            PhashRow(a, h1, now),
            PhashRow(b, h2, now + timedelta(seconds=1)),
            PhashRow(c, h3, now + timedelta(seconds=2)),
        ]
        components = cluster_phash_rows(
            rows, hamming_threshold=8, time_window_seconds=90, bands=8, rows_per_band=8
        )
        self.assertEqual(len(components), 1)
        self.assertEqual(set(components[0]), {a, b, c})
