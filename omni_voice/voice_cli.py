#!/usr/bin/env python3
"""
OmniVoice CLI — Talk to Claude with your voice, hear it talk back.
===================================================================

Controls
--------
  Enter        → start recording
  Enter again  → stop recording and send
  Ctrl+C       → quit

Usage
-----
  # From the omni-voice project root:
  python -m omni_voice.voice_cli

  # Or directly:
  python omni_voice/voice_cli.py

Requirements
------------
  pip install sounddevice numpy edge-tts
  # ffmpeg must be installed (brew install ffmpeg on Mac)
"""
from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
import tempfile
import wave
import logging

log = logging.getLogger(__name__)

# ── Audio constants ────────────────────────────────────────────────────────────
SAMPLE_RATE = 16_000   # Hz — Deepgram nova-2 works great at 16kHz
CHANNELS    = 1        # mono
DTYPE       = "int16"  # 16-bit PCM


# ── Recording ─────────────────────────────────────────────────────────────────

def _record_blocking(stop_event: "threading.Event") -> bytes:
    """
    Record from the default microphone until stop_event is set.
    Returns raw 16-bit PCM bytes at SAMPLE_RATE.
    """
    import threading
    import numpy as np
    try:
        import sounddevice as sd
    except ImportError:
        print("\n❌  sounddevice not installed — run: pip install sounddevice")
        sys.exit(1)

    frames: list[bytes] = []

    def callback(indata, frame_count, time_info, status):
        frames.append(bytes(indata))

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        callback=callback,
    ):
        stop_event.wait()   # block until main thread signals stop

    return b"".join(frames)


def _pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap raw PCM-16 in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)          # 16-bit → 2 bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


# ── Playback ──────────────────────────────────────────────────────────────────

async def _play_mp3(mp3_bytes: bytes) -> None:
    """Play MP3 bytes using the system audio player (no extra libs needed)."""
    suffix = ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(mp3_bytes)
        tmpfile = f.name

    try:
        loop = asyncio.get_event_loop()
        if sys.platform == "darwin":
            await loop.run_in_executor(
                None, lambda: subprocess.run(["afplay", tmpfile], check=True)
            )
        elif sys.platform.startswith("linux"):
            # Try mpg123 first, then ffplay
            player = "mpg123" if _cmd_exists("mpg123") else "ffplay"
            args = [player, "-q", tmpfile] if player == "mpg123" else [
                player, "-nodisp", "-autoexit", "-loglevel", "quiet", tmpfile
            ]
            await loop.run_in_executor(
                None, lambda: subprocess.run(args, check=True)
            )
        else:
            # Windows
            os.startfile(tmpfile)
            await asyncio.sleep(3)   # give it time to play
    finally:
        try:
            os.unlink(tmpfile)
        except OSError:
            pass


def _cmd_exists(cmd: str) -> bool:
    return subprocess.run(
        ["which", cmd], capture_output=True
    ).returncode == 0


# ── Main loop ─────────────────────────────────────────────────────────────────

BANNER = """
╔══════════════════════════════════════════╗
║   🎙  OmniVoice — Voice CLI             ║
║                                          ║
║   Enter  → start / stop recording       ║
║   Ctrl+C → quit                         ║
╚══════════════════════════════════════════╝
"""


async def main() -> None:
    import threading

    # Lazy-import providers so startup is fast even if some are missing
    from omni_voice.providers import build_asr, build_llm, build_tts

    # Silence httpx info logs for cleaner output
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.basicConfig(level=logging.WARNING)

    print(BANNER)
    print("  Loading providers…", end="", flush=True)
    asr = build_asr()
    llm = build_llm()
    tts = build_tts()
    print(" ready.\n")

    # Derive a friendly label from the provider class name
    llm_label = type(llm).__name__.replace("LLM", "").replace("Client", "").strip() or "AI"

    history: list[dict] = []
    loop = asyncio.get_event_loop()

    while True:
        try:
            # ── Wait for user to press Enter to start ─────────────────────
            await loop.run_in_executor(None, lambda: input("  ⏎  Press Enter to speak… "))

            # ── Start recording in background thread ──────────────────────
            stop_event = threading.Event()
            print("  🔴  Recording… Press Enter to send", flush=True)

            record_future = loop.run_in_executor(
                None, _record_blocking, stop_event
            )

            # Wait for second Enter press (non-blocking via executor)
            await loop.run_in_executor(None, input, "")
            stop_event.set()
            pcm_bytes = await record_future

            if len(pcm_bytes) < SAMPLE_RATE * 2 * 0.3:   # < 0.3 s of audio
                print("  ⚠️  Too short — try again\n")
                continue

            # ── ASR ───────────────────────────────────────────────────────
            print("  ⏳  Transcribing…", end="", flush=True)
            wav_bytes  = _pcm_to_wav(pcm_bytes)
            transcript = await asr.transcribe(wav_bytes, content_type="audio/wav")
            transcript = transcript.strip()

            if not transcript:
                print(" couldn't hear you — try again\n")
                continue

            print(f"\n\n  🗣  You: {transcript}\n")

            # ── LLM ───────────────────────────────────────────────────────
            sys.stdout.write(f"  🤖  {llm_label}: ")
            sys.stdout.flush()
            chunks: list[str] = []

            async for token in llm.stream_tokens(
                prompt=transcript,
                history=history,
                system_prompt=(
                    "You are a helpful, friendly voice assistant. "
                    "Keep replies concise and conversational — 1-3 sentences. "
                    "Never use markdown, bullet points, or special formatting. "
                    "Speak naturally as if in a conversation."
                ),
            ):
                chunks.append(token)
                sys.stdout.write(token)
                sys.stdout.flush()

            print("\n")
            reply = "".join(chunks)

            # Keep last 10 turns in context
            history.append({"role": "user",      "content": transcript})
            history.append({"role": "assistant",  "content": reply})
            if len(history) > 20:
                history = history[-20:]

            # ── TTS → play back ───────────────────────────────────────────
            print("  🔊  Speaking…", end="", flush=True)
            audio_bytes = await tts.synthesize(reply)
            print()
            await _play_mp3(audio_bytes)
            print()

        except KeyboardInterrupt:
            print("\n\n  👋  Bye!\n")
            break
        except Exception as exc:
            log.exception("Error in voice loop")
            print(f"\n  ❌  {exc} — try again\n")


if __name__ == "__main__":
    asyncio.run(main())
