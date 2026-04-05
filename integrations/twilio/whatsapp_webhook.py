"""
whatsapp_webhook.py — Twilio WhatsApp integration for OmniVoice
================================================================

Handles two incoming message types from WhatsApp:

  Text message  → LLM → text reply
  Voice note    → download OGG/MP3 → resample to PCM-16 16kHz
                → ASR (Whisper or Deepgram) → LLM → TTS → send OGG voice note back

Webhook URL to set in Twilio Console:
  POST  https://<your-domain>/twilio/whatsapp

Quick-start
-----------
  pip install fastapi uvicorn twilio httpx pydub openai

  # Set in .env:
  TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  TWILIO_WHATSAPP_FROM=whatsapp:+14155238886   # Twilio sandbox number
  # Plus your existing OPENAI_API_KEY / DEEPGRAM_API_KEY
"""

import io
import logging
import tempfile
import os
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import PlainTextResponse

# OmniVoice provider factory
from omni_voice.providers import build_asr, build_llm, build_tts
from omni_voice.config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/twilio", tags=["twilio"])

# ── Lazy provider singletons ─────────────────────────────────────────────────

_asr = None
_llm = None
_tts = None

def get_asr():
    global _asr
    if _asr is None:
        _asr = build_asr()
    return _asr

def get_llm():
    global _llm
    if _llm is None:
        _llm = build_llm()
    return _llm

def get_tts():
    global _tts
    if _tts is None:
        _tts = build_tts()
    return _tts


# ── Twilio request validation ─────────────────────────────────────────────────

def _validate_twilio_request(request: Request, params: dict) -> bool:
    """Validate that the request genuinely came from Twilio."""
    try:
        auth_token = getattr(settings, "twilio_auth_token", "")
        if not auth_token:
            return True  # no token configured, skip validation in dev
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(auth_token)
        signature = request.headers.get("X-Twilio-Signature", "")
        # Use the public URL — internal URL differs from what Twilio signed
        base = getattr(settings, "public_base_url", "").rstrip("/")
        path = request.url.path
        query = ("?" + str(request.url.query)) if request.url.query else ""
        url = f"{base}{path}{query}" if base else str(request.url)
        result = validator.validate(url, params, signature)
        if not result:
            log.debug("Twilio sig check failed | url=%s | params=%s | sig=%s", url, params, signature)
        return result
    except Exception as e:
        log.warning("Twilio validation failed: %s", e)
        return True  # fail open in dev so we can test without valid signatures


# ── Audio helpers ────────────────────────────────────────────────────────────

async def _download_media(url: str) -> bytes:
    """Download a Twilio media attachment (voice note OGG/MP3)."""
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    async with httpx.AsyncClient() as client:
        r = await client.get(url, auth=auth, follow_redirects=True, timeout=30)
        r.raise_for_status()
        return r.content


def _to_pcm16_16khz(audio_bytes: bytes, mime: str = "audio/ogg") -> bytes:
    """
    Convert any audio format (OGG/Opus, MP3, WAV …) to PCM-16 mono 16 kHz.
    Requires pydub + ffmpeg.
    """
    try:
        from pydub import AudioSegment
    except ImportError:
        raise RuntimeError("pydub is required: pip install pydub")

    fmt = "ogg" if "ogg" in mime else "mp3" if "mp3" in mime else "wav"
    seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format=fmt)
    seg = seg.set_channels(1).set_frame_rate(16000).set_sample_width(2)
    return seg.raw_data


async def _tts_to_ogg(text: str) -> bytes:
    """
    Synthesise `text` with the configured TTS provider and return OGG/Opus bytes
    suitable for sending as a WhatsApp voice note.

    - OpenAI TTS  → returns raw PCM-16 mono 16kHz (opus format via API)
    - ElevenLabs  → returns MP3 bytes
    Both are re-encoded to OGG/Opus for WhatsApp.
    """
    from omni_voice.providers.tts.openai import ElevenLabsTTS as _ElevenLabsTTS
    from omni_voice.providers.tts.edge import EdgeTTS as _EdgeTTS

    tts = get_tts()
    audio_bytes = await tts.synthesize(text)

    if not audio_bytes:
        raise RuntimeError("TTS returned empty audio")

    try:
        from pydub import AudioSegment

        if isinstance(tts, (_ElevenLabsTTS, _EdgeTTS)):
            # ElevenLabs and EdgeTTS both return MP3 — load directly
            log.info("%s MP3 (%d bytes) → OGG/Opus", type(tts).__name__, len(audio_bytes))
            seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
        else:
            # OpenAI TTS returns raw PCM-16 mono 16kHz
            log.info("OpenAI PCM (%d bytes) → OGG/Opus", len(audio_bytes))
            seg = AudioSegment(
                data=audio_bytes,
                sample_width=2,
                frame_rate=16000,
                channels=1,
            )

        buf = io.BytesIO()
        seg.export(buf, format="ogg", codec="libopus")
        return buf.getvalue()
    except ImportError:
        return audio_bytes


# ── LLM helper ───────────────────────────────────────────────────────────────

