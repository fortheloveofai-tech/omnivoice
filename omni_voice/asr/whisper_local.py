"""
Local Whisper ASR — runs entirely on your machine, no API key needed.
Model is lazy-loaded on first transcription call.
"""
import asyncio
import io
import os
import tempfile
from functools import lru_cache

MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")


@lru_cache(maxsize=1)
def _load_model():
    import whisper
    print(f"  Loading Whisper model: {MODEL_SIZE} …", flush=True)
    return whisper.load_model(MODEL_SIZE)


async def transcribe(audio_bytes: bytes, content_type: str = "audio/wav") -> str:
    """Transcribe audio bytes to text using local Whisper."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _transcribe_sync, audio_bytes)


def _transcribe_sync(audio_bytes: bytes) -> str:
    model = _load_model()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        result = model.transcribe(tmp_path)
        return result["text"].strip()
    finally:
        os.unlink(tmp_path)
