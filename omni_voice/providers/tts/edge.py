"""
TTS Provider: Microsoft Edge TTS (free, no API key required)
============================================================
Uses the edge-tts library which calls Microsoft's neural TTS
endpoint — the same engine powering Edge Read Aloud.

Install: pip install edge-tts

Voices: https://aka.ms/msra-voices
Recommended: en-US-JennyNeural, en-US-GuyNeural, en-US-AriaNeural
"""
from __future__ import annotations

import io
import logging
from typing import AsyncIterator

from omni_voice.providers.base import TTSProvider

logger = logging.getLogger(__name__)


class EdgeTTS(TTSProvider):
    """
    Free Microsoft Edge neural TTS.
    Returns MP3 bytes — no account or API key needed.

    Parameters
    ----------
    voice:
        Any edge-tts voice name, e.g. "en-US-JennyNeural".
    rate:
        Speaking rate adjustment, e.g. "+0%", "-10%", "+20%".
    """

    def __init__(
        self,
        voice: str = "en-US-JennyNeural",
        rate: str = "+0%",
    ) -> None:
        self.voice = voice
        self.rate = rate

    async def synthesize(self, text: str) -> bytes:
        """Synthesise text and return MP3 bytes."""
        try:
            import edge_tts
        except ImportError:
            raise RuntimeError(
                "edge-tts not installed. Run: pip install edge-tts"
            )

        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        audio_bytes = buf.getvalue()
        logger.info("EdgeTTS: %d bytes for %d chars", len(audio_bytes), len(text))
        return audio_bytes

    async def synthesize_stream(
        self, token_stream: AsyncIterator[str]
    ) -> AsyncIterator[bytes]:
        """Accumulate tokens then synthesise."""
        tokens = []
        async for token in token_stream:
            tokens.append(token)
        text = "".join(tokens)
        if text.strip():
            yield await self.synthesize(text)

    async def close(self) -> None:
        pass
