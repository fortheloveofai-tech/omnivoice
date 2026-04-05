"""
LLM Provider: Anthropic Claude (claude-3-haiku, claude-sonnet, etc.)
====================================================================
Uses Anthropic's streaming messages API.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from omni_voice.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class AnthropicLLM(LLMProvider):
    """
    Streaming LLM via Anthropic Messages API.

    Parameters
    ----------
    api_key:
        Anthropic API key.
    model:
        Model name, e.g. "claude-3-haiku-20240307".
    max_tokens:
        Maximum response tokens (default 300 for voice).
    temperature:
        Sampling temperature (default 0.7).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-haiku-20240307",
        max_tokens: int = 300,
        temperature: float = 0.7,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=self.api_key)
            except ImportError:
                logger.error("anthropic package not installed: pip install anthropic")
                raise
        return self._client

    async def stream_tokens(
        self,
        prompt: str,
        history: list[dict],
        system_prompt: str = "",
    ) -> AsyncIterator[str]:
        client = self._get_client()

        # Anthropic uses "user" / "assistant" roles directly
        messages = list(history)
        messages.append({"role": "user", "content": prompt})

        try:
            async with client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt or "You are a helpful voice assistant.",
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as exc:
            logger.error("Anthropic streaming error: %s", exc)
            yield f"[Error: {exc}]"

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
