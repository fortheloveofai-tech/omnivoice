"""
Microsoft EdgeTTS — free neural voices, no API key needed.
Returns MP3 bytes.
"""
import io
import os

VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")


async def synthesize(text: str) -> bytes:
    """Convert text to speech using EdgeTTS. Returns MP3 bytes."""
    import edge_tts
    communicate = edge_tts.Communicate(text, VOICE)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    buf.seek(0)
    return buf.read()
