"""
voice_call_handler.py — Real-time phone-call integration for OmniVoice
=======================================================================

When someone calls your Twilio number, Twilio:
  1. Hits POST /twilio/call/incoming  → we return TwiML that opens a Media Stream
  2. Opens a WebSocket to POST /twilio/call/stream  → we receive μ-law 8kHz audio
  3. We resample to PCM-16 16kHz, feed into the OmniVoice session pipeline
  4. ASR → LLM → TTS, then we send μ-law audio frames back over the same WebSocket

Architecture
------------

  PSTN caller
      │  (phone call)
  Twilio
      │  HTTP POST → /twilio/call/incoming   (returns TwiML <Stream>)
      │
      │  WebSocket  ←→  /twilio/call/stream
      │    inbound: JSON {event:"media", payload:{...audio...}}
      │    outbound: JSON {event:"media", payload:{...tts audio...}}
      │
  OmniVoice
      └── VoiceSession (ASR → ATTS → LLM → TAB → AQAL → TTS)

Audio format
------------
Twilio Media Streams send/receive 8kHz μ-law (PCMU).
OmniVoice internals use 16kHz PCM-16.
We resample at the boundary using audioop (stdlib) or scipy.

References
----------
https://www.twilio.com/docs/voice/twiml/stream
https://www.twilio.com/docs/voice/media-streams
"""

import asyncio
import audioop
import base64
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

from omni_voice.providers import build_asr, build_llm, build_tts
from omni_voice.config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/twilio/call", tags=["twilio-voice"])

# ── Audio resampling helpers ──────────────────────────────────────────────────

TWILIO_RATE = 8_000   # μ-law 8 kHz (Twilio default)
OMNI_RATE   = 16_000  # OmniVoice internal rate

_resample_state_in:  Optional[object] = None  # per-connection state for audioop
_resample_state_out: Optional[object] = None


def ulaw_to_pcm16(ulaw_bytes: bytes) -> bytes:
    """μ-law 8kHz → PCM-16 8kHz → PCM-16 16kHz (2× linear interpolation)."""
    pcm8 = audioop.ulaw2lin(ulaw_bytes, 2)           # → PCM-16 8kHz
    pcm16, _ = audioop.ratecv(pcm8, 2, 1, TWILIO_RATE, OMNI_RATE, None)
    return pcm16


def pcm16_to_ulaw(pcm16_bytes: bytes) -> bytes:
    """PCM-16 16kHz → PCM-16 8kHz → μ-law 8kHz."""
    pcm8, _ = audioop.ratecv(pcm16_bytes, 2, 1, OMNI_RATE, TWILIO_RATE, None)
    return audioop.lin2ulaw(pcm8, 2)


# ── TwiML: tell Twilio to open a Media Stream ─────────────────────────────────

def _stream_twiml(ws_url: str) -> str:
    """
    Return TwiML that:
      - Greets the caller
      - Opens a bidirectional Media Stream to our WebSocket endpoint
      - Keeps the call alive while the stream is open
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">
    Hello! You've reached the Omni Voice assistant. Please speak after the tone.
  </Say>
  <Connect>
    <Stream url="{ws_url}" track="inbound_track">
      <Parameter name="asr_language" value="en-US"/>
    </Stream>
  </Connect>
</Response>"""


# ── Incoming call endpoint ────────────────────────────────────────────────────

@router.post("/incoming", response_class=PlainTextResponse)
async def incoming_call(request: Request) -> PlainTextResponse:
    """
    Twilio calls this when a new call arrives.
    We respond with TwiML that opens a Media Stream WebSocket.
    """
    base_url = getattr(settings, "public_base_url", "http://localhost:8000")
    ws_url   = base_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url   = f"{ws_url}/twilio/call/stream"

    log.info("Incoming call → opening Media Stream at %s", ws_url)
    twiml = _stream_twiml(ws_url)
    return PlainTextResponse(twiml, media_type="text/xml")


# ── Per-call pipeline state ───────────────────────────────────────────────────

