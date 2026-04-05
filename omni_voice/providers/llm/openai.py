"""
LLM Provider: OpenAI (GPT-4o, GPT-4o-mini, etc.)
==================================================
Uses OpenAI's streaming chat completions API.
Any OpenAI-compatible endpoint (vLLM, LM Studio, Groq, etc.)
can be used by pointing OPENAI_BASE_URL to the alternative endpoint.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from omni_voice.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class OpenAILLM(LLMProvider):
    """
    Streaming LLM via OpenAI Chat Completions.

    Parameters
    ----------
    api_key:
        OpenAI API key (or compatible provider key).
    model:
        Model name, e.g. "gpt-4o-mini".
    base_url:
        Optional alternative base URL for OpenAI-compatible APIs
        (vLLM, Groq, LM Studio, Ollama OpenAI-compat, etc.)
    temperature:
        Sampling temperature (default 0.7).
    max_tokens:
        Maximum response tokens (default 300 — short for voice).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 300,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                kwargs = {"api_key": self.api_key}
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                self._client = AsyncOpenAI(**kwargs)
            except ImportError:
                logger.error("openai package not installed: pip install openai")
                raise
        return self._client

    async def stream_tokens(
        self,
        prompt: str,
        history: list[dict],
        system_prompt: str = "",
    ) -> AsyncIterator[str]:
        client = self._get_client()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        try:
            stream = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except Exception as exc:
            logger.error("OpenAI streaming error: %s", exc)
            yield f"[Error: {exc}]"

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


class OllamaLLM(LLMProvider):
    """
    Streaming LLM via Ollama's OpenAI-compatible API.
    Ollama runs models locally (Mistral, LLaMA, Gemma, etc.)
    """

    def __init__(
        self,
        model: str = "mistral",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.7,
        max_tokens: int = 300,
    ) -> None:
        self._inner = OpenAILLM(
            api_key="ollama",   # Ollama doesn't check the key
            model=model,
            base_url=f"{base_url}/v1",
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def stream_tokens(
        self, prompt: str, history: list[dict], system_prompt: str = ""
    ) -> AsyncIterator[str]:
        async for token in self._inner.stream_tokens(prompt, history, system_prompt):
            yield token

    async def close(self) -> None:
        await self._inner.close()
