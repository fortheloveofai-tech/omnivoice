"""
ASR Provider: faster-whisper (local, CPU/GPU)
=============================================
Uses the faster-whisper library for offline transcription.
Acts as the fallback when Deepgram is unavailable or when
privacy requirements prohibit sending audio to a third party.
"""
from __future__ import annotations

import asyncio
import io
import logging
import wave
from typing import AsyncIterator

import numpy as np

from omni_voice.providers.base import ASRProvider

logger = logging.getLogger(__name__)

# Audio constants
SAMPLE_RATE = 16_000
CHUNK_DURATION_SEC = 1.0          # accumulate 1 s before transcribing
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_DURATION_SEC)


class WhisperASR(ASRProvider):
    """
    Local streaming ASR using faster-whisper.

    Accumulates audio in sliding windows and transcribes each window,
    yielding delta text as new words appear.

    Parameters
    ----------
    model_size:
        Whisper model variant: tiny | base | small | medium | large.
    device:
        "cpu" or "cuda".
    compute_type:
        Quantisation type: "int8" (CPU) or "float16" (GPU).
    language:
        Force a language code (e.g. "en") or None for auto-detect.
    """

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = "en",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            logger.info("faster-whisper model '%s' loaded on %s", self.model_size, self.device)
        except ImportError:
            logger.warning(
                "faster-whisper not installed. "
                "Install with: pip install faster-whisper"
            )
            self._model = None
        return self._model

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[str]:
        model = self._load_model()
        if model is None:
            # Graceful degradation
            async for _ in audio_stream:
                pass
            yield "[faster-whisper not installed — install to enable local ASR]"
            return

        buffer = np.array([], dtype=np.int16)

        async for chunk in audio_stream:
            # chunk is raw PCM int16 bytes
            pcm = np.frombuffer(chunk, dtype=np.int16)
            buffer = np.concatenate([buffer, pcm])

            # Transcribe when we have at least CHUNK_SAMPLES
            while len(buffer) >= CHUNK_SAMPLES:
                window = buffer[:CHUNK_SAMPLES]
                buffer = buffer[CHUNK_SAMPLES:]

                # Convert int16 → float32 normalised [-1, 1]
                audio_f32 = window.astype(np.float32) / 32768.0

                # Run transcription in a thread pool (CPU-bound)
                loop = asyncio.get_event_loop()
                segments, _info = await loop.run_in_executor(
                    None,
                    lambda: model.transcribe(
                        audio_f32,
                        language=self.language,
                        beam_size=1,
                        vad_filter=True,
                    ),
                )
                for seg in segments:
                    text = seg.text.strip()
                    if text:
                        yield text

    async def close(self) -> None:
        self._model = None
