"""
Omni Voice – Central configuration via pydantic-settings.
All values are read from environment variables or a .env file.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    openai = "openai"
    anthropic = "anthropic"
    ollama = "ollama"


class ASRProvider(str, Enum):
    deepgram = "deepgram"
    whisper = "whisper"      # local Whisper via openai-whisper package


class TTSProvider(str, Enum):
    openai = "openai"
    elevenlabs = "elevenlabs"
    kokoro = "kokoro"
    edge = "edge"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Server ───────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # ── Provider selection ───────────────────────────────────
    llm_provider: LLMProvider = LLMProvider.openai
    asr_provider: ASRProvider = ASRProvider.deepgram
    tts_provider: TTSProvider = TTSProvider.openai

    # ── LLM credentials ──────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""   # e.g. https://openrouter.ai/api/v1 for OpenRouter
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-haiku-20240307"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "mistral"

    # ── Twilio ───────────────────────────────────────────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    public_base_url: str = "https://omnivoice.dev"

    # System prompt injected into every LLM session
    system_prompt: str = (
        "You are a helpful, friendly voice assistant. "
        "Keep responses concise and conversational — ideally 1-3 sentences. "
        "You are speaking aloud, so avoid markdown, lists, or special formatting."
    )

    # ── ASR credentials ──────────────────────────────────────
    deepgram_api_key: str = ""
    whisper_model_size: str = "base"

    # ── TTS credentials ──────────────────────────────────────
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    openai_tts_voice: str = "alloy"  # alloy | echo | fable | onyx | nova | shimmer
    openai_tts_model: str = "tts-1"
    openai_tts_api_key: str = ""    # separate key for TTS; falls back to openai_api_key

    # ── TAB (Temporal Alignment Buffer) ─────────────────────
    tab_speech_rate_tokens_per_sec: float = 8.5
    tab_high_water_sec: float = 0.15
    tab_low_water_sec: float = 0.05

    # ── ATTS (Adaptive Turn-Taking Scheduler) ────────────────
    atts_epoch_ms: int = 50
    atts_margin_ms: int = 100
    atts_leaky_bucket_rate: float = 1.0
    atts_leaky_bucket_burst: int = 5

    # ── AQAL (Adaptive Audio Channel Quality Layer) ──────────
    aqal_bucket_capacity: int = 30
    aqal_base_leak_rate: float = 0.05  # tokens/ms

    # ── Observability ────────────────────────────────────────
    prometheus_port: int = 9090
    enable_otel: bool = False
    otel_endpoint: str = "http://localhost:4317"

    # ── Waitlist ─────────────────────────────────────────────
    notify_email: str = "fortheloveofai082@gmail.com"
    gmail_app_password: str = ""


# Module-level singleton
settings = Settings()
