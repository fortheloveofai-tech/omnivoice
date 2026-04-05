# OmniVoice

**Give any AI a voice. On WhatsApp. In minutes.**

Send a voice note on WhatsApp → OmniVoice transcribes it → passes it to your AI → replies with audio. No app download for your users. No vendor lock-in. Runs on your machine or in the cloud.

---

## What makes it different

- **WhatsApp as the interface** — 2B+ users already have it. Zero onboarding friction.
- **Any LLM** — Claude, GPT-4o, or a local Ollama model. Swap with one config line.
- **Self-hosted** — your keys, your data, your server.
- **Zero-key path** — run entirely free with Ollama + Whisper + EdgeTTS. No credit card.

---

## Quick start

### Path A · Terminal voice chat (5 minutes, no Twilio needed)

Talk to your AI from your terminal — mic input, speaker output.

**1. Install**
```bash
git clone https://github.com/your-org/omni-voice.git
cd omni-voice
pip install -r requirements.txt
```

**2. Configure**
```bash
python setup_env.py
```

Pick your providers when prompted. The free path needs nothing:

| Goal | LLM | ASR | TTS |
|---|---|---|---|
| Zero keys (free) | `ollama` | `whisper` | `edge` |
| Best quality | `anthropic` | `whisper` | `edge` |

**3. Talk**
```bash
python -m omni_voice.voice_cli
```

Press **Enter** to record. Speak. Press **Enter** again to send. Hear the reply.

---

### Path B · WhatsApp voice bot (20 minutes)

**What you need first**
- A free [Twilio account](https://twilio.com) (trial credit is enough for weeks of testing)
- `cloudflared` or `ngrok` for the public tunnel:
  ```bash
  brew install cloudflare/cloudflare/cloudflared   # recommended
  # or: brew install ngrok/ngrok/ngrok
  ```
- `ffmpeg` for audio conversion:
  ```bash
  brew install ffmpeg
  ```

**1. Install**
```bash
git clone https://github.com/your-org/omni-voice.git
cd omni-voice
pip install -r requirements.txt
```

**2. Configure**
```bash
python setup_env.py
```

You will need your Twilio Account SID, Auth Token, and WhatsApp sandbox number — all on your [Twilio Console](https://console.twilio.com) dashboard.

**3. Start everything**
```bash
python start.py
```

This opens a public HTTPS tunnel, updates your Twilio webhook automatically, and starts the server — all in one command.

**4. Connect WhatsApp**

In the Twilio Console → Messaging → Try it out → Send a WhatsApp message, find your **join keyword** (e.g. `join bright-fox`).

On your phone, send that keyword to the Twilio WhatsApp number.

**5. Send a voice note**

Record a voice note and send it. You will get an audio reply in seconds.

---

## Zero-key setup (completely free)

No API keys, no credit card. Everything runs locally.

```bash
# 1. Install Ollama from https://ollama.com, then pull a model
ollama pull llama3.2

# 2. Configure — pick ollama / whisper / edge when prompted
python setup_env.py

# 3. Talk
python -m omni_voice.voice_cli
```

---

## Provider options

### LLM

```bash
# Claude (Anthropic) — get key at console.anthropic.com
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# GPT-4o (OpenAI) — get key at platform.openai.com
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

# Ollama — local, free, no key needed
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.2
OLLAMA_BASE_URL=http://localhost:11434

# Any OpenAI-compatible API (OpenRouter, Groq, Together, etc.)
LLM_PROVIDER=openai
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_MODEL=anthropic/claude-3.5-sonnet
```

### ASR (Speech-to-Text)

```bash
# Whisper — local, free, recommended
ASR_PROVIDER=whisper
WHISPER_MODEL_SIZE=base   # tiny | base | small | medium | large

# Deepgram — cloud, fast, free tier at console.deepgram.com
ASR_PROVIDER=deepgram
DEEPGRAM_API_KEY=...
```

**Whisper model sizes**

| Model | Download | Speed | Good for |
|---|---|---|---|
| `tiny` | 75 MB | Very fast | Quick testing |
| `base` | 145 MB | Fast | **Start here** |
| `small` | 465 MB | Medium | Daily use |
| `medium` | 1.5 GB | Slower | Better accuracy |
| `large` | 3 GB | Slow | Accents, noisy audio |

Models download automatically on first use to `~/.cache/whisper/`.

### TTS (Text-to-Speech)

```bash
# EdgeTTS — free, Microsoft neural voices, recommended
TTS_PROVIDER=edge
EDGE_TTS_VOICE=en-US-JennyNeural

# ElevenLabs — paid, highest quality
TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM

# OpenAI TTS — paid
TTS_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_TTS_VOICE=alloy
```

---

## How it works

```
WhatsApp voice note
       |
       v
 Twilio webhook
       |
       v
  Download OGG --> ASR --> transcript
                            |
                            v
                           LLM --> reply text
                            |
                            v
                           TTS --> OGG audio --> WhatsApp reply
```

For the terminal CLI, Twilio is replaced by your mic and speakers.

---

## Deploying to the cloud

Once local testing works, deploy for 24/7 uptime without your laptop.

**Railway (easiest)**
1. Push to GitHub
2. Connect at [railway.app](https://railway.app) -> New Project -> Deploy from GitHub
3. Add your `.env` values in the Railway Variables tab
4. Add a `Procfile`:
   ```
   web: uvicorn omni_voice.main:app --host 0.0.0.0 --port $PORT
   ```
5. Use your Railway URL as the Twilio webhook — no tunnel needed

**Render** and **Fly.io** work the same way. See `docker/` for a Dockerfile if you need system packages like ffmpeg pre-installed.

> **Note:** Ollama runs locally, so it will not be available on cloud hosts. Switch to `anthropic` or `openai` for cloud deployments.

---

## Cost estimate

| Component | Free option | Paid option |
|---|---|---|
| LLM | Ollama (local) | ~$0.003/turn (Claude or GPT-4o) |
| ASR | Whisper (local) | Deepgram free tier |
| TTS | EdgeTTS (Microsoft) | ElevenLabs / OpenAI TTS |
| Twilio | Trial credit ($15) | ~$0.005/message |

A typical 10-message voice conversation costs under $0.05 with paid APIs.

---

## Requirements

- Python 3.9+
- `ffmpeg` for WhatsApp audio: `brew install ffmpeg`
- For Ollama: [ollama.com](https://ollama.com)
- For WhatsApp: a Twilio account + cloudflared or ngrok

---

## Project structure

```
omni-voice/
├── omni_voice/
│   ├── providers/      # Pluggable ASR / LLM / TTS adapters
│   │   ├── asr/        # deepgram.py, whisper_local.py
│   │   ├── llm/        # anthropic.py, openai.py (+ OllamaLLM)
│   │   └── tts/        # openai.py (+ ElevenLabs), edge.py
│   └── voice_cli.py    # Terminal voice chat
├── integrations/
│   └── twilio/
│       └── whatsapp_webhook.py   # WhatsApp webhook handler
├── demo/
│   └── landing.html    # Landing page
├── setup_env.py        # Interactive .env configurator
├── start.py            # One-command launcher (tunnel + webhook + server)
└── requirements.txt
```

---

## Development

```bash
# Run tests
pytest tests/

# Run server with auto-reload
uvicorn omni_voice.main:app --reload

# API docs (when server is running)
open http://localhost:8000/docs
```

---

## License

MIT
