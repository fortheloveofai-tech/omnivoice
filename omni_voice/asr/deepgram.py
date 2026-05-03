"""
Deepgram cloud ASR — fast, free tier available at console.deepgram.com
Set DEEPGRAM_API_KEY in your .env
"""
import os
import httpx

API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
API_URL = "https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true"


async def transcribe(audio_bytes: bytes, content_type: str = "audio/ogg") -> str:
    if not API_KEY:
        raise ValueError("DEEPGRAM_API_KEY not set. Run python setup_env.py")

    headers = {
        "Authorization": f"Token {API_KEY}",
        "Content-Type": content_type,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(API_URL, headers=headers, content=audio_bytes)
        resp.raise_for_status()
        data = resp.json()

    try:
        return data["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
    except (KeyError, IndexError):
        return ""
