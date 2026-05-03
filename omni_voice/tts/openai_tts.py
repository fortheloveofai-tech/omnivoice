"""
OpenAI TTS — requires OPENAI_API_KEY.
Returns MP3 bytes.
"""
import os

VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")
MODEL = os.getenv("OPENAI_TTS_MODEL", "tts-1")


async def synthesize(text: str) -> bytes:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = await client.audio.speech.create(
        model=MODEL,
        voice=VOICE,
        input=text,
    )
    return response.content
