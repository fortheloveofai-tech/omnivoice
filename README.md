OmniVoice
Give any LLM a voice. On WhatsApp, in your terminal, or anywhere you build.
OmniVoice is an open-source voice layer for AI. Point it at Claude, GPT-4o, or a local Ollama model — it handles the mic, the transcription, the thinking, and the spoken reply. No proprietary stack. No lock-in. Free path available.
→ omnivoice.dev

What it does
Your voice  →  Whisper ASR  →  Your LLM  →  EdgeTTS  →  Spoken reply
Two ways to use it out of the box:
1. WhatsApp — Send a voice note. Get a voice note back. Works on any phone, no app install.
2. Terminal / CLI — Push to talk from your terminal. Press Enter to speak, press Enter to send. AI replies out loud.

Quick start
Prerequisites

Python 3.10+
ffmpeg (for WhatsApp audio conversion)
A Twilio account (for WhatsApp only)

bash# Clone
git clone https://github.com/fortheloveofai-tech/omnivoice.git
cd omnivoice

# Install
pip install -r requirements.txt

# Configure (interactive setup — sets up your .env)
python setup_env.py
Run the terminal voice chat
bashpython -m omni_voice.voice_cli
Press Enter to start recording. Press Enter again to send. Press Ctrl+C to quit.
Run the WhatsApp server
bashpython start.py
This starts a cloudflare/ngrok tunnel, captures the public URL, updates your Twilio webhook automatically, and launches the server. One command.

Zero-cost path
You don't need any paid API keys to run OmniVoice.
ComponentFree optionHowLLMOllama (local)ollama pull llama3ASRWhisper (local)Runs on your machineTTSEdgeTTS (Microsoft)Free neural voices, no key neededWhatsAppTwilio sandboxFree for testing
Set these in .env or via python setup_env.py:
envLLM_PROVIDER=ollama
ASR_PROVIDER=whisper
TTS_PROVIDER=edge

Supported providers
LLM
ProviderValueNotesAnthropic (Claude)anthropicSet ANTHROPIC_API_KEYOpenAI (GPT-4o)openaiSet OPENAI_API_KEYOllama (local)ollamaFree, runs locallyOpenRouteropenrouterSet OPENROUTER_API_KEY
ASR (Speech-to-Text)
ProviderValueNotesWhisper (local)whisperDefault. No key. Set WHISPER_MODEL_SIZE (tiny/base/small)DeepgramdeepgramCloud, fast. Free tier at console.deepgram.com
TTS (Text-to-Speech)
ProviderValueNotesEdgeTTSedgeDefault. Free Microsoft neural voicesElevenLabselevenlabsBest quality. Set ELEVENLABS_API_KEYOpenAI TTSopenaiSet OPENAI_API_KEY

Configuration
Run python setup_env.py for a guided interactive setup, or create .env manually:
env# LLM
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# ASR
ASR_PROVIDER=whisper
WHISPER_MODEL_SIZE=base

# TTS
TTS_PROVIDER=edge
EDGE_TTS_VOICE=en-US-AriaNeural

# WhatsApp / Twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886

# Tunnel (for WhatsApp webhook)
TUNNEL_PROVIDER=cloudflared   # or ngrok
NGROK_AUTH_TOKEN=             # only if using ngrok

Project structure
omnivoice/
├── omni_voice/
│   ├── voice_cli.py          # Terminal push-to-talk client
│   ├── asr/
│   │   ├── whisper_local.py  # Local Whisper ASR (no API key)
│   │   └── deepgram.py       # Deepgram cloud ASR
│   ├── tts/
│   │   ├── edge_tts.py       # Microsoft EdgeTTS (free)
│   │   ├── elevenlabs.py     # ElevenLabs TTS
│   │   └── openai_tts.py     # OpenAI TTS
│   └── llm/
│       ├── anthropic.py      # Claude
│       ├── openai.py         # GPT-4o
│       └── ollama.py         # Local Ollama
├── integrations/
│   └── twilio/
│       └── whatsapp_webhook.py   # FastAPI WhatsApp server
├── setup_env.py              # Interactive .env configurator
├── start.py                  # One-command launcher (tunnel + webhook + server)
├── requirements.txt
└── .env.example

