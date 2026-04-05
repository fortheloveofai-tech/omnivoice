"""
TTS Provider: OpenAI TTS (tts-1, tts-1-hd)
============================================
Uses OpenAI's streaming TTS API.
Buffers tokens into sentences before synthesising to improve
naturalness while keeping latency low.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import AsyncIterator

from omni_voice.providers.base import TTSProvider

logger = logging.getLogger(__name__)

# Sentence-boundary characters — we flush to TTS when we see one
_SENTENCE_ENDS = {".", "!", "?", "\n"}
# Minimum token buffer length before flushing (avoid very short clips)
_MIN_CHUNK_CHARS = 20


class OpenAITTS(TTSProvider):
    """
    Streaming TTS via OpenAI's audio speech endpoint.

    Accumulates tokens into sentence-level chunks before sending to
    the API, then streams the returned MP3/PCM back to the caller.

    Parameters
    ----------
    api_key:
        OpenAI API key.
    model:
        "tts-1" (fastest) or "tts-1-hd" (highest quality).
    voice:
        One of: alloy | echo | fable | onyx | nova | shimmer.
    speed:
        Playback speed multiplier (0.25–4.0, default 1.0).
    response_format:
        "opus" for lowest latency streaming, "mp3" for compatibility.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "tts-1",
        voice: str = "alloy",
        speed: float = 1.0,
        response_format: str = "opus",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.speed = speed
        self.response_format = response_format
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(api_key=self.api_key)
            except ImportError:
                logger.error("openai package not installed: pip install openai")
                raise
        return self._client

    async def _synthesize_text(self, text: str) -> bytes:
        """Synthesise a text chunk and return raw audio bytes."""
        client = self._get_client()
        try:
            response = await client.audio.speech.create(
                model=self.model,
                voice=self.voice,
                input=text,
                speed=self.speed,
                response_format=self.response_format,
            )
            return response.content
        except Exception as exc:
            logger.error("OpenAI TTS error for text=%r: %s", text[:50], exc)
            return b""

    async def synthesize_stream(
        self, token_stream: AsyncIterator[str]
    ) -> AsyncIterator[bytes]:
        """
        Buffer tokens into sentence chunks and stream synthesised audio.
        """
        buffer = ""
        async for token in token_stream:
            buffer += token
            # Flush on sentence boundary or when buffer is long enough
            last_char = token[-1] if token else ""
            if (last_char in _SENTENCE_ENDS and len(buffer) >= _MIN_CHUNK_CHARS) or \
               len(buffer) >= 200:
                chunk = buffer.strip()
                buffer = ""
                if chunk:
                    audio = await self._synthesize_text(chunk)
                    if audio:
                        yield audio

        # Flush remaining text
        if buffer.strip():
            audio = await self._synthesize_text(buffer.strip())
            if audio:
                yield audio

    async def synthesize(self, text: str) -> bytes:
        """Synthesise complete text and return all audio bytes."""
        return await self._synthesize_text(text)

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


class ElevenLabsTTS(TTSProvider):
    """
    Streaming TTS via ElevenLabs API.
    Produces the most natural-sounding voice output.
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",
        model_id: str = "eleven_turbo_v2_5",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
    ) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.stability = stability
        self.similarity_boost = similarity_boost

    async def synthesize_stream(
        self, token_stream: AsyncIterator[str]
    ) -> AsyncIterator[bytes]:
        try:
            from elevenlabs.client import AsyncElevenLabs
            from elevenlabs import VoiceSettings
        except ImportError:
            logger.warning("elevenlabs SDK not installed: pip install elevenlabs")
            async for _ in token_stream:
                pass
            return

        client = AsyncElevenLabs(api_key=self.api_key)

        async def text_chunks():
            async for token in token_stream:
                yield token

        async for chunk in await client.generate(
            text=text_chunks(),
            voice=self.voice_id,
            model=self.model_id,
            voice_settings=VoiceSettings(
                stability=self.stability,
                similarity_boost=self.similarity_boost,
            ),
            stream=True,
        ):
            if chunk:
                yield chunk

    async def synthesize(self, text: str) -> bytes:
        """Returns raw MP3 audio bytes from ElevenLabs (bypasses SDK to avoid version issues)."""
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={
                    "text": text,
                    "model_id": self.model_id,
                    "voice_settings": {
                        "stability": self.stability,
                        "similarity_boost": self.similarity_boost,
                    },
                },
            )
            resp.raise_for_status()
            logger.info("ElevenLabs TTS: %d bytes returned", len(resp.content))
            return resp.content

    async def close(self) -> None:
        pass
