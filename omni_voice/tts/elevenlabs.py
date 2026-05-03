"""
ElevenLabs TTS — best quality, paid. Set ELEVENLABS_API_KEY in .env
Returns MP3 bytes.
"""
import os
import httpx

API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")  # Bella
API_URL = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"


async def synthesize(text: str) -> bytes:
    if not API_KEY:
        raise ValueError("ELEVENLABS_API_KEY not set. Run python setup_env.py")

    headers = {
        "xi-api-key": API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(API_URL, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.content
