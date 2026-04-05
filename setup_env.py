#!/usr/bin/env python3
"""
OmniVoice Setup — Interactive .env configurator
================================================
Run once (or any time you want to change settings):

    python setup_env.py

Reads existing .env values as defaults, asks questions,
then writes everything back to .env.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_FILE = Path(__file__).parent / ".env"

RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
DIM    = "\033[2m"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_env() -> dict[str, str]:
    """Parse existing .env into a dict (preserves comments as-is)."""
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _write_env(values: dict[str, str]) -> None:
    """Write key=value pairs to .env, preserving order."""
    lines = []
    for k, v in values.items():
        lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def ask(prompt: str, default: str = "", secret: bool = False, choices: list[str] | None = None) -> str:
    """Prompt the user for input with an optional default."""
    if choices:
        opts = " / ".join(
            f"{BOLD}{c}{RESET}" if c == default else c
            for c in choices
        )
        hint = f"  [{opts}]"
    elif default:
        masked = ("*" * min(len(default), 8) + "…") if secret and default else default
        hint = f"  {DIM}[{masked}]{RESET}"
    else:
        hint = ""

    sys.stdout.write(f"\n{CYAN}{prompt}{RESET}{hint}\n  → ")
    sys.stdout.flush()

    if secret:
        import getpass
        val = getpass.getpass(prompt="")
    else:
        val = input().strip()

    if not val:
        return default
    if choices and val not in choices:
        print(f"  {YELLOW}⚠  Invalid choice '{val}', using '{default}'{RESET}")
        return default
    return val


def section(title: str) -> None:
    print(f"\n{BOLD}{GREEN}── {title} {'─' * max(0, 42 - len(title))}{RESET}")


# ── Setup flow ─────────────────────────────────────────────────────────────────

def run() -> None:
    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║      OmniVoice — Environment Setup       ║{RESET}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════╝{RESET}")
    print(f"  {DIM}Updating: {ENV_FILE}{RESET}")
    print(f"  {DIM}Press Enter to keep the current value shown in brackets.{RESET}")

    env = _load_env()
    cfg: dict[str, str] = {}

    # ── LLM ───────────────────────────────────────────────────────────────────
    section("LLM Provider")
    print(f"  {DIM}anthropic → Claude (direct)  |  openai → GPT-4o (direct)  |  ollama → local, free, no key{RESET}\n")
    llm = ask(
        "Which LLM provider?",
        default=env.get("LLM_PROVIDER", "anthropic"),
        choices=["anthropic", "openai", "ollama"],
    )
    cfg["LLM_PROVIDER"] = llm

    if llm == "anthropic":
        cfg["ANTHROPIC_API_KEY"] = ask(
            "Anthropic API key  (console.anthropic.com → API Keys)",
            default=env.get("ANTHROPIC_API_KEY", ""),
            secret=True,
        )
        cfg["ANTHROPIC_MODEL"] = ask(
            "Model",
            default=env.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
        )

    elif llm == "openai":
        cfg["OPENAI_API_KEY"] = ask(
            "OpenAI API key  (platform.openai.com → API Keys)",
            default=env.get("OPENAI_API_KEY", ""),
            secret=True,
        )
        cfg["OPENAI_MODEL"] = ask(
            "Model",
            default=env.get("OPENAI_MODEL", "gpt-4o-mini"),
        )
        use_openrouter = ask(
            "Route via OpenRouter instead? (access 300+ models with one key)",
            default="yes" if env.get("OPENAI_BASE_URL", "").startswith("https://openrouter") else "no",
            choices=["yes", "no"],
        )
        if use_openrouter == "yes":
            cfg["OPENAI_BASE_URL"] = "https://openrouter.ai/api/v1"
            cfg["OPENAI_API_KEY"] = ask(
                "OpenRouter API key  (openrouter.ai → Keys)",
                default=env.get("OPENAI_API_KEY", ""),
                secret=True,
            )
            cfg["OPENAI_MODEL"] = ask(
                "Model  (e.g. anthropic/claude-3.5-sonnet, google/gemini-flash-1.5)",
                default=env.get("OPENAI_MODEL", "anthropic/claude-3.5-sonnet"),
            )
        else:
            cfg["OPENAI_BASE_URL"] = ""

    elif llm == "ollama":
        cfg["OLLAMA_BASE_URL"] = ask(
            "Ollama base URL",
            default=env.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
        cfg["OLLAMA_MODEL"] = ask(
            "Model (e.g. mistral, llama3, phi3)",
            default=env.get("OLLAMA_MODEL", "mistral"),
        )

    # ── ASR ───────────────────────────────────────────────────────────────────
    section("ASR (Speech-to-Text)")
    print(f"  {DIM}whisper → local, free, no key  |  deepgram → cloud, fast, free tier at console.deepgram.com{RESET}\n")
    asr = ask(
        "Which ASR provider?",
        default=env.get("ASR_PROVIDER", "whisper"),
        choices=["whisper", "deepgram"],
    )
    cfg["ASR_PROVIDER"] = asr

    if asr == "deepgram":
        cfg["DEEPGRAM_API_KEY"] = ask(
            "Deepgram API key  (free tier at console.deepgram.com)",
            default=env.get("DEEPGRAM_API_KEY", ""),
            secret=True,
        )
    elif asr == "whisper":
        cfg["WHISPER_MODEL_SIZE"] = ask(
            "Whisper model size (runs locally, no API key needed)",
            default=env.get("WHISPER_MODEL_SIZE", "base"),
            choices=["tiny", "base", "small", "medium", "large"],
        )

    # ── TTS ───────────────────────────────────────────────────────────────────
    section("TTS (Text-to-Speech)")
    print(f"  {DIM}edge → free, no key (Microsoft neural)  |  elevenlabs → paid, best quality  |  openai → paid{RESET}\n")
    tts = ask(
        "Which TTS provider?",
        default=env.get("TTS_PROVIDER", "edge"),
        choices=["edge", "elevenlabs", "openai"],
    )
    cfg["TTS_PROVIDER"] = tts

    if tts == "elevenlabs":
        cfg["ELEVENLABS_API_KEY"] = ask(
            "ElevenLabs API key  (elevenlabs.io)",
            default=env.get("ELEVENLABS_API_KEY", ""),
            secret=True,
        )
        cfg["ELEVENLABS_VOICE_ID"] = ask(
            "Voice ID  (default = Rachel)",
            default=env.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
        )

    elif tts == "openai":
        oai_tts_key = ask(
            "OpenAI TTS API key  (leave blank to reuse LLM key)",
            default=env.get("OPENAI_TTS_API_KEY", ""),
            secret=True,
        )
        cfg["OPENAI_TTS_API_KEY"] = oai_tts_key
        cfg["OPENAI_TTS_VOICE"] = ask(
            "Voice",
            default=env.get("OPENAI_TTS_VOICE", "alloy"),
            choices=["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
        )

    elif tts == "edge":
        cfg["EDGE_TTS_VOICE"] = ask(
            "Voice  (en-US-JennyNeural = female, en-US-GuyNeural = male)",
            default=env.get("EDGE_TTS_VOICE", "en-US-JennyNeural"),
        )

    # ── Twilio / WhatsApp (optional) ──────────────────────────────────────────
    section("Twilio / WhatsApp  (optional — skip if only using voice CLI)")
    setup_twilio = ask(
        "Set up Twilio WhatsApp integration?",
        default="yes" if env.get("TWILIO_ACCOUNT_SID", "") else "no",
        choices=["yes", "no"],
    )

    if setup_twilio == "yes":
        cfg["TWILIO_ACCOUNT_SID"] = ask(
            "Twilio Account SID",
            default=env.get("TWILIO_ACCOUNT_SID", ""),
            secret=True,
        )
        cfg["TWILIO_AUTH_TOKEN"] = ask(
            "Twilio Auth Token",
            default=env.get("TWILIO_AUTH_TOKEN", ""),
            secret=True,
        )
        cfg["TWILIO_PHONE_NUMBER"] = ask(
            "Twilio WhatsApp number  (e.g. whatsapp:+14155238886)",
            default=env.get("TWILIO_PHONE_NUMBER", ""),
        )
        cfg["PUBLIC_BASE_URL"] = ask(
            "Public base URL  (your Cloudflare Tunnel / ngrok URL)",
            default=env.get("PUBLIC_BASE_URL", "https://omnivoice.dev"),
        )
    else:
        # Keep existing values if already set
        for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "PUBLIC_BASE_URL"):
            if k in env:
                cfg[k] = env[k]

    # ── Waitlist / notifications (optional) ───────────────────────────────────
    section("Waitlist email notifications  (optional)")
    setup_email = ask(
        "Set up waitlist email notifications?",
        default="yes" if env.get("GMAIL_APP_PASSWORD", "") else "no",
        choices=["yes", "no"],
    )

    if setup_email == "yes":
        cfg["NOTIFY_EMAIL"] = ask(
            "Email address to receive waitlist notifications",
            default=env.get("NOTIFY_EMAIL", ""),
        )
        cfg["GMAIL_APP_PASSWORD"] = ask(
            "Gmail app password  (myaccount.google.com → Security → App passwords)",
            default=env.get("GMAIL_APP_PASSWORD", ""),
            secret=True,
        )
    else:
        for k in ("NOTIFY_EMAIL", "GMAIL_APP_PASSWORD"):
            if k in env:
                cfg[k] = env[k]

    # ── Write .env ─────────────────────────────────────────────────────────────
    _write_env(cfg)

    print(f"\n{BOLD}{GREEN}✅  .env updated successfully!{RESET}")
    print(f"   {DIM}{ENV_FILE}{RESET}\n")

    print(f"{BOLD}Next steps:{RESET}")
    print(f"  Voice CLI  →  {CYAN}python -m omni_voice.voice_cli{RESET}")
    print(f"  Web server →  {CYAN}uvicorn omni_voice.main:app --reload{RESET}\n")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n\n  Cancelled — .env not changed.\n")
        sys.exit(0)
