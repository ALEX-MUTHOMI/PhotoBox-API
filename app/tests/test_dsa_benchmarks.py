"""
Automated DS&A Complexity & Performance Benchmark Test Suite for PhotoBox-API.
"""
import time
import pytest
from unittest.mock import patch, MagicMock
from core.domain_index import get_workspace_id_by_domain, invalidate_domain_cache
from gallery.bloom_guard import TokenBloomFilter, mark_token_active, might_be_valid_token
from core.rate_limiter import check_sliding_window_rate_limit
from gallery.archive_queue import ArchiveMinHeap, schedule_archive_packaging, get_next_archive_job


class TestDomainIndexComplexity:
    def test_domain_index_constant_time_cached_lookup(self):
        """Assert O(1) cache lookup latency is under 5ms."""
        with patch("core.domain_index.cache") as mock_cache:
            mock_cache.get.return_value = "workspace-uuid-123"
            start = time.perf_counter()
            for _ in range(1000):
                res = get_workspace_id_by_domain("client.studio.com")
                assert res == "workspace-uuid-123"
            elapsed = time.perf_counter() - start
            avg_ms = (elapsed / 1000) * 1000
            assert avg_ms < 5.0, f"Average lookup took {avg_ms:.4f}ms, expected < 5ms"


class TestBloomFilterZeroFalseNegatives:
    def test_zero_false_negatives_on_10000_tokens(self):
        """Assert Bloom filter has exactly 0 false negatives across 10,000 items."""
        bloom = TokenBloomFilter(size=100_000, hash_count=5)
        tokens = [f"token-hash-{i:06d}" for i in range(10000)]
        for t in tokens:
            bloom.add(t)

        false_negatives = [t for t in tokens if t not in bloom]
        assert len(false_negatives) == 0, f"Found {len(false_negatives)} false negatives"

    def test_negative_lookup_rejects_unseen_tokens(self):
        bloom = TokenBloomFilter(size=100_000, hash_count=5)
        bloom.add("valid-hash-abc")
        assert "valid-hash-abc" in bloom
        assert "completely-invalid-hash" not in bloom


class TestSlidingWindowRateLimiter:
    def test_rate_limit_allows_under_threshold(self):
        with patch("core.rate_limiter.cache") as mock_cache:
            mock_cache.get.return_value = 2
            mock_cache.incr.return_value = 3
            allowed, remaining = check_sliding_window_rate_limit("ip_192_168_1_1", limit=5)
            assert allowed is True
            assert remaining == 2

    def test_rate_limit_blocks_over_threshold(self):
        with patch("core.rate_limiter.cache") as mock_cache:
            mock_cache.get.return_value = 5
            allowed, remaining = check_sliding_window_rate_limit("ip_192_168_1_1", limit=5)
            assert allowed is False
            assert remaining == 0


class TestArchiveMinHeapOrdering:
    def test_min_heap_prioritizes_pro_users(self):
        heap = ArchiveMinHeap()
        heap.push_job("job-free-1", "gal-1", 50, 10000, is_pro=False)
        heap.push_job("job-pro-1", "gal-2", 200, 50000, is_pro=True)
        heap.push_job("job-free-2", "gal-3", 10, 2000, is_pro=False)

        first = heap.pop_next_job()
        assert first.job_id == "job-pro-1"
        assert first.priority == 1