WhatsApp pipeline (detailed)
User sends voice note on WhatsApp
        ↓
Twilio receives it, POSTs to your webhook
        ↓
OmniVoice downloads the OGG audio
        ↓
ffmpeg converts it to WAV
        ↓
Whisper (or Deepgram) transcribes the audio
        ↓
Your LLM generates a response
        ↓
EdgeTTS (or ElevenLabs) converts text → MP3
        ↓
OmniVoice converts MP3 → OGG, stores in memory
        ↓
Twilio fetches audio from /twilio/audio/{filename}
        ↓
User receives a voice note reply on WhatsApp
End-to-end latency is typically 3–6 seconds depending on LLM and model size.

Terminal CLI pipeline (detailed)
Press Enter  →  sounddevice starts recording
Press Enter  →  recording stops, audio saved
        ↓
Whisper transcribes locally (no cloud call)
        ↓
Transcript sent to your LLM
        ↓
Response streamed to terminal
        ↓
EdgeTTS speaks the reply through your speakers
        ↓
Cursor returns, ready for next input

Whisper model sizes
SizeRAM neededSpeedAccuracytiny~400MBFastestGood for clear speechbase~600MBFastGood general use (recommended)small~1.2GBModerateBetter for accents/noise
Set in .env: WHISPER_MODEL_SIZE=base
The model downloads automatically on first run and is cached locally.

ffmpeg
Required for WhatsApp audio conversion (OGG ↔ WAV/MP3).
bash# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg
Not needed for the terminal CLI — only for WhatsApp voice notes.

Deploying WhatsApp to production
start.py uses a temporary tunnel (cloudflared or ngrok) which dies when your machine sleeps. For a permanent WhatsApp endpoint:

Deploy to Railway — connect GitHub repo, publish directory is root, add env vars in dashboard
Add Procfile: web: uvicorn integrations.twilio.whatsapp_webhook:app --host 0.0.0.0 --port $PORT
Switch ASR to Deepgram (Whisper needs ~1GB RAM; Railway starter gives 512MB)
Update Twilio webhook to your Railway URL

For Whisper in production, use Railway's $20/month plan (8GB RAM) or a DigitalOcean droplet.

Environment variables reference
VariableDefaultDescriptionLLM_PROVIDERanthropicanthropic, openai, ollama, openrouterANTHROPIC_API_KEY—Required if LLM_PROVIDER=anthropicOPENAI_API_KEY—Required if LLM_PROVIDER=openaiOLLAMA_MODELllama3Model name for OllamaOPENROUTER_API_KEY—Required if LLM_PROVIDER=openrouterASR_PROVIDERwhisperwhisper or deepgramWHISPER_MODEL_SIZEbasetiny, base, smallDEEPGRAM_API_KEY—Required if ASR_PROVIDER=deepgramTTS_PROVIDERedgeedge, elevenlabs, openaiEDGE_TTS_VOICEen-US-AriaNeuralAny EdgeTTS voice nameELEVENLABS_API_KEY—Required if TTS_PROVIDER=elevenlabsTWILIO_ACCOUNT_SID—From Twilio consoleTWILIO_AUTH_TOKEN—From Twilio consoleTWILIO_WHATSAPP_FROM—e.g. whatsapp:+14155238886TUNNEL_PROVIDERcloudflaredcloudflared or ngrokNGROK_AUTH_TOKEN—Required if TUNNEL_PROVIDER=ngrok

Requirements
fastapi
uvicorn
httpx
python-dotenv
openai
anthropic
edge-tts
elevenlabs
openai-whisper
deepgram-sdk
sounddevice
pydub
twilio
requests
numpy

Contributing
PRs welcome. The cleanest areas to extend:

New LLM provider — add a file under omni_voice/llm/ implementing generate(prompt) -> str
New ASR provider — add under omni_voice/asr/ implementing transcribe(audio_bytes) -> str
New TTS provider — add under omni_voice/tts/ implementing synthesize(text) -> bytes
New channel — Telegram, Signal, SMS — follow the WhatsApp integration pattern


License
MIT — use it, fork it, build on it.

Links

Website: omnivoice.dev
GitHub: github.com/fortheloveofai-tech/omnivoice
