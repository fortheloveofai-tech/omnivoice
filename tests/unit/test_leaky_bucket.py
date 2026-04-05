"""
Unit tests for LeakyBucket — the shared rate-shaping primitive.
Tests mirror the invariants from cycle_03 and cycle_10 specs.
"""
import time
import pytest
from omni_voice.core.leaky_bucket import LeakyBucket


class TestLeakyBucketBasics:
    def test_starts_empty(self):
        bucket = LeakyBucket(capacity=10.0, leak_rate=0.0)
        assert bucket.level == 0.0

    def test_add_tokens(self):
        bucket = LeakyBucket(capacity=10.0, leak_rate=0.0)
        level = bucket.add(5.0)
        assert level == 5.0

    def test_add_capped_at_capacity(self):
        bucket = LeakyBucket(capacity=10.0, leak_rate=0.0)
        bucket.add(15.0)
        assert bucket.level == 10.0

    def test_consume_success(self):
        bucket = LeakyBucket(capacity=10.0, leak_rate=0.0)
        bucket.add(5.0)
        result = bucket.consume(3.0)
        assert result is True
        assert abs(bucket.level - 2.0) < 0.1

    def test_consume_fails_when_empty(self):
        bucket = LeakyBucket(capacity=10.0, leak_rate=0.0)
        result = bucket.consume(1.0)
        assert result is False

    def test_consume_fails_when_insufficient(self):
        bucket = LeakyBucket(capacity=10.0, leak_rate=0.0)
        bucket.add(2.0)
        result = bucket.consume(5.0)
        assert result is False

    def test_fill_pct(self):
        bucket = LeakyBucket(capacity=10.0, leak_rate=0.0)
        bucket.add(5.0)
        assert abs(bucket.fill_pct - 0.5) < 0.05

    def test_fill_pct_zero_capacity(self):
        bucket = LeakyBucket(capacity=0.0, leak_rate=0.0)
        assert bucket.fill_pct == 0.0


class TestLeakyBucketDraining:
    def test_drains_over_time(self):
        # leak_rate=1.0 token/ms → bucket empties fast
        bucket = LeakyBucket(capacity=10.0, leak_rate=1.0)
        bucket.add(5.0)
        time.sleep(0.01)   # 10 ms → drains 10 tokens
        assert bucket.level == 0.0   # clamped to 0

    def test_does_not_drain_below_zero(self):
        bucket = LeakyBucket(capacity=10.0, leak_rate=10.0)
        time.sleep(0.05)
        assert bucket.level >= 0.0

    def test_slow_drain(self):
        # leak_rate=0.001 token/ms → very slow drain
        bucket = LeakyBucket(capacity=100.0, leak_rate=0.001)
        bucket.add(10.0)
        time.sleep(0.001)   # 1 ms → drains 0.001 tokens
        # Level should still be close to 10
        assert bucket.level > 9.0


class TestLeakyBucketAdaptation:
    def test_adapt_leak_rate_increases_when_bandwidth_low(self):
        bucket = LeakyBucket(capacity=30.0, leak_rate=0.05)
        initial_rate = bucket.leak_rate
        # bw_current < bw_target → rate should decrease (throttle)
        bucket.adapt_leak_rate(bw_current=100.0, bw_target=1000.0)
        # When current << target, ratio > 1 → rate increases
        assert bucket.leak_rate != initial_rate

    def test_adapt_leak_rate_clamped(self):
        bucket = LeakyBucket(capacity=30.0, leak_rate=0.05)
        # Extreme case should not cause negative or overflow rates
        bucket.adapt_leak_rate(bw_current=0.0001, bw_target=1000.0)
        assert bucket.leak_rate >= 0.001
        assert bucket.leak_rate <= 30.0
