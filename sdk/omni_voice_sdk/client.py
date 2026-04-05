"""
Omni Voice Python SDK
======================
A pip-installable client for the Omni Voice platform.

Quick start
-----------
    from omni_voice_sdk import VoiceCopilotClient

    async def main():
        client = VoiceCopilotClient("ws://localhost:8000/ws/voice")
        client.on_audio = lambda pcm: play_audio(pcm)
        client.on_transcript = lambda text: print(text)

        await client.connect()
        await client.start_session()

        # Stream microphone audio
        async for frame in mic_stream():
            await client.send_audio(frame)

        await client.end_session()
        await client.disconnect()

API surface mirrors the Swift/Kotlin SDK spec from cycle_09.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class SessionStats:
    """Per-session performance statistics."""
    session_id: str = ""
    audio_frames_sent: int = 0
    audio_bytes_received: int = 0
    transcripts_received: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_sec(self) -> float:
        return time.monotonic() - self.started_at


class VoiceCopilotClient:
    """
    Async Python client for the Omni Voice platform.

    Parameters
    ----------
    endpoint:
        WebSocket URL of the Omni Voice server, e.g.:
        "ws://localhost:8000/ws/voice"
    on_audio:
        Callback invoked with each raw audio bytes chunk received
        from TTS. Connect this to your audio output device.
    on_transcript:
        Callback invoked with each partial transcript string
        received from ASR.
    on_session_started:
        Callback invoked when the server confirms session start.
    on_error:
        Callback invoked on any error.
    auto_reconnect:
        Whether to automatically reconnect on disconnect (default True).
    max_reconnect_attempts:
        Maximum reconnect attempts before giving up (default 5).
    """

    def __init__(
        self,
        endpoint: str = "ws://localhost:8000/ws/voice",
        on_audio: Optional[Callable[[bytes], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_session_started: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        auto_reconnect: bool = True,
        max_reconnect_attempts: int = 5,
    ) -> None:
        self.endpoint = endpoint
        self.on_audio = on_audio
        self.on_transcript = on_transcript
        self.on_session_started = on_session_started
        self.on_error = on_error
        self.auto_reconnect = auto_reconnect
        self.max_reconnect_attempts = max_reconnect_attempts

        self._ws = None
        self._session_id: Optional[str] = None
        self._stats = SessionStats()
        self._connected = False
        self._receive_task: Optional[asyncio.Task] = None
        self._metrics_task: Optional[asyncio.Task] = None

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the WebSocket connection to the Omni Voice server."""
        try:
            import websockets
        except ImportError:
            raise ImportError(
                "websockets not installed. "
                "Install the SDK with: pip install omni-voice-sdk"
            )

        logger.info("Connecting to %s", self.endpoint)
        self._ws = await websockets.connect(
            self.endpoint,
            ping_interval=20,
            ping_timeout=10,
            max_size=10 * 1024 * 1024,   # 10 MB max frame
        )
        self._connected = True
        # Start background receive loop
        self._receive_task = asyncio.create_task(
            self._receive_loop(), name="omnivoice-recv"
        )
        logger.info("Connected to Omni Voice at %s", self.endpoint)

    async def disconnect(self) -> None:
        """Cleanly close the WebSocket connection."""
        self._connected = False
        if self._receive_task:
            self._receive_task.cancel()
        if self._metrics_task:
            self._metrics_task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None

    # ── Session control ───────────────────────────────────────────────────────

    async def start_session(self) -> str:
        """
        Request a new voice session from the server.

        Returns
        -------
        str
            The session ID once the server confirms.
        """
        await self._send_json({"type": "start_session"})
        # Wait for confirmation
        for _ in range(50):   # up to 5 seconds
            if self._session_id:
                return self._session_id
            await asyncio.sleep(0.1)
        raise TimeoutError("Server did not confirm session start within 5 seconds")

    async def end_session(self) -> None:
        """Gracefully end the current voice session."""
        if self._session_id:
            await self._send_json({"type": "end_session"})
            self._session_id = None

    # ── Audio I/O ─────────────────────────────────────────────────────────────

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """
        Send a raw PCM audio chunk to the server.

        Parameters
        ----------
        pcm_bytes:
            16-bit signed PCM, 16 kHz, mono.
            Recommended chunk size: 20 ms = 320 samples = 640 bytes.
        """
        if not self._ws or not self._connected:
            logger.warning("send_audio called but not connected")
            return
        try:
            await self._ws.send(pcm_bytes)
            self._stats.audio_frames_sent += 1
        except Exception as exc:
            logger.error("send_audio error: %s", exc)
            if self.on_error:
                self.on_error(str(exc))

    async def update_network_metrics(
        self, rtt_ms: float, loss_pct: float = 0.0, bw_kbps: float = 1000.0
    ) -> None:
        """
        Push network quality metrics to the AQAL controller.
        Call this approximately every 50–100 ms for best adaptation.
        """
        await self._send_json({
            "type": "network_metrics",
            "rtt_ms": rtt_ms,
            "loss_pct": loss_pct,
            "bw_kbps": bw_kbps,
        })

    # ── Convenience: microphone capture ──────────────────────────────────────

    async def stream_microphone(self, duration_sec: float = 0) -> None:
        """
        Capture microphone audio and stream it to the server.
        Requires the ``sounddevice`` package.

        Parameters
        ----------
        duration_sec:
            Stop after this many seconds (0 = run until cancelled).
        """
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            raise ImportError(
                "sounddevice and numpy are required for microphone capture. "
                "Install with: pip install sounddevice numpy"
            )

        SAMPLE_RATE = 16_000
        CHUNK_FRAMES = 320   # 20 ms at 16 kHz

        logger.info("Starting microphone capture (Ctrl+C to stop)")
        loop = asyncio.get_event_loop()
        audio_queue: asyncio.Queue[bytes] = asyncio.Queue()

        def callback(indata, frames, time_info, status):
            pcm = (indata[:, 0] * 32767).astype("int16").tobytes()
            loop.call_soon_threadsafe(audio_queue.put_nowait, pcm)

        deadline = time.monotonic() + duration_sec if duration_sec > 0 else float("inf")

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=CHUNK_FRAMES,
            callback=callback,
        ):
            while time.monotonic() < deadline:
                try:
                    chunk = await asyncio.wait_for(audio_queue.get(), timeout=1.0)
                    await self.send_audio(chunk)
                except asyncio.TimeoutError:
                    continue

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def stats(self) -> SessionStats:
        return self._stats

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _send_json(self, data: dict) -> None:
        if not self._ws:
            return
        try:
            await self._ws.send(json.dumps(data))
        except Exception as exc:
            logger.error("_send_json error: %s", exc)

    async def _receive_loop(self) -> None:
        """Background loop that routes incoming messages to callbacks."""
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    # Binary = synthesised audio
                    self._stats.audio_bytes_received += len(message)
                    if self.on_audio:
                        self.on_audio(message)
                else:
                    # Text = JSON control message
                    try:
                        data = json.loads(message)
                        await self._handle_control(data)
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON text: %r", message[:100])
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Receive loop error: %s", exc)
            if self.on_error:
                self.on_error(str(exc))

    async def _handle_control(self, data: dict) -> None:
        msg_type = data.get("type")

        if msg_type == "session_started":
            self._session_id = data.get("session_id", "")
            self._stats = SessionStats(session_id=self._session_id)
            logger.info("Session started: %s", self._session_id)
            if self.on_session_started:
                self.on_session_started(self._session_id)

        elif msg_type == "transcript":
            text = data.get("text", "")
            self._stats.transcripts_received += 1
            if self.on_transcript:
                self.on_transcript(text)

        elif msg_type == "session_ended":
            self._session_id = None
            logger.info("Session ended")

        elif msg_type == "error":
            msg = data.get("message", "Unknown error")
            logger.error("Server error: %s", msg)
            if self.on_error:
                self.on_error(msg)
