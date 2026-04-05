"""
Voice Session Manager
=====================
One VoiceSession per connected WebSocket client.

A session orchestrates the full pipeline:
  Audio-in → ASR → ATTS → LLM → TAB → AQAL → TTS → Audio-out

Each session is fully async and isolated. The SessionManager registry
keeps track of all active sessions and exposes metrics.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Optional

from omni_voice.config import settings
from omni_voice.core import (
    AQALController,
    AdaptiveTurnTakingScheduler,
    NetworkMetrics,
    TemporalAlignmentBuffer,
    TurnState,
)
from omni_voice.observability import metrics as m
from omni_voice.providers.base import ASRProvider, LLMProvider, TTSProvider

logger = logging.getLogger(__name__)


@dataclass
class ConversationTurn:
    role: str        # "user" | "assistant"
    content: str
    timestamp: float = field(default_factory=time.monotonic)


class VoiceSession:
    """
    A single bidirectional voice conversation session.

    Parameters
    ----------
    session_id:
        Unique session identifier.
    asr:
        ASR provider instance.
    llm:
        LLM provider instance.
    tts:
        TTS provider instance.
    audio_sink:
        Async callback that receives synthesised audio bytes for delivery
        to the WebSocket client.
    transcript_sink:
        Optional async callback for partial transcripts (for live display).
    """

    def __init__(
        self,
        session_id: str,
        asr: ASRProvider,
        llm: LLMProvider,
        tts: TTSProvider,
        audio_sink: Callable[[bytes], None],
        transcript_sink: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.session_id = session_id
        self._asr = asr
        self._llm = llm
        self._tts = tts
        self._audio_sink = audio_sink
        self._transcript_sink = transcript_sink

        # Pipeline components
        self._tab = TemporalAlignmentBuffer(
            rate=settings.tab_speech_rate_tokens_per_sec,
            high_water=settings.tab_high_water_sec,
            low_water=settings.tab_low_water_sec,
            on_backpressure=self._on_tab_backpressure,
        )
        self._atts = AdaptiveTurnTakingScheduler(
            epoch_ms=settings.atts_epoch_ms,
            margin_ms=settings.atts_margin_ms,
            leak_rate=settings.atts_leaky_bucket_rate,
            burst_capacity=settings.atts_leaky_bucket_burst,
            on_state_change=self._on_state_change,
        )
        self._aqal = AQALController(
            capacity=settings.aqal_bucket_capacity,
            base_leak_rate=settings.aqal_base_leak_rate,
            on_codec_change=lambda cfg: logger.info(
                "[%s] Codec → %s", session_id, cfg.profile
            ),
            on_backpressure=self._on_aqal_backpressure,
        )

        # Conversation history (last 10 turns for context)
        self._history: list[dict] = []
        self._current_transcript = ""
        self._llm_backpressure = False

        # Audio input queue (fed by WebSocket handler)
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

        # Internal tasks
        self._tasks: list[asyncio.Task] = []
        self._started = False
        self._start_time = time.monotonic()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start all background pipeline tasks."""
        await self._aqal.start()
        self._tasks = [
            asyncio.create_task(self._asr_loop(), name=f"asr-{self.session_id}"),
            asyncio.create_task(self._vad_epoch_loop(), name=f"vad-{self.session_id}"),
        ]
        self._started = True
        m.active_sessions.inc()
        logger.info("[%s] Session started", self.session_id)

    async def stop(self) -> None:
        """Gracefully stop all tasks and release resources."""
        self._started = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._aqal.stop()
        m.active_sessions.dec()
        logger.info("[%s] Session stopped", self.session_id)

    # ── Audio ingestion ───────────────────────────────────────────────────────

    async def ingest_audio(self, pcm_bytes: bytes) -> None:
        """
        Feed a raw PCM audio chunk from the client WebSocket.
        Non-blocking — drops frames if the queue is full.
        """
        try:
            self._audio_queue.put_nowait(pcm_bytes)
            await self._atts.on_speech_frame()
        except asyncio.QueueFull:
            logger.warning("[%s] Audio queue full — dropping frame", self.session_id)

    async def update_network_metrics(self, metrics: NetworkMetrics) -> None:
        """Push updated network quality metrics from the client."""
        self._aqal.update_metrics(metrics)

    # ── Pipeline loops ────────────────────────────────────────────────────────

    async def _audio_source(self) -> AsyncIterator[bytes]:
        """Async generator that yields audio chunks from the ingest queue."""
        while self._started:
            try:
                chunk = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
                yield chunk
            except asyncio.TimeoutError:
                continue

    async def _asr_loop(self) -> None:
        """Continuously transcribe incoming audio and trigger the LLM pipeline."""
        t0_asr = time.monotonic()
        try:
            async for transcript in self._asr.transcribe_stream(self._audio_source()):
                latency_ms = (time.monotonic() - t0_asr) * 1000
                m.asr_decode_latency.observe(latency_ms)
                t0_asr = time.monotonic()

                self._current_transcript = transcript
                if self._transcript_sink:
                    await asyncio.coroutine(self._transcript_sink)(transcript)

                # Notify ATTS that speech is active
                await self._atts.on_speech_start()

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[%s] ASR loop error: %s", self.session_id, exc)
            m.errors_total.labels(stage="asr").inc()

    async def _vad_epoch_loop(self) -> None:
        """
        Runs every ATTS epoch to detect end-of-user-turn and trigger LLM.
        Also drives the ATTS leaky-bucket tick.
        """
        epoch_sec = settings.atts_epoch_ms / 1000
        try:
            while self._started:
                await asyncio.sleep(epoch_sec)
                await self._atts.tick_bucket()
                state = await self._atts.on_vad_silence()

                # When ATTS transitions to Thinking, run the LLM pipeline
                if state == TurnState.thinking and self._current_transcript:
                    transcript = self._current_transcript
                    self._current_transcript = ""
                    asyncio.create_task(
                        self._llm_tts_pipeline(transcript),
                        name=f"llm-tts-{self.session_id}",
                    )

                # Update TAB metrics
                m.tab_buffer_occupancy.set(self._tab.queue_depth)
                m.tab_buffered_seconds.set(self._tab.buffered_seconds)

        except asyncio.CancelledError:
            pass

    async def _llm_tts_pipeline(self, user_utterance: str) -> None:
        """
        Core pipeline: user_utterance → LLM tokens → TAB → AQAL → TTS → audio.
        """
        t0 = time.monotonic()
        full_response = ""
        first_token = True

        logger.debug("[%s] LLM prompt: %r", self.session_id, user_utterance)

        try:
            # Build a token stream from the LLM
            async def token_stream() -> AsyncIterator[str]:
                nonlocal first_token, full_response
                t0_token = time.monotonic()
                async for token in self._llm.stream_tokens(
                    prompt=user_utterance,
                    history=self._history[-10:],   # cap history
                    system_prompt=settings.system_prompt,
                ):
                    if first_token:
                        await self._atts.on_first_token()
                        first_token = False

                    latency_ms = (time.monotonic() - t0_token) * 1000
                    m.llm_token_latency.observe(latency_ms)
                    t0_token = time.monotonic()
                    m.tokens_generated.inc()

                    # AQAL back-pressure: wait briefly if bucket is full
                    while self._aqal.backpressure:
                        await asyncio.sleep(0.01)

                    # Ingest into TAB for rate-shaping
                    await self._tab.ingest(token)
                    self._aqal.add_token()
                    full_response += token

                    yield token

            # Stream tokens → TTS → audio sink
            t0_tts = time.monotonic()
            async for audio_chunk in self._tts.synthesize_stream(token_stream()):
                tts_latency_ms = (time.monotonic() - t0_tts) * 1000
                m.tts_synthesis_latency.observe(tts_latency_ms)
                t0_tts = time.monotonic()

                # Send audio to client
                m.audio_bytes_sent.inc(len(audio_chunk))
                await asyncio.coroutine(self._audio_sink)(audio_chunk)

        except asyncio.CancelledError:
            logger.info("[%s] LLM-TTS pipeline interrupted (barge-in)", self.session_id)
        except Exception as exc:
            logger.error("[%s] LLM-TTS pipeline error: %s", self.session_id, exc)
            m.errors_total.labels(stage="llm").inc()
        finally:
            # Record conversation history
            if user_utterance:
                self._history.append({"role": "user", "content": user_utterance})
            if full_response:
                self._history.append({"role": "assistant", "content": full_response})
            # Keep history bounded
            self._history = self._history[-20:]

            # End-to-end latency
            e2e_ms = (time.monotonic() - t0) * 1000
            m.call_round_trip_latency.observe(e2e_ms)
            logger.info("[%s] Turn complete in %.0f ms", self.session_id, e2e_ms)

            await self._atts.on_turn_complete()

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_tab_backpressure(self, active: bool) -> None:
        m.tab_backpressure.set(1 if active else 0)
        logger.debug("[%s] TAB back-pressure: %s", self.session_id, active)

    def _on_aqal_backpressure(self, active: bool) -> None:
        self._llm_backpressure = active

    def _on_state_change(self, old: TurnState, new: TurnState) -> None:
        logger.debug("[%s] ATTS: %s → %s", self.session_id, old, new)
        if new == TurnState.interrupt:
            m.barge_in_count.inc()


# ── Registry ──────────────────────────────────────────────────────────────────

class SessionManager:
    """Registry of all active VoiceSession instances."""

    def __init__(self) -> None:
        self._sessions: dict[str, VoiceSession] = {}

    def create(
        self,
        asr: ASRProvider,
        llm: LLMProvider,
        tts: TTSProvider,
        audio_sink: Callable[[bytes], None],
        transcript_sink: Optional[Callable[[str], None]] = None,
    ) -> VoiceSession:
        sid = str(uuid.uuid4())
        session = VoiceSession(
            session_id=sid,
            asr=asr,
            llm=llm,
            tts=tts,
            audio_sink=audio_sink,
            transcript_sink=transcript_sink,
        )
        self._sessions[sid] = session
        return session

    async def destroy(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            await session.stop()

    def get(self, session_id: str) -> Optional[VoiceSession]:
        return self._sessions.get(session_id)

    @property
    def count(self) -> int:
        return len(self._sessions)


# Module-level singleton
session_manager = SessionManager()
