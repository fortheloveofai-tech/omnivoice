"""
Adaptive Turn-Taking Scheduler (ATTS)
======================================
Implements the VAD-triggered state machine from cycle_08.

States
------
  Idle → Listening → Thinking → Speaking → [Interrupt] → Thinking

The ATTS is the orchestration brain of every voice session.  It coordinates:
  * When to start LLM inference (after user speech ends)
  * When to interrupt the assistant's response (barge-in)
  * Token flow-control via a leaky-bucket gate
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional

from omni_voice.core.leaky_bucket import LeakyBucket


class TurnState(str, Enum):
    idle = "idle"
    listening = "listening"
    thinking = "thinking"
    speaking = "speaking"
    interrupt = "interrupt"


@dataclass
class EpochClock:
    """Fixed-size epoch counter (default 50 ms per epoch, from spec)."""
    epoch_ms: int = 50
    _start: float = field(default_factory=time.monotonic, init=False)

    @property
    def current(self) -> int:
        """Monotonically increasing epoch counter."""
        return int((time.monotonic() - self._start) * 1000 / self.epoch_ms)


class AdaptiveTurnTakingScheduler:
    """
    ATTS state machine with leaky-bucket token gating and barge-in detection.

    Parameters
    ----------
    epoch_ms:
        Duration of one scheduling epoch in milliseconds (default 50).
    margin_ms:
        Safety window for VAD lag — how long to wait after silence before
        declaring the user's turn complete (default 100 ms).
    leak_rate:
        Leaky-bucket token drain rate in tokens/epoch (default 1.0).
    burst_capacity:
        Maximum token burst the bucket can absorb (default 5).
    on_state_change:
        Optional callback invoked on every state transition.
    """

    def __init__(
        self,
        epoch_ms: int = 50,
        margin_ms: int = 100,
        leak_rate: float = 1.0,
        burst_capacity: int = 5,
        on_state_change: Optional[Callable[[TurnState, TurnState], None]] = None,
    ) -> None:
        self.clock = EpochClock(epoch_ms)
        self.margin_epochs = max(1, margin_ms // epoch_ms)
        self.on_state_change = on_state_change

        self._state = TurnState.idle
        self._last_vad_active_epoch: int = 0
        self._bucket = LeakyBucket(
            capacity=float(burst_capacity),
            leak_rate=leak_rate / 1000 * epoch_ms,   # tokens per ms → tokens per epoch tick
        )
        self._lock = asyncio.Lock()
        self._state_event = asyncio.Event()

    # ── State machine ─────────────────────────────────────────────────────────

    def _transition(self, new_state: TurnState) -> None:
        old = self._state
        if old == new_state:
            return
        self._state = new_state
        self._state_event.set()
        self._state_event.clear()
        if self.on_state_change:
            self.on_state_change(old, new_state)

    @property
    def state(self) -> TurnState:
        return self._state

    # ── VAD events ────────────────────────────────────────────────────────────

    async def on_speech_start(self) -> None:
        """Call when VAD detects voice activity begins."""
        async with self._lock:
            self._last_vad_active_epoch = self.clock.current
            if self._state == TurnState.idle:
                self._transition(TurnState.listening)
            elif self._state == TurnState.speaking:
                # Potential barge-in — mark epoch, barge-in handler decides
                self._last_vad_active_epoch = self.clock.current

    async def on_speech_frame(self) -> None:
        """Call on each VAD-confirmed speech frame to refresh activity epoch."""
        async with self._lock:
            self._last_vad_active_epoch = self.clock.current

    async def on_vad_silence(self) -> TurnState:
        """
        Call periodically (every epoch) when VAD sees silence.

        Returns
        -------
        TurnState
            The current state after evaluating the silence window.
        """
        async with self._lock:
            current_epoch = self.clock.current
            silence_epochs = current_epoch - self._last_vad_active_epoch

            if self._state == TurnState.listening:
                if silence_epochs >= self.margin_epochs:
                    self._transition(TurnState.thinking)

            elif self._state == TurnState.interrupt:
                if silence_epochs >= self.margin_epochs:
                    self._transition(TurnState.thinking)

            return self._state

    # ── LLM / TTS events ─────────────────────────────────────────────────────

    async def on_first_token(self) -> None:
        """Call when the LLM emits its first token."""
        async with self._lock:
            if self._state == TurnState.thinking:
                self._transition(TurnState.speaking)

    async def on_turn_complete(self) -> None:
        """Call when TTS finishes speaking and the token queue is empty."""
        async with self._lock:
            if self._state == TurnState.speaking:
                self._transition(TurnState.idle)

    # ── Barge-in ──────────────────────────────────────────────────────────────

    async def handle_barge_in(self, audio_energy: float, threshold: float = 0.01) -> bool:
        """
        Evaluate a barge-in candidate frame.

        Parameters
        ----------
        audio_energy:
            RMS energy of the incoming audio frame (normalised 0-1).
        threshold:
            Energy threshold above which we consider it speech.

        Returns
        -------
        bool
            True if we interrupted the assistant turn.
        """
        async with self._lock:
            if self._state != TurnState.speaking:
                return False
            if audio_energy > threshold:
                self._transition(TurnState.interrupt)
                return True
            return False

    # ── Token gating ──────────────────────────────────────────────────────────

    async def gate_token(self) -> bool:
        """
        Check whether the leaky-bucket allows emitting the next LLM token.

        Returns True and consumes 1 token from the bucket if allowed.
        The caller should pause generation when this returns False.
        """
        async with self._lock:
            return self._bucket.consume(1.0)

    async def tick_bucket(self) -> None:
        """Advance the leaky-bucket by one epoch (call every epoch_ms)."""
        async with self._lock:
            self._bucket.add(self._bucket.leak_rate * self.clock.epoch_ms)

    # ── Helper ────────────────────────────────────────────────────────────────

    def is_user_done(self) -> bool:
        """Return True when the user's turn is fully complete."""
        current = self.clock.current
        silence = current - self._last_vad_active_epoch
        return silence >= self.margin_epochs and self._state in (
            TurnState.listening,
            TurnState.thinking,
        )

    async def wait_for_state(self, target: TurnState, timeout: float = 5.0) -> bool:
        """
        Block until the ATTS enters *target* state or *timeout* seconds pass.

        Returns True if the target state was reached.
        """
        deadline = time.monotonic() + timeout
        while self._state != target:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.get_event_loop().create_future()),
                    timeout=min(remaining, 0.05),
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        return True

    def reset(self) -> None:
        """Reset to Idle (e.g. on session end)."""
        self._state = TurnState.idle
        self._last_vad_active_epoch = 0
