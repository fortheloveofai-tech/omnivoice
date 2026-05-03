"""
OmniVoice — One-command launcher

1. Starts a cloudflared or ngrok tunnel
2. Captures the public HTTPS URL
3. Updates Twilio WhatsApp webhook automatically
4. Launches the FastAPI server with uvicorn

Usage:
    python start.py
"""
import os
import re
import subprocess
import sys
import time
import threading

import httpx
from dotenv import load_dotenv, set_key

load_dotenv()

TUNNEL_PROVIDER     = os.getenv("TUNNEL_PROVIDER", "cloudflared").lower()
NGROK_AUTH_TOKEN    = os.getenv("NGROK_AUTH_TOKEN", "")
TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM      = os.getenv("TWILIO_WHATSAPP_FROM", "")
PORT                = int(os.getenv("PORT", "8000"))

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def info(msg):  print(f"{GREEN}  ✓{RESET}  {msg}")
def warn(msg):  print(f"{YELLOW}  ⚠{RESET}  {msg}")
def error(msg): print(f"{RED}  ✗{RESET}  {msg}")


def check_deps():
    """Preflight checks."""
    if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
        warn("ffmpeg not found — WhatsApp voice notes won't work without it.")
        warn("Install with:  brew install ffmpeg")

    if TUNNEL_PROVIDER == "cloudflared":
        if subprocess.run(["which", "cloudflared"], capture_output=True).returncode != 0:
            error("cloudflared not found. Install: brew install cloudflared")
            sys.exit(1)
    else:
        if subprocess.run(["which", "ngrok"], capture_output=True).returncode != 0:
            error("ngrok not found. Install: brew install ngrok/ngrok/ngrok")
            sys.exit(1)


def start_tunnel() -> str:
    """Start tunnel and return public HTTPS URL."""
    if TUNNEL_PROVIDER == "cloudflared":
        return _start_cloudflared()
    return _start_ngrok()


def _start_cloudflared() -> str:
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url_pattern = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")
    print(f"{DIM}  Starting cloudflared tunnel…{RESET}", flush=True)
    for line in proc.stdout:
        match = url_pattern.search(line)
        if match:
            return match.group(0)
    raise RuntimeError("Could not capture cloudflared URL")


def _start_ngrok() -> str:
    if NGROK_AUTH_TOKEN:
        subprocess.run(["ngrok", "config", "add-authtoken", NGROK_AUTH_TOKEN],
                       capture_output=True)
    proc = subprocess.Popen(
        ["ngrok", "http", str(PORT), "--log=stdout"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    print(f"{DIM}  Starting ngrok tunnel…{RESET}", flush=True)
    time.sleep(2)

    try:
        resp = httpx.get("http://localhost:4040/api/tunnels", timeout=5)
        tunnels = resp.json().get("tunnels", [])
        for t in tunnels:
            if t.get("proto") == "https":
                return t["public_url"]
    except Exception:
        pass

    url_pattern = re.compile(r"https://[a-z0-9\-]+\.ngrok(-free)?\.app")
    for line in proc.stdout:
        match = url_pattern.search(line)
        if match:
            return match.group(0)
    raise RuntimeError("Could not capture ngrok URL")


def update_twilio_webhook(public_url: str):
    """Update Twilio WhatsApp sandbox webhook to the new tunnel URL."""
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        warn("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set — skipping webhook update.")
        return

    webhook_url = f"{public_url}/twilio/whatsapp"

    # Extract phone number from e.g. "whatsapp:+14155238886"
    number = TWILIO_WA_FROM.replace("whatsapp:", "").strip()
    if not number:
        warn("TWILIO_WHATSAPP_FROM not set — skipping webhook update.")
        return

    try:
        # Find the incoming phone number SID
        r = httpx.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/IncomingPhoneNumbers.json",
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=10,
        )
        r.raise_for_status()
        numbers = r.json().get("incoming_phone_numbers", [])
        sid = next((n["sid"] for n in numbers if n["phone_number"] == number), None)

        if sid:
            httpx.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/IncomingPhoneNumbers/{sid}.json",
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                data={"SmsUrl": webhook_url},
                timeout=10,
            )
            info(f"Twilio webhook updated → {webhook_url}")
        else:
            # Try sandbox
            httpx.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/SandboxWhatsapp.json",
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                data={"StatusCallback": webhook_url, "SandboxWhatsappUrl": webhook_url},
                timeout=10,
            )
            info(f"Twilio sandbox webhook updated → {webhook_url}")

    except Exception as e:
        warn(f"Could not auto-update Twilio webhook: {e}")
        warn(f"Set manually in Twilio console → {webhook_url}")


def main():
    print(f"\n{GREEN}  OmniVoice — Starting up{RESET}\n")
    check_deps()

    # Start tunnel in background thread
    public_url = None
    tunnel_error = None

    def run_tunnel():
        nonlocal public_url, tunnel_error
        try:
            public_url = start_tunnel()
        except Exception as e:
            tunnel_error = e

    t = threading.Thread(target=run_tunnel, daemon=True)
    t.start()
    t.join(timeout=20)

    if tunnel_error:
        error(f"Tunnel failed: {tunnel_error}")
        sys.exit(1)

    if not public_url:
        error("Could not get tunnel URL. Is cloudflared/ngrok running?")
        sys.exit(1)

    info(f"Tunnel live → {public_url}")

    # Write PUBLIC_URL to .env
    set_key(".env", "PUBLIC_URL", public_url)

    # Update Twilio webhook
    update_twilio_webhook(public_url)

    info(f"Starting server on port {PORT}…\n")
    print(f"  {DIM}Send a WhatsApp voice note to your Twilio number to test.{RESET}\n")

    # Set PUBLIC_URL in env for the server process
    env = os.environ.copy()
    env["PUBLIC_URL"] = public_url

    subprocess.run(
        [sys.executable, "-m", "uvicorn",
         "integrations.twilio.whatsapp_webhook:app",
         "--host", "0.0.0.0",
         "--port", str(PORT),
         "--reload"],
        env=env,
    )


if __name__ == "__main__":
    main()
