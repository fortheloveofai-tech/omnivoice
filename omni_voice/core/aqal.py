"""
Adaptive Audio Channel Quality Layer (AQAL)
============================================
Implements the adaptive codec + back-pressure controller from cycle_10.

Responsibilities
----------------
* Monitors real-time network metrics (RTT, loss, bandwidth).
* Selects the best codec configuration from a decision matrix.
* Adapts the leaky-bucket leak-rate when bandwidth degrades.
* Signals back-pressure to the LLM provider when the buffer fills.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from omni_voice.core.leaky_bucket import LeakyBucket


class CodecProfile(str, Enum):
    """Codec configurations ordered by quality (highest first)."""
    high   = "opus_24khz_64kbps"
    medium = "opus_16khz_48kbps"
    low    = "evs_8khz_32kbps"


@dataclass
class NetworkMetrics:
    """Snapshot of network quality (pushed by the client every 50 ms)."""
    rtt_ms: float = 0.0
    loss_pct: float = 0.0
    bw_kbps: float = 1000.0
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.monotonic()


@dataclass
class CodecConfig:
    profile: CodecProfile
    sample_rate_hz: int
    bitrate_kbps: int
    frame_ms: int = 20


_CODEC_MATRIX: list[tuple[CodecConfig, Callable[[NetworkMetrics], bool]]] = [
    (
        CodecConfig(CodecProfile.high, 24_000, 64),
        lambda m: m.rtt_ms <= 30 and m.loss_pct <= 1.0,
    ),
    (
        CodecConfig(CodecProfile.medium, 16_000, 48),
        lambda m: m.rtt_ms <= 70 and m.loss_pct <= 3.0,
    ),
    (
        CodecConfig(CodecProfile.low, 8_000, 32),
        lambda _: True,   # always falls back to this
    ),
]


class AQALController:
    """
    Adaptive Audio Channel Quality Layer controller.

    Runs an async background loop that:
    1. Evaluates network metrics every 100 ms.
    2. Selects codec profile via the decision matrix.
    3. Adjusts bucket leak-rate based on bandwidth.
    4. Emits back-pressure signals when the bucket is >80 % full.

    Parameters
    ----------
    capacity:
        Maximum token queue size (default 30 from spec).
    base_leak_rate:
        Base drain rate in tokens/ms (default 0.05).
    on_codec_change:
        Optional callback when the codec profile changes.
    on_backpressure:
        Optional callback when back-pressure state changes.
    """

    def __init__(
        self,
        capacity: int = 30,
        base_leak_rate: float = 0.05,
        on_codec_change: Optional[Callable[[CodecConfig], None]] = None,
        on_backpressure: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self._bucket = LeakyBucket(capacity=float(capacity), leak_rate=base_leak_rate)
        self._base_leak_rate = base_leak_rate
        self._metrics = NetworkMetrics()
        self._codec = _CODEC_MATRIX[0][0]   # start with highest quality
        self.on_codec_change = on_codec_change
        self.on_backpressure = on_backpressure
        self._backpressure = False
        self._running = False
        self._task: Optional[asyncio.Task] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background adaptation loop."""
        self._running = True
        self._task = asyncio.create_task(self._adaptation_loop(), name="aqal-loop")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Network metrics ───────────────────────────────────────────────────────

    def update_metrics(self, metrics: NetworkMetrics) -> None:
        """Push a fresh network quality snapshot (from client QoS agent)."""
        self._metrics = metrics

    # ── Token flow control ────────────────────────────────────────────────────

    def add_token(self, n: float = 1.0) -> float:
        """Add tokens produced by LLM. Returns current bucket level."""
        return self._bucket.add(n)

    def consume_token(self, n: float = 1.0) -> bool:
        """Consume a token for TTS synthesis. Returns True if allowed."""
        return self._bucket.consume(n)

    @property
    def backpressure(self) -> bool:
        return self._backpressure

    @property
    def current_codec(self) -> CodecConfig:
        return self._codec

    @property
    def bucket_fill_pct(self) -> float:
        return self._bucket.fill_pct

    # ── Background loop ───────────────────────────────────────────────────────

    async def _adaptation_loop(self) -> None:
        """Run every 100 ms: evaluate metrics → select codec → adapt bucket."""
        while self._running:
            await asyncio.sleep(0.1)
            self._evaluate_codec()
            self._evaluate_backpressure()
            self._adapt_bucket()

    def _evaluate_codec(self) -> None:
        for config, condition in _CODEC_MATRIX:
            if condition(self._metrics):
                if config.profile != self._codec.profile:
                    self._codec = config
                    if self.on_codec_change:
                        self.on_codec_change(config)
                return

    def _evaluate_backpressure(self) -> None:
        was = self._backpressure
        # Apply back-pressure when bucket is >80 % full; release at <40 %
        fill = self._bucket.fill_pct
        if fill > 0.8:
            self._backpressure = True
        elif fill < 0.4:
            self._backpressure = False
        if self._backpressure != was and self.on_backpressure:
            self.on_backpressure(self._backpressure)

    def _adapt_bucket(self) -> None:
        """Scale leak-rate based on available bandwidth."""
        bw_current = self._metrics.bw_kbps
        bw_target = self._codec.bitrate_kbps
        if bw_current > 0:
            self._bucket.adapt_leak_rate(bw_current, bw_target)
