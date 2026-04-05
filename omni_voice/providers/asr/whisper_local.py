"""
ASR Provider: Local Whisper (OpenAI open-source, runs on your machine)
=======================================================================
No API key. No cost. No data leaving your server.
Requires: pip install openai-whisper

Supports: tiny / base / small / medium / large
Recommended for low-end hardware: base  (~140 MB, ~1s on CPU)
Recommended for quality:          small (~460 MB, ~3s on CPU)
"""
from __future__ import annotations

import asyncio
import io
import logging
import wave
from typing import AsyncIterator

from omni_voice.providers.base import ASRProvider

logger = logging.getLogger(__name__)


class WhisperLocalASR(ASRProvider):
    """
    Offline speech-to-text via OpenAI's open-source Whisper model.
    Model is downloaded once and cached in ~/.cache/whisper/.

    Parameters
    ----------
    model_size:
        "tiny" | "base" | "small" | "medium" | "large"
    device:
        "cpu" | "cuda" — auto-detected if not specified.
    language:
        ISO language code, e.g. "en". None = auto-detect.
    """

    def __init__(
        self,
        model_size: str = "base",
        device: str | None = None,
        language: str | None = "en",
    ) -> None:
        self.model_size = model_size
        self.device     = device
        self.language   = language
        self._model     = None   # lazy-loaded on first use

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            import whisper
        except ImportError:
            raise RuntimeError(
                "openai-whisper not installed.\n"
                "  pip install openai-whisper\n"
                "  brew install ffmpeg   # also needed"
            )
        import torch
        device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        logger.info("Loading Whisper %s on %s …", self.model_size, device)
        self._model = whisper.load_model(self.model_size, device=device)
        logger.info("Whisper ready.")
        return self._model

    async def transcribe(self, audio_bytes: bytes, content_type: str = "audio/wav") -> str:
        """Transcribe audio bytes. Runs whisper in a thread to avoid blocking."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._transcribe_sync, audio_bytes, content_type)

    def _transcribe_sync(self, audio_bytes: bytes, content_type: str) -> str:
        import tempfile, os, subprocess
        model = self._load_model()

        # Write to a temp file — whisper needs a file path or numpy array
        suffix = ".ogg" if "ogg" in content_type else ".mp3" if "mp3" in content_type else ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            opts = {"language": self.language} if self.language else {}
            result = model.transcribe(tmp_path, **opts)
            return result["text"].strip()
        except Exception as e:
            logger.error("Whisper transcription failed: %s", e)
            return ""
        finally:
            os.unlink(tmp_path)

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[str]:
        """Accumulate stream then transcribe in one shot."""
        chunks = []
        async for chunk in audio_stream:
            chunks.append(chunk)
        audio_bytes = b"".join(chunks)
        if audio_bytes:
            text = await self.transcribe(audio_bytes)
            if text:
                yield text

    async def close(self) -> None:
        pass
