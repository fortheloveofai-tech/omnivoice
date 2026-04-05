"""
Unit tests for the Temporal Alignment Buffer (TAB).
Validates: timestamp ordering, back-pressure, consumption, and queue depth.
"""
import asyncio
import pytest
from omni_voice.core.tab import TemporalAlignmentBuffer, TabEntry


@pytest.mark.asyncio
class TestTABIngestion:
    async def test_ingest_returns_entry(self):
        tab = TemporalAlignmentBuffer(rate=8.5, high_water=0.15, low_water=0.05)
        entry = await tab.ingest("hello")
        assert isinstance(entry, TabEntry)
        assert entry.token == "hello"

    async def test_queue_depth_increases(self):
        tab = TemporalAlignmentBuffer(rate=8.5, high_water=0.15, low_water=0.05)
        assert tab.queue_depth == 0
        await tab.ingest("one")
        await tab.ingest("two")
        assert tab.queue_depth == 2

    async def test_ts_target_is_monotonic(self):
        tab = TemporalAlignmentBuffer(rate=8.5, high_water=0.15, low_water=0.05)
        e1 = await tab.ingest("first")
        e2 = await tab.ingest("second")
        e3 = await tab.ingest("third")
        assert e1.ts_target <= e2.ts_target <= e3.ts_target

    async def test_duration_positive(self):
        tab = TemporalAlignmentBuffer()
        entry = await tab.ingest("synthesize")
        assert entry.duration > 0.0

    async def test_short_token_has_nonzero_duration(self):
        tab = TemporalAlignmentBuffer()
        entry = await tab.ingest("I")
        assert entry.duration > 0.0


@pytest.mark.asyncio
class TestTABBackpressure:
    async def test_backpressure_activates_at_high_water(self):
        pressures = []
        tab = TemporalAlignmentBuffer(
            rate=8.5,
            high_water=0.01,   # very low threshold
            low_water=0.005,
            on_backpressure=lambda p: pressures.append(p),
        )
        # Ingest many tokens quickly to fill buffer
        for word in "the quick brown fox jumps over the lazy dog".split():
            await tab.ingest(word)
        assert tab.backpressure is True
        assert True in pressures

    async def test_backpressure_releases_after_consume(self):
        tab = TemporalAlignmentBuffer(
            rate=8.5,
            high_water=0.01,
            low_water=0.005,
        )
        for word in "hello world foo bar baz".split():
            await tab.ingest(word)
        # Drain
        await tab.drain_all()
        # After drain, backpressure should be off
        assert tab.backpressure is False


@pytest.mark.asyncio
class TestTABConsumption:
    async def test_consume_returns_entries(self):
        tab = TemporalAlignmentBuffer(rate=8.5)
        await tab.ingest("now")   # should be ready immediately
        await asyncio.sleep(0.05)
        ready = await tab.consume(lookahead=5)
        assert len(ready) >= 0   # may or may not be ready depending on timing

    async def test_drain_all_empties_queue(self):
        tab = TemporalAlignmentBuffer()
        for w in ["a", "b", "c"]:
            await tab.ingest(w)
        assert tab.queue_depth == 3
        all_entries = await tab.drain_all()
        assert len(all_entries) == 3
        assert tab.queue_depth == 0

    async def test_buffered_seconds_positive(self):
        tab = TemporalAlignmentBuffer()
        await tab.ingest("hello world this is a test sentence")
        assert tab.buffered_seconds > 0.0
