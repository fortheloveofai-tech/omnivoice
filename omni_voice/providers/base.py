"""
Abstract base interfaces for all pluggable providers.

Every provider (ASR, LLM, TTS) implements exactly one ABC.
Swapping providers is a one-line config change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator


# ── ASR ───────────────────────────────────────────────────────────────────────

class ASRProvider(ABC):
    """
    Streaming Automatic Speech Recognition provider.

    Receives raw PCM audio bytes and emits text transcript fragments
    as they are recognised (partial transcripts).
    """

    @abstractmethod
    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[str]:
        """
        Yield partial transcript strings as audio arrives.

        Parameters
        ----------
        audio_stream:
            Async iterator of PCM audio chunks (16-bit, 16 kHz, mono).

        Yields
        ------
        str
            Partial or final transcript fragment.
        """
        ...  # pragma: no cover

    @abstractmethod
    async def close(self) -> None:
        """Release any open connections or resources."""
        ...  # pragma: no cover


# ── LLM ───────────────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """
    Streaming Large Language Model provider.

    Receives a text prompt and emits response tokens one by one.
    """

    @abstractmethod
    async def stream_tokens(
        self,
        prompt: str,
        history: list[dict],
        system_prompt: str = "",
    ) -> AsyncIterator[str]:
        """
        Stream LLM response tokens for *prompt*.

        Parameters
        ----------
        prompt:
            The user's latest utterance (plain text).
        history:
            Prior conversation turns as a list of
            ``{"role": "user"|"assistant", "content": str}`` dicts.
        system_prompt:
            System/persona instructions.

        Yields
        ------
        str
            Individual text tokens as they are generated.
        """
        ...  # pragma: no cover

    @abstractmethod
    async def close(self) -> None:
        """Release any open connections or resources."""
        ...  # pragma: no cover


# ── TTS ───────────────────────────────────────────────────────────────────────

class TTSProvider(ABC):
    """
    Streaming Text-to-Speech provider.

    Receives text tokens and emits synthesised PCM audio chunks.
    """

    @abstractmethod
    async def synthesize_stream(
        self, token_stream: AsyncIterator[str]
    ) -> AsyncIterator[bytes]:
        """
        Stream synthesised audio for *token_stream*.

        Parameters
        ----------
        token_stream:
            Async iterator of text tokens (from LLM via TAB).

        Yields
        ------
        bytes
            PCM audio frames (16-bit, provider-specific sample rate).
        """
        ...  # pragma: no cover

    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """
        Synthesise a complete text string and return raw PCM bytes.
        Used for short responses where streaming isn't required.
        """
        ...  # pragma: no cover

    @abstractmethod
    async def close(self) -> None:
        """Release any open connections or resources."""
        ...  # pragma: no cover
