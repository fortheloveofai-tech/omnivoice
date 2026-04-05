"""
Temporal Alignment Buffer (TAB)
================================
Smooths bursty LLM token streams into a steady phoneme flow for
the TTS engine.  Directly implements the design from cycle_03.

Key behaviours
--------------
* Timestamp-tags each token with its expected synthesis start time.
* Normalises burst arrivals via a leaky-bucket rate-shaper.
* Emits back-pressure signals when the buffer exceeds high-water mark.
* Provides a sliding-window look-ahead so TTS can pre-fetch the next N tokens.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Deque, Optional

from omni_voice.core.leaky_bucket import LeakyBucket


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class TabEntry:
    token: str
    ts_target: float      # target synthesis time (seconds since session start)
    duration: float       # estimated audio duration (seconds)
    confidence: float = 1.0   # PTPB confidence score (1.0 = confirmed LLM token)


# Average syllable duration at 150 wpm, used when a G2P model isn't available.
_AVG_PHONEME_DURATION_SEC = 0.065  # ~65 ms per token


def _estimate_duration(token: str) -> float:
    """
    Lightweight token-duration estimator (no G2P required).
    Approximates duration from character length and syllable heuristics.
    """
    chars = len(token.strip())
    if chars == 0:
        return 0.02
    # Rough heuristic: 1 syllable ≈ 0.12s; average English word ~1.5 syllables
    syllables = max(1, chars // 3)
    return min(syllables * 0.12, 0.5)


# ── Buffer ─────────────────────────────────────────────────────────────────────

class TemporalAlignmentBuffer:
    """
    Thread-safe, asyncio-compatible Temporal Alignment Buffer.

    Parameters
    ----------
    rate:
        Target token-delivery rate in tokens/second (default ≈ 150 wpm).
    high_water:
        Seconds of buffered audio before back-pressure is signalled.
    low_water:
        Seconds of buffered audio before back-pressure is released.
    on_backpressure:
        Optional async callback invoked when back-pressure state changes.
        Receives a bool: True = pressure on, False = pressure released.
    """

    def __init__(
        self,
        rate: float = 8.5,
        high_water: float = 0.15,
        low_water: float = 0.05,
        on_backpressure: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self.rate = rate
        self.high_water = high_water
        self.low_water = low_water
        self.on_backpressure = on_backpressure

        self._bucket = LeakyBucket(capacity=10.0, leak_rate=rate / 1000)
        self._entries: Deque[TabEntry] = deque()
        self._session_start = time.monotonic()
        self._last_ts = time.monotonic()
        self._backpressure_active = False
        self._lock = asyncio.Lock()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _now(self) -> float:
        return time.monotonic() - self._session_start

    def _buffered_audio(self) -> float:
        """Total seconds of audio currently queued."""
        return sum(e.duration for e in self._entries)

    def _update_backpressure(self) -> None:
        buffered = self._buffered_audio()
        was_active = self._backpressure_active
        if buffered > self.high_water:
            self._backpressure_active = True
        elif buffered < self.low_water:
            self._backpressure_active = False
        if self.on_backpressure and self._backpressure_active != was_active:
            self.on_backpressure(self._backpressure_active)

    # ── Public API ────────────────────────────────────────────────────────────

    async def ingest(self, token: str, confidence: float = 1.0) -> TabEntry:
        """
        Ingest a new token from the LLM stream.

        Returns the TabEntry with its scheduled synthesis timestamp.
        The ts_target is guaranteed to be monotonically non-decreasing across
        successive calls, even when tokens arrive in rapid bursts.
        """
        async with self._lock:
            now = self._now()
            dur = _estimate_duration(token)

            # ts_target is the *next available* synthesis slot.
            # We track a running cursor (self._next_slot) that advances by
            # dur/rate for each token, so slots are always monotonic.
            if not hasattr(self, '_next_slot'):
                self._next_slot = now
            self._next_slot = max(self._next_slot, now)
            ts_target = self._next_slot
            self._next_slot += dur / max(self.rate, 0.001)

            entry = TabEntry(
                token=token,
                ts_target=ts_target,
                duration=dur,
                confidence=confidence,
            )
            self._entries.append(entry)
            self._bucket.add(dur * self.rate)
            self._last_ts = now
            self._update_backpressure()
            return entry

    async def consume(self, lookahead: int = 3) -> list[TabEntry]:
        """
        Return up to *lookahead* entries that are ready for TTS synthesis.

        An entry is ready when its target time is within the low-water window.
        """
        async with self._lock:
            now = self._now()
            ready: list[TabEntry] = []
            while (
                self._entries
                and self._entries[0].ts_target <= now + self.low_water
                and len(ready) < lookahead
            ):
                ready.append(self._entries.popleft())
            self._update_backpressure()
            return ready

    async def drain_all(self) -> list[TabEntry]:
        """Flush all remaining entries (e.g. on session end or interrupt)."""
        async with self._lock:
            result = list(self._entries)
            self._entries.clear()
            self._backpressure_active = False
            return result

    async def stream(self, poll_interval: float = 0.01) -> AsyncIterator[TabEntry]:
        """
        Async generator that yields TabEntries as they become ready.
        Yields immediately when tokens are available, otherwise polls.
        """
        while True:
            ready = await self.consume(lookahead=5)
            for entry in ready:
                yield entry
            if not ready:
                await asyncio.sleep(poll_interval)

    @property
    def backpressure(self) -> bool:
        return self._backpressure_active

    @property
    def queue_depth(self) -> int:
        return len(self._entries)

    @property
    def buffered_seconds(self) -> float:
        return self._buffered_audio()
