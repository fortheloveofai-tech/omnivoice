"""
Provider factory — resolves config → concrete provider instances.
"""
from __future__ import annotations

from omni_voice.config import ASRProvider, LLMProvider, TTSProvider, settings
from omni_voice.providers.base import (
    ASRProvider as ASRBase,
    LLMProvider as LLMBase,
    TTSProvider as TTSBase,
)


def build_asr() -> ASRBase:
    if settings.asr_provider == ASRProvider.deepgram:
        from omni_voice.providers.asr.deepgram import DeepgramASR
        return DeepgramASR(api_key=settings.deepgram_api_key)
    elif settings.asr_provider == ASRProvider.whisper:
        from omni_voice.providers.asr.whisper_local import WhisperLocalASR
        return WhisperLocalASR(model_size=settings.whisper_model_size)
    else:
        raise ValueError(f"Unknown ASR provider: {settings.asr_provider}")


def build_llm() -> LLMBase:
    if settings.llm_provider == LLMProvider.openai:
        from omni_voice.providers.llm.openai import OpenAILLM
        return OpenAILLM(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url or None,
        )
    elif settings.llm_provider == LLMProvider.anthropic:
        from omni_voice.providers.llm.anthropic import AnthropicLLM
        return AnthropicLLM(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
        )
    elif settings.llm_provider == LLMProvider.ollama:
        from omni_voice.providers.llm.openai import OllamaLLM
        return OllamaLLM(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")


def build_tts() -> TTSBase:
    if settings.tts_provider == TTSProvider.openai:
        from omni_voice.providers.tts.openai import OpenAITTS
        # Use dedicated TTS key if set, otherwise fall back to openai_api_key
        tts_key = settings.openai_tts_api_key or settings.openai_api_key
        return OpenAITTS(
            api_key=tts_key,
            voice=settings.openai_tts_voice,
            model=settings.openai_tts_model,
        )
    elif settings.tts_provider == TTSProvider.elevenlabs:
        from omni_voice.providers.tts.openai import ElevenLabsTTS
        return ElevenLabsTTS(
            api_key=settings.elevenlabs_api_key,
            voice_id=settings.elevenlabs_voice_id,
        )
    elif settings.tts_provider == TTSProvider.edge:
        from omni_voice.providers.tts.edge import EdgeTTS
        return EdgeTTS(
            voice=getattr(settings, "edge_tts_voice", "en-US-JennyNeural"),
            rate=getattr(settings, "edge_tts_rate", "+0%"),
        )
    else:
        raise ValueError(f"Unknown TTS provider: {settings.tts_provider}")