async def _llm_respond(user_text: str, history: list[dict] | None = None) -> str:
    """Run one LLM turn and return the full response string."""
    llm = get_llm()
    chunks = []
    async for token in llm.stream_tokens(
        prompt=user_text,
        history=history or [],
        system_prompt="You are a helpful voice assistant. Keep responses concise.",
    ):
        chunks.append(token)
    return "".join(chunks)


# ── Twilio TwiML helpers ─────────────────────────────────────────────────────

def _twiml_text(body: str) -> str:
    """Return a minimal TwiML MessagingResponse with a text reply."""
    safe = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message>{safe}</Message>
</Response>"""


def _twiml_media(body: str, media_url: str) -> str:
    """Return a TwiML MessagingResponse with text + a media (voice note) attachment."""
    safe = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message>
    <Body>{safe}</Body>
    <Media>{media_url}</Media>
  </Message>
</Response>"""


# ── Upload TTS audio so Twilio can fetch it ───────────────────────────────────

_AUDIO_STORE: dict[str, bytes] = {}   # in-memory; replace with S3/GCS in prod

@router.get("/audio/{filename}")
async def serve_audio(filename: str) -> Response:
    """Serve a cached TTS audio blob to Twilio for voice note delivery."""
    data = _AUDIO_STORE.get(filename)
    if not data:
        return Response(status_code=404)
    return Response(content=data, media_type="audio/ogg")


def _store_audio(audio_bytes: bytes) -> str:
    """Persist audio bytes and return a public URL Twilio can fetch."""
    import uuid
    filename = f"{uuid.uuid4().hex}.ogg"
    _AUDIO_STORE[filename] = audio_bytes
    base_url = getattr(settings, "public_base_url", "http://localhost:8000")
    return f"{base_url}/twilio/audio/{filename}"


# ── Main WhatsApp webhook ────────────────────────────────────────────────────

@router.post("/whatsapp", response_class=PlainTextResponse)
async def whatsapp_webhook(
    request: Request,
    # Twilio form fields
    Body:          Annotated[str,  Form()] = "",
    From:          Annotated[str,  Form()] = "",
    NumMedia:      Annotated[int,  Form()] = 0,
    MediaUrl0:     Annotated[str,  Form()] = "",
    MediaContentType0: Annotated[str, Form()] = "",
):
    """
    Twilio WhatsApp webhook.

    Text message  → LLM → text reply
    Voice note    → ASR → LLM → TTS → voice note reply
    """
    # Get ALL form fields Twilio sent — needed for correct signature validation
    raw_form = await request.form()
    form_params = {k: v for k, v in raw_form.items()}

    if not _validate_twilio_request(request, form_params):
        log.warning("Invalid Twilio signature from %s", request.client)
        return Response(status_code=403)

    sender = From  # e.g. "whatsapp:+14155551234"
    log.info("WhatsApp message from %s | media=%d | body=%r", sender, NumMedia, Body[:80])

    # ── Voice note path ───────────────────────────────────────────────────────
    if NumMedia > 0 and MediaUrl0 and "audio" in MediaContentType0:
        try:
            log.info("Downloading voice note: %s", MediaUrl0)
            raw_audio  = await _download_media(MediaUrl0)

            # ASR: normalise content type (audio/ogg; codecs=opus → audio/ogg)
            asr = get_asr()
            content_type = MediaContentType0.split(";")[0].strip() or "audio/ogg"
            transcript = await asr.transcribe(raw_audio, content_type=content_type)
            log.info("Transcript: %r", transcript)

            if not transcript.strip():
                return PlainTextResponse(_twiml_text("Sorry, I couldn't make out what you said. Could you try again?"))

            # LLM: respond
            reply_text = await _llm_respond(transcript)
            log.info("LLM reply: %r", reply_text[:100])

            # TTS: synthesise reply as OGG voice note
            try:
                ogg_bytes  = await _tts_to_ogg(reply_text)
                audio_url  = _store_audio(ogg_bytes)
                twiml      = _twiml_media(reply_text, audio_url)
            except Exception as tts_err:
                log.exception("TTS failed — falling back to text reply: %s", tts_err)
                twiml = _twiml_text(reply_text)

            return PlainTextResponse(twiml, media_type="text/xml")

        except Exception as e:
            log.exception("Voice note pipeline failed: %s", e)
            return PlainTextResponse(
                _twiml_text("Something went wrong processing your voice message. Please try again."),
                media_type="text/xml",
            )

    # ── Text message path ─────────────────────────────────────────────────────
    if Body.strip():
        try:
            reply_text = await _llm_respond(Body.strip())
            return PlainTextResponse(_twiml_text(reply_text), media_type="text/xml")
        except Exception as e:
            log.exception("LLM failed for text message: %s", e)
            return PlainTextResponse(
                _twiml_text("I couldn't generate a response right now. Please try again."),
                media_type="text/xml",
            )

    # Empty message
    return PlainTextResponse(_twiml_text("Hi! Send me a text or a voice note."), media_type="text/xml")
