# OmniVoice × Twilio Integration

Two channels — both feed into the same ASR → LLM → TTS pipeline:

| Channel | How it works |
|---|---|
| **WhatsApp voice note** | User records & sends audio → ASR → LLM → TTS reply sent back as a voice note |
| **WhatsApp text** | User sends a text → LLM → text reply |
| **Phone call (PSTN)** | User calls your Twilio number → real-time audio stream → ASR → LLM → TTS spoken back |

---

## 1. Prerequisites

```bash
pip install twilio httpx pydub
# pydub also requires ffmpeg:
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Ubuntu/Debian
```

---

## 2. Twilio account setup

### WhatsApp sandbox (free, no approval needed)

1. Sign up at [twilio.com](https://www.twilio.com)
2. Go to **Messaging → Try it out → Send a WhatsApp message**
3. Follow the sandbox join instructions (text a code to the sandbox number)
4. Set the **"When a message comes in"** webhook URL to:
   ```
   POST https://<your-public-url>/twilio/whatsapp
   ```

### Phone calls

1. In Twilio Console, buy a phone number with **Voice** capability
2. Under the number's **Voice** config, set:
   - **"A call comes in"** → Webhook → `POST https://<your-public-url>/twilio/call/incoming`
3. Note your number in `.env` as `TWILIO_PHONE_NUMBER`

---

## 3. Environment variables

Copy `.env.example` and fill in:

```env
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
PUBLIC_BASE_URL=https://your-ngrok-id.ngrok.io
TWILIO_PHONE_NUMBER=+15005550006
```

---

## 4. Expose localhost with ngrok

Twilio needs a public HTTPS URL to reach your server.

```bash
# Install: brew install ngrok / https://ngrok.com
ngrok http 8000
# Copy the https://xxxx.ngrok.io URL → set as PUBLIC_BASE_URL in .env
```

---

## 5. Start the server

```bash
# From the omni-voice root:
python -m omni_voice.main
```

On startup you'll see:
```
Twilio integration loaded  (WhatsApp + Phone)
```

Endpoints available:
- `POST /twilio/whatsapp` — WhatsApp webhook
- `POST /twilio/call/incoming` — Phone call TwiML
- `WS   /twilio/call/stream` — Twilio Media Streams WebSocket

---

## 6. Test it

**WhatsApp:**
- Open WhatsApp on your phone
- Text or send a voice note to the sandbox number (`+1 415 523 8886`)
- The assistant replies with text + a voice note

**Phone call:**
- Call your Twilio number from any phone
- You'll hear a greeting, then speak — the LLM responds in real time

---

## Architecture

```
WhatsApp voice note
  └─ POST /twilio/whatsapp
       ├─ download OGG from Twilio CDN
       ├─ pydub: OGG → PCM-16 16kHz
       ├─ ASRProvider.transcribe()
       ├─ LLMProvider.generate()
       ├─ TTSProvider.synthesise() → OGG
       └─ TwiML <Media> reply

Phone call (PSTN)
  └─ POST /twilio/call/incoming → TwiML <Stream>
       └─ WS /twilio/call/stream
            ├─ Twilio μ-law 8kHz frames in
            ├─ audioop: μ-law 8kHz → PCM-16 16kHz
            ├─ silence detector → flush to ASR
            ├─ ASRProvider.transcribe()
            ├─ LLMProvider.generate()
            ├─ TTSProvider.synthesise()
            ├─ audioop: PCM-16 16kHz → μ-law 8kHz
            └─ base64 μ-law frames back to Twilio
```

---

## Production notes

- **Audio store**: `whatsapp_webhook.py` currently caches TTS audio in memory (`_AUDIO_STORE`). Replace with S3/GCS for production.
- **Conversation history**: `CallSession` keeps history per call in memory. Add Redis persistence for multi-instance deployments.
- **Twilio signature validation**: enabled by default. Requires `TWILIO_AUTH_TOKEN` in env.
- **Rate limits**: Twilio WhatsApp sandbox limits to ~1 msg/sec. Production WhatsApp Business API has higher limits.
