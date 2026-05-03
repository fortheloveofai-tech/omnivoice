"""
OmniVoice — Terminal Voice CLI
Push-to-talk voice chat with any LLM from your terminal.

Usage:
    python -m omni_voice.voice_cli

Controls:
    Enter  → start / stop recording
    Ctrl+C → quit
"""
import asyncio
import io
import os
import sys
import threading

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
SAMPLE_RATE  = 16000
CHANNELS     = 1
DTYPE        = "int16"
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "whisper").lower()
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "edge").lower()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
PURPLE = "\033[95m"
RED    = "\033[91m"
DIM    = "\033[2m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


# ── Provider loaders ──────────────────────────────────────────────────────────
def _load_asr():
    if ASR_PROVIDER == "deepgram":
        from omni_voice.asr import deepgram as asr
    else:
        from omni_voice.asr import whisper_local as asr
    return asr


def _load_tts():
    if TTS_PROVIDER == "elevenlabs":
        from omni_voice.tts import elevenlabs as tts
    elif TTS_PROVIDER == "openai":
        from omni_voice.tts import openai_tts as tts
    else:
        from omni_voice.tts import edge_tts as tts
    return tts


def _load_llm():
    if LLM_PROVIDER == "openai":
        from omni_voice.llm import openai as llm
    elif LLM_PROVIDER == "ollama":
        from omni_voice.llm import ollama as llm
    elif LLM_PROVIDER == "openrouter":
        from omni_voice.llm import openrouter as llm
    else:
        from omni_voice.llm import anthropic as llm
    return llm


def _llm_label(llm_module) -> str:
    name = getattr(llm_module, "__name__", "")
    label = name.split(".")[-1].replace("llm", "").replace("_", " ").strip().title()
    return label or "AI"


# ── Audio helpers ─────────────────────────────────────────────────────────────
def _record_blocking() -> bytes:
    """Record until the user presses Enter again. Returns raw PCM bytes."""
    frames = []
    stop_event = threading.Event()

    def callback(indata, frame_count, time_info, status):
        frames.append(bytes(indata))  # bytes() works across all Python versions

    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        callback=callback,
    )

    with stream:
        input()  # wait for Enter

    return b"".join(frames)


def _pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap raw PCM int16 in a WAV container."""
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


async def _play_audio(mp3_bytes: bytes):
    """Play MP3 bytes through speakers using pydub + sounddevice."""
    from pydub import AudioSegment
    seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
    seg = seg.set_frame_rate(SAMPLE_RATE).set_channels(1).set_sample_width(2)
    arr = np.frombuffer(seg.raw_data, dtype=np.int16)
    sd.play(arr, samplerate=SAMPLE_RATE)
    sd.wait()


# ── Main loop ─────────────────────────────────────────────────────────────────
async def main():
    asr = _load_asr()
    tts = _load_tts()
    llm = _load_llm()
    label = _llm_label(llm)

    banner = f"""
{GREEN}╔══════════════════════════════════════════╗
║   🎙  OmniVoice — Voice CLI             ║
║                                          ║
║   Enter  → start / stop recording       ║
║   Ctrl+C → quit                         ║
╚══════════════════════════════════════════╝{RESET}"""

    print(banner)
    print(f"{BLUE}  Loading providers… ready.{RESET}")
    print(f"{DIM}  ⏎  Press Enter to speak…{RESET}\n")

    history = []

    while True:
        try:
            input()  # wait for first Enter
        except (EOFError, KeyboardInterrupt):
            print(f"\n{DIM}  Goodbye.{RESET}")
            break

        print(f"{RED}  🔴 Recording… Press Enter to stop{RESET}", flush=True)
        pcm = _record_blocking()

        print(f"{DIM}  ⏳  Transcribing…{RESET}", flush=True)
        wav = _pcm_to_wav(pcm)
        transcript = await asr.transcribe(wav)

        if not transcript:
            print(f"{DIM}  (no speech detected){RESET}\n")
            continue

        print(f"\n{YELLOW}  🗣  You:{RESET} {transcript}")

        print(f"{BLUE}  🧠  {label}:{RESET} ", end="", flush=True)
        reply = await llm.generate(transcript, history=history)
        print(reply)

        # Update history
        history.append({"role": "user", "content": transcript})
        history.append({"role": "assistant", "content": reply})
        # Keep last 10 turns
        if len(history) > 20:
            history = history[-20:]

        print(f"{GREEN}  🔊  Speaking…{RESET}", flush=True)
        audio = await tts.synthesize(reply)
        await _play_audio(audio)

        print(f"\n{DIM}  ⏎  Press Enter to speak…{RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())
