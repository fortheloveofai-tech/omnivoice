# OmniVoice

**Give any LLM a voice. On WhatsApp, in your terminal, or anywhere you build.**

OmniVoice is an open-source voice layer for AI. Point it at Claude, GPT-4o, or a fully local Ollama model — it handles the mic, the transcription, the thinking, and the spoken reply. No proprietary stack. No lock-in. Free path available.

→ [omnivoice.dev](https://omnivoice.dev) · [GitHub](https://github.com/fortheloveofai-tech/omnivoice)

---

## How it works

```
Your voice  →  Whisper ASR  →  Your LLM  →  EdgeTTS  →  Spoken reply
```

Two ways to use it:

- **Terminal (CLI)** — Push to talk from any terminal. Press Enter to speak, press Enter to send. AI replies out loud.
- **WhatsApp** — Send a voice note to your Twilio number. Get a voice note back. Works on any phone with no app download.

---

## Requirements

- Python 3.10 or higher
- `ffmpeg` — required for WhatsApp audio conversion

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg
```

---

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/fortheloveofai-tech/omnivoice.git
cd omnivoice

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Configuration

Run the interactive setup wizard — it asks you questions and writes your `.env` file:

```bash
python setup_env.py
```

Or copy `.env.example` to `.env` and fill in the values manually:

```bash
cp .env.example .env
```

### Minimum config for terminal chat (zero-cost path)

```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3

ASR_PROVIDER=whisper
WHISPER_MODEL_SIZE=base

TTS_PROVIDER=edge
EDGE_TTS_VOICE=en-US-AriaNeural
```

Make sure Ollama is running first: `ollama serve` and `ollama pull llama3`

### Minimum config for WhatsApp

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

ASR_PROVIDER=whisper
WHISPER_MODEL_SIZE=base

TTS_PROVIDER=edge

TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886

TUNNEL_PROVIDER=cloudflared
```

---

## Usage

### Terminal voice chat

```bash
python -m omni_voice.voice_cli
```

- Press `Enter` to start recording
- Press `Enter` again to stop and send
- Press `Ctrl+C` to quit

### WhatsApp server

```bash
python start.py
```

This will:
1. Start a cloudflared (or ngrok) tunnel and get a public HTTPS URL
2. Update your Twilio webhook automatically
3. Launch the FastAPI server

Then send a voice note to your Twilio WhatsApp number and you'll get a voice note back.

**Requires:** cloudflared or ngrok installed

```bash
# Install cloudflared
brew install cloudflared

# Or ngrok
brew install ngrok/ngrok/ngrok
```

---

## Providers

### LLM

| Provider | `LLM_PROVIDER` value | Key needed | Notes |
|----------|---------------------|------------|-------|
| Claude (Anthropic) | `anthropic` | `ANTHROPIC_API_KEY` | console.anthropic.com |
| GPT-4o (OpenAI) | `openai` | `OPENAI_API_KEY` | platform.openai.com |
| Ollama (local) | `ollama` | None | Free, runs on your machine |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | openrouter.ai — access 100+ models |

### ASR (Speech → Text)

| Provider | `ASR_PROVIDER` value | Key needed | Notes |
|----------|---------------------|------------|-------|
| Whisper | `whisper` | None | Runs locally. Set `WHISPER_MODEL_SIZE`: `tiny` / `base` / `small` |
| Deepgram | `deepgram` | `DEEPGRAM_API_KEY` | Cloud, fast. Free tier at console.deepgram.com |

### TTS (Text → Speech)

| Provider | `TTS_PROVIDER` value | Key needed | Notes |
|----------|---------------------|------------|-------|
| EdgeTTS | `edge` | None | Free Microsoft neural voices |
| ElevenLabs | `elevenlabs` | `ELEVENLABS_API_KEY` | Best quality, paid |
| OpenAI TTS | `openai` | `OPENAI_API_KEY` | Paid |

---

## Free path (zero API keys)

You can run OmniVoice with no paid API keys at all:

```env
LLM_PROVIDER=ollama        # local model via Ollama
ASR_PROVIDER=whisper       # local Whisper
TTS_PROVIDER=edge          # free Microsoft EdgeTTS
```

Prerequisites: [Ollama](https://ollama.com) installed and a model pulled (`ollama pull llama3`).

---

## Whisper model sizes

| Size | RAM needed | Speed | Best for |
|------|-----------|-------|----------|
| `tiny` | ~400 MB | Fastest | Clear speech, low-resource machines |
| `base` | ~600 MB | Fast | General use — recommended default |
| `small` | ~1.2 GB | Moderate | Accents, background noise |

The model downloads automatically on first run and is cached locally.

---

## Project structure

```
omnivoice/
├── omni_voice/
│   ├── voice_cli.py              # Terminal push-to-talk client
│   ├── asr/
│   │   ├── whisper_local.py      # Local Whisper (no API key)
│   │   └── deepgram.py           # Deepgram cloud ASR
│   ├── tts/
│   │   ├── edge_tts.py           # Microsoft EdgeTTS (free)
│   │   ├── elevenlabs.py         # ElevenLabs
│   │   └── openai_tts.py         # OpenAI TTS
│   └── llm/
│       ├── anthropic.py          # Claude
│       ├── openai.py             # GPT-4o
│       ├── ollama.py             # Local Ollama
│       └── openrouter.py         # OpenRouter
├── integrations/
│   └── twilio/
│       └── whatsapp_webhook.py   # FastAPI WhatsApp server
├── setup_env.py                  # Interactive .env configurator
├── start.py                      # One-command launcher
├── requirements.txt
└── .env.example                  # Template — copy to .env
```

---

## Deploying WhatsApp to production

`python start.py` uses a local tunnel that stops when your machine sleeps. For a permanent always-on endpoint:

1. Deploy to [Railway](https://railway.app) — connect this GitHub repo, set env vars in the dashboard
2. Add a `Procfile` at the root:
   ```
   web: uvicorn integrations.twilio.whatsapp_webhook:app --host 0.0.0.0 --port $PORT
   ```
3. Switch ASR to Deepgram (Whisper needs ~600MB+ RAM; Railway's free tier is 512MB)
4. Set all your env vars in the Railway dashboard
5. Update the Twilio webhook URL to your Railway domain

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'omni_voice'`**
Run as a module from the project root, not as a script:
```bash
python -m omni_voice.voice_cli   # ✓ correct
python omni_voice/voice_cli.py   # ✗ wrong
```

**`ffmpeg not found`**
Required for WhatsApp audio conversion: `brew install ffmpeg`

**`sounddevice` errors on first run**
You may need to grant microphone permission in System Preferences → Privacy → Microphone.

**Whisper takes a long time on first run**
The model is downloading and caching locally. Subsequent runs are instant.

**Ollama connection refused**
Make sure Ollama is running: `ollama serve`

---

## Contributing

PRs welcome. The easiest ways to extend OmniVoice:

- **New LLM** — add a file under `omni_voice/llm/` with an async `generate(prompt, history) -> str` function
- **New ASR** — add under `omni_voice/asr/` with an async `transcribe(audio_bytes) -> str` function
- **New TTS** — add under `omni_voice/tts/` with an async `synthesize(text) -> bytes` function
- **New channel** — Telegram, Signal, SMS — follow the pattern in `integrations/twilio/`

---

## License

MIT — use it, fork it, build on it.