class CallSession:
    """
    Manages one live phone call.

    Inbound audio → buffer → flush to ASR when silence detected
                          → LLM → TTS → outbound audio back to Twilio
    """

    SILENCE_THRESHOLD_BYTES = OMNI_RATE * 2 * 1  # 1 second of silence → trigger ASR

    def __init__(self, call_sid: str, ws: WebSocket):
        self.call_sid  = call_sid
        self.ws        = ws
        self.asr       = build_asr()
        self.llm       = build_llm()
        self.tts       = build_tts()
        self._buf: bytearray = bytearray()
        self._silent_bytes   = 0
        self._history: list[dict] = [
            {"role": "system", "content": (
                "You are a helpful voice assistant. Keep responses concise — "
                "one or two sentences — because this is a phone call."
            )}
        ]
        self._pipeline_lock = asyncio.Lock()

    # ── Inbound audio from caller ──────────────────────────────────────────────

    def feed_audio(self, ulaw_payload: str) -> None:
        """Receive a base64 μ-law chunk from Twilio and buffer it."""
        raw   = base64.b64decode(ulaw_payload)
        pcm16 = ulaw_to_pcm16(raw)
        self._buf.extend(pcm16)

        # Simple energy-based silence detector
        energy = audioop.rms(pcm16, 2)
        if energy < 300:                        # ~silence threshold
            self._silent_bytes += len(pcm16)
        else:
            self._silent_bytes = 0

    def should_trigger_asr(self) -> bool:
        """Return True when there's enough audio + a trailing silence."""
        has_speech = len(self._buf) > OMNI_RATE * 2 * 0.5  # at least 0.5s of audio
        trailing_silence = self._silent_bytes >= self.SILENCE_THRESHOLD_BYTES
        return has_speech and trailing_silence

    def take_buffer(self) -> bytes:
        """Drain and return the accumulated PCM buffer."""
        data       = bytes(self._buf)
        self._buf  = bytearray()
        self._silent_bytes = 0
        return data

    # ── ASR → LLM → TTS pipeline ──────────────────────────────────────────────

    async def run_pipeline(self, pcm_bytes: bytes) -> None:
        """
        Full pipeline for one turn: PCM audio → text → LLM → TTS → send audio.
        Protected by a lock so only one turn runs at a time.
        """
        if self._pipeline_lock.locked():
            log.debug("[%s] pipeline busy, dropping turn", self.call_sid)
            return

        async with self._pipeline_lock:
            # 1. ASR
            transcript = await self.asr.transcribe(pcm_bytes, sample_rate=OMNI_RATE)
            log.info("[%s] transcript: %r", self.call_sid, transcript)
            if not transcript.strip():
                return

            # 2. LLM
            self._history.append({"role": "user", "content": transcript})
            response_chunks: list[str] = []
            async for token in self.llm.stream_tokens(
                prompt=transcript,
                history=self._history[:-1],
                system_prompt="You are a helpful voice assistant. Keep responses concise — one or two sentences — because this is a phone call.",
            ):
                response_chunks.append(token)
            response_text = "".join(response_chunks)
            self._history.append({"role": "assistant", "content": response_text})
            log.info("[%s] response: %r", self.call_sid, response_text[:100])

            # 3. TTS → stream audio back to caller
            await self._send_tts(response_text)

    async def _send_tts(self, text: str) -> None:
        """Synthesise text and stream μ-law chunks back to Twilio."""
        stream_sid = getattr(self, "_stream_sid", "")
        pcm_buf    = bytearray()

        async for chunk in self.tts.synthesise(text):
            pcm_buf.extend(chunk)
            # Send in 20ms frames (320 samples × 2 bytes at 16kHz)
            while len(pcm_buf) >= 640:
                frame    = bytes(pcm_buf[:640])
                pcm_buf  = pcm_buf[640:]
                ulaw     = pcm16_to_ulaw(frame)
                payload  = base64.b64encode(ulaw).decode()
                msg = json.dumps({
                    "event":     "media",
                    "streamSid": stream_sid,
                    "media":     {"payload": payload},
                })
                try:
                    await self.ws.send_text(msg)
                except Exception:
                    return

        # Flush remaining
        if pcm_buf:
            ulaw    = pcm16_to_ulaw(bytes(pcm_buf))
            payload = base64.b64encode(ulaw).decode()
            msg = json.dumps({
                "event":     "media",
                "streamSid": stream_sid,
                "media":     {"payload": payload},
            })
            try:
                await self.ws.send_text(msg)
            except Exception:
                pass


# ── WebSocket Media Stream endpoint ──────────────────────────────────────────

@router.websocket("/stream")
async def call_stream(ws: WebSocket) -> None:
    """
    Twilio Media Streams WebSocket.

    Message types:
      connected   → stream handshake
      start       → new stream, contains callSid / streamSid
      media       → audio chunk (base64 μ-law)
      stop        → call ended
    """
    await ws.accept()
    session: Optional[CallSession] = None
    pipeline_task: Optional[asyncio.Task] = None

    try:
        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event = msg.get("event")

            if event == "connected":
                log.info("Media stream connected: protocol=%s", msg.get("protocol"))

            elif event == "start":
                start      = msg["start"]
                call_sid   = start.get("callSid", str(uuid.uuid4()))
                stream_sid = start.get("streamSid", "")
                log.info("Stream started: call=%s stream=%s", call_sid, stream_sid)
                session = CallSession(call_sid, ws)
                session._stream_sid = stream_sid

            elif event == "media" and session:
                payload = msg.get("media", {}).get("payload", "")
                if payload:
                    session.feed_audio(payload)

                    # Check if we should trigger the ASR pipeline
                    if session.should_trigger_asr():
                        pcm = session.take_buffer()
                        # Run pipeline in background so we can keep receiving audio
                        if pipeline_task and not pipeline_task.done():
                            pipeline_task.cancel()
                        pipeline_task = asyncio.create_task(
                            session.run_pipeline(pcm)
                        )

            elif event == "stop":
                log.info("Stream stopped for call %s",
                         session.call_sid if session else "unknown")
                break

    except WebSocketDisconnect:
        log.info("WebSocket disconnected")
    except Exception as e:
        log.exception("Stream error: %s", e)
    finally:
        if pipeline_task:
            pipeline_task.cancel()
        log.info("Call session ended")
