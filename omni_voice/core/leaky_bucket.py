"""
Leaky Bucket rate-shaper — shared by TAB, ATTS, and AQAL.

Implements a token-bucket with a dynamic leak rate so downstream
consumers always receive a smooth, predictable flow regardless of
upstream burst patterns.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class LeakyBucket:
    """
    Classic leaky-bucket with time-aware draining.

    Parameters
    ----------
    capacity:
        Maximum number of tokens the bucket can hold.
    leak_rate:
        Tokens drained per millisecond under normal conditions.
    """

    capacity: float
    leak_rate: float  # tokens per ms
    _tokens: float = field(default=0.0, init=False, repr=False)
    _last_ts_ns: int = field(default_factory=time.monotonic_ns, init=False, repr=False)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _drain(self) -> None:
        """Drain tokens proportional to elapsed wall-clock time."""
        now_ns = time.monotonic_ns()
        elapsed_ms = (now_ns - self._last_ts_ns) / 1_000_000
        drained = elapsed_ms * self.leak_rate
        self._tokens = max(0.0, self._tokens - drained)
        self._last_ts_ns = now_ns

    # ── Public API ────────────────────────────────────────────────────────────

    def add(self, n: float = 1.0) -> float:
        """
        Add *n* tokens, capped at capacity.

        Returns
        -------
        float
            Current token level after addition.
        """
        self._drain()
        self._tokens = min(self.capacity, self._tokens + n)
        return self._tokens

    def consume(self, n: float = 1.0) -> bool:
        """
        Attempt to consume *n* tokens atomically.

        Returns
        -------
        bool
            True if tokens were available and consumed, False otherwise.
        """
        self._drain()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    @property
    def level(self) -> float:
        """Current token level (0 … capacity) after draining."""
        self._drain()
        return self._tokens

    @property
    def fill_pct(self) -> float:
        """Bucket fullness as a fraction 0.0 … 1.0."""
        return self.level / self.capacity if self.capacity > 0 else 0.0

    def adapt_leak_rate(self, bw_current: float, bw_target: float, alpha: float = 0.3) -> None:
        """
        Dynamically adjust leak rate based on observed vs. target bandwidth.

        From AQAL spec:
            leak_rate = base_rate × (1 + α × (bw_target / bw_current - 1))

        Parameters
        ----------
        bw_current: float
            Current available bandwidth (kbps).
        bw_target: float
            Target bandwidth (kbps).
        alpha: float
            Adaptation strength (0-1). Default 0.3 from AQAL spec.
        """
        if bw_current <= 0:
            return
        ratio = bw_target / bw_current
        self.leak_rate = self.leak_rate * (1.0 + alpha * (ratio - 1.0))
        # Clamp to sensible range
        self.leak_rate = max(0.001, min(self.leak_rate, self.capacity))
