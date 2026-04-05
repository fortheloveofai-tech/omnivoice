"""
ASR Provider: Deepgram (streaming, low-latency)
================================================
Uses Deepgram's WebSocket streaming API for real-time transcription.
Deepgram's Nova-2 model achieves ~200-300 ms latency on 16 kHz audio,
making it the recommended primary ASR provider.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import httpx

from omni_voice.providers.base import ASRProvider

logger = logging.getLogger(__name__)


class DeepgramASR(ASRProvider):
    """
    Streaming ASR via Deepgram Nova-2.

    Parameters
    ----------
    api_key:
        Deepgram API key.
    language:
        BCP-47 language code (default "en-US").
    model:
        Deepgram model name (default "nova-2").
    sample_rate:
        Audio sample rate in Hz (default 16000).
    """

    def __init__(
        self,
        api_key: str,
        language: str = "en-US",
        model: str = "nova-2",
        sample_rate: int = 16_000,
    ) -> None:
        self.api_key = api_key
        self.language = language
        self.model = model
        self.sample_rate = sample_rate
        self._ws = None

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[str]:
        """
        Stream audio to Deepgram and yield transcript fragments.
        Falls back to a mock if the deepgram SDK isn't installed.
        """
        try:
            from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents
        except ImportError:
            logger.warning(
                "deepgram-sdk not installed — falling back to mock ASR. "
                "Install with: pip install deepgram-sdk"
            )
            async for chunk in audio_stream:
                _ = chunk  # consume audio
            yield "[deepgram-sdk not installed — install to enable real ASR]"
            return

        client = DeepgramClient(self.api_key)
        options = LiveOptions(
            model=self.model,
            language=self.language,
            encoding="linear16",
            sample_rate=self.sample_rate,
            channels=1,
            interim_results=True,
            utterance_end_ms=1000,
            vad_events=True,
            smart_format=True,
        )

        transcript_queue: asyncio.Queue[str] = asyncio.Queue()
        done = asyncio.Event()

        connection = client.listen.asyncwebsocket.v("1")

        async def on_message(self_ref, result, **kwargs):
            try:
                sentence = result.channel.alternatives[0].transcript
                if sentence:
                    await transcript_queue.put(sentence)
            except (AttributeError, IndexError):
                pass

        async def on_error(self_ref, error, **kwargs):
            logger.error("Deepgram error: %s", error)
            done.set()

        async def on_close(self_ref, close, **kwargs):
            done.set()

        connection.on(LiveTranscriptionEvents.Transcript, on_message)
        connection.on(LiveTranscriptionEvents.Error, on_error)
        connection.on(LiveTranscriptionEvents.Close, on_close)

        await connection.start(options)

        # Feed audio in a background task
        async def feed_audio():
            async for chunk in audio_stream:
                connection.send(chunk)
            await connection.finish()
            done.set()

        feed_task = asyncio.create_task(feed_audio())

        try:
            while not done.is_set() or not transcript_queue.empty():
                try:
                    text = await asyncio.wait_for(transcript_queue.get(), timeout=0.1)
                    yield text
                except asyncio.TimeoutError:
                    continue
        finally:
            feed_task.cancel()

    async def transcribe(self, audio_bytes: bytes, content_type: str = "audio/wav") -> str:
        """
        One-shot transcription via Deepgram's prerecorded REST API.
        Ideal for complete audio files (e.g. WhatsApp voice notes).
        No SDK version dependency — uses httpx directly.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.deepgram.com/v1/listen",
                headers={
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": content_type,
                },
                params={
                    "model": self.model,
                    "language": self.language,
                    "smart_format": "true",
                },
                content=audio_bytes,
            )
            response.raise_for_status()
            data = response.json()
            try:
                return data["results"]["channels"][0]["alternatives"][0]["transcript"]
            except (KeyError, IndexError):
                logger.warning("Deepgram returned no transcript: %s", data)
                return ""

    async def close(self) -> None:
        pass   # connection is closed per-stream


class MockASR(ASRProvider):
    """
    Echo ASR for testing without credentials.
    Simulates latency and yields canned responses.
    """

    def __init__(self, latency_ms: int = 80) -> None:
        self.latency_ms = latency_ms

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[str]:
        responses = [
            "Hello, how are you?",
            "Tell me about the weather today.",
            "What is the capital of France?",
        ]
        i = 0
        async for _ in audio_stream:
            await asyncio.sleep(self.latency_ms / 1000)
            yield responses[i % len(responses)]
            i += 1

    async def close(self) -> None:
        pass
