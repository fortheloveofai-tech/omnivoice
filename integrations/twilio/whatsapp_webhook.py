"""
OmniVoice — WhatsApp Webhook (Twilio)

Receives incoming WhatsApp voice notes via Twilio webhook,
transcribes with ASR, generates reply with LLM, and sends
back a voice note reply via TTS.

Start with: python start.py
"""
import asyncio
import io
import os
import uuid
from typing import Dict

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import StreamingResponse

load_dotenv()

app = FastAPI(title="OmniVoice WhatsApp")

# In-memory audio store: filename → ogg bytes
_audio_store: Dict[str, bytes] = {}

ASR_PROVIDER = os.getenv("ASR_PROVIDER", "whisper").lower()
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "edge").lower()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN", "")
PUBLIC_URL         = os.getenv("PUBLIC_URL", "")  # set by start.py


# ── Provider loaders ──────────────────────────────────────────────────────────
def _get_asr():
    if ASR_PROVIDER == "deepgram":
        from omni_voice.asr import deepgram
        return deepgram
    from omni_voice.asr import whisper_local
    return whisper_local


def _get_tts():
    if TTS_PROVIDER == "elevenlabs":
        from omni_voice.tts import elevenlabs
        return elevenlabs
    if TTS_PROVIDER == "openai":
        from omni_voice.tts import openai_tts
        return openai_tts
    from omni_voice.tts import edge_tts
    return edge_tts


def _get_llm():
    if LLM_PROVIDER == "openai":
        from omni_voice.llm import openai
        return openai
    if LLM_PROVIDER == "ollama":
        from omni_voice.llm import ollama
        return ollama
    if LLM_PROVIDER == "openrouter":
        from omni_voice.llm import openrouter
        return openrouter
    from omni_voice.llm import anthropic
    return anthropic


# ── Audio helpers ─────────────────────────────────────────────────────────────
async def _download_audio(media_url: str) -> bytes:
    """Download audio from Twilio media URL (requires auth)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            media_url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        )
        resp.raise_for_status()
        return resp.content


def _to_wav(audio_bytes: bytes) -> bytes:
    """Convert any audio format to 16kHz mono WAV using pydub."""
    from pydub import AudioSegment
    seg = AudioSegment.from_file(io.BytesIO(audio_bytes))
    seg = seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    buf = io.BytesIO()
    seg.export(buf, format="wav")
    return buf.getvalue()


def _mp3_to_ogg(mp3_bytes: bytes) -> bytes:
    """Convert MP3 bytes to OGG/Opus for WhatsApp voice note delivery."""
    from pydub import AudioSegment
    seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
    buf = io.BytesIO()
    seg.export(buf, format="ogg", codec="libopus")
    return buf.getvalue()


def _tts_to_ogg(tts_bytes: bytes) -> bytes:
    """Convert TTS output to OGG. Handles both MP3 (EdgeTTS/ElevenLabs) and PCM (OpenAI)."""
    if TTS_PROVIDER == "openai":
        # OpenAI TTS returns MP3 too
        return _mp3_to_ogg(tts_bytes)
    # EdgeTTS and ElevenLabs both return MP3
    return _mp3_to_ogg(tts_bytes)


# ── Webhook endpoint ──────────────────────────────────────────────────────────
@app.post("/twilio/whatsapp")
async def whatsapp_webhook(
    request: Request,
    Body: str = Form(default=""),
    NumMedia: str = Form(default="0"),
    MediaUrl0: str = Form(default=""),
    MediaContentType0: str = Form(default=""),
    From: str = Form(default=""),
):
    """Handle incoming WhatsApp messages from Twilio."""

    # ── Text message ──
    if Body and not MediaUrl0:
        reply_text = await _get_llm().generate(Body)
        return _twiml_text(reply_text)

    # ── Voice note ──
    if MediaUrl0:
        try:
            # 1. Download audio
            raw_audio = await _download_audio(MediaUrl0)

            # 2. Normalise to WAV for ASR
            content_type = MediaContentType0 or "audio/ogg"
            wav_bytes = _to_wav(raw_audio)

            # 3. Transcribe
            transcript = await _get_asr().transcribe(wav_bytes)
            if not transcript:
                return _twiml_text("Sorry, I couldn't hear that. Please try again.")

            # 4. Generate LLM reply
            reply_text = await _get_llm().generate(transcript)

            # 5. Synthesise speech
            tts_bytes = await _get_tts().synthesize(reply_text)
            ogg_bytes = _tts_to_ogg(tts_bytes)

            # 6. Store in memory and get public URL
            filename = f"{uuid.uuid4().hex}.ogg"
            _audio_store[filename] = ogg_bytes
            audio_url = f"{PUBLIC_URL}/twilio/audio/{filename}"

            return _twiml_voice(audio_url)

        except Exception as e:
            print(f"[ERROR] {e}")
            return _twiml_text("Something went wrong. Please try again.")

    return Response(content="<Response/>", media_type="application/xml")


@app.get("/twilio/audio/{filename}")
async def serve_audio(filename: str):
    """Serve stored OGG audio files to Twilio."""
    if filename not in _audio_store:
        return Response(status_code=404)
    data = _audio_store.pop(filename)  # serve once, then discard
    return Response(content=data, media_type="audio/ogg")


@app.get("/health")
async def health():
    return {"status": "ok", "asr": ASR_PROVIDER, "tts": TTS_PROVIDER, "llm": LLM_PROVIDER}


# ── TwiML helpers ─────────────────────────────────────────────────────────────
def _twiml_text(message: str) -> Response:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message>{message}</Message>
</Response>"""
    return Response(content=xml, media_type="application/xml")


def _twiml_voice(audio_url: str) -> Response:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message>
    <Media>{audio_url}</Media>
  </Message>
</Response>"""
    return Response(content=xml, media_type="application/xml")
