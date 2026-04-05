#!/usr/bin/env python3
"""
OmniVoice — One-command launcher
=================================
Does everything in one terminal window:

  1. Runs setup_env.py if .env is missing or --setup flag passed
  2. Starts a Cloudflare Tunnel (or ngrok as fallback)
  3. Auto-updates your Twilio WhatsApp webhook to the tunnel URL
  4. Starts the OmniVoice server

Usage
-----
  python start.py            # normal start
  python start.py --setup    # force re-run setup wizard first
  python start.py --no-tunnel  # skip tunnel (if you have a static domain)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import threading
from pathlib import Path

ROOT    = Path(__file__).parent
ENV_FILE = ROOT / ".env"

# ── Colours ────────────────────────────────────────────────────────────────────
R = "\033[0m"
BOLD = "\033[1m"
G    = "\033[92m"
Y    = "\033[93m"
C    = "\033[96m"
RED  = "\033[91m"
DIM  = "\033[2m"

def ok(msg):  print(f"  {G}✓{R}  {msg}")
def info(msg):print(f"  {C}→{R}  {msg}")
def warn(msg):print(f"  {Y}⚠{R}  {msg}")
def err(msg): print(f"  {RED}✗{R}  {msg}")
def banner(msg): print(f"\n{BOLD}{C}{msg}{R}")


# ── .env helpers ───────────────────────────────────────────────────────────────

def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def set_env_key(key: str, value: str) -> None:
    """Update or add a key in .env without touching other lines."""
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    updated = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}=") or line.strip().startswith(f"{key} ="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n")


# ── Tunnel helpers ─────────────────────────────────────────────────────────────

def _find_cloudflared() -> str | None:
    """Return path to cloudflared binary, or None."""
    for candidate in ["cloudflared", "/usr/local/bin/cloudflared",
                      "/opt/homebrew/bin/cloudflared"]:
        if subprocess.run(["which", candidate], capture_output=True).returncode == 0:
            return candidate
    return None


def _find_ngrok() -> str | None:
    if subprocess.run(["which", "ngrok"], capture_output=True).returncode == 0:
        return "ngrok"
    return None


def start_cloudflare_tunnel(port: int) -> tuple[subprocess.Popen, str] | tuple[None, None]:
    """Start cloudflared quick tunnel, return (process, https_url)."""
    cf = _find_cloudflared()
    if not cf:
        return None, None

    info("Starting Cloudflare Tunnel …")
    proc = subprocess.Popen(
        [cf, "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    url = None
    deadline = time.time() + 30
    for line in proc.stdout:
        if time.time() > deadline:
            break
        # cloudflared prints the URL to stdout/stderr
        m = re.search(r"https://[a-z0-9\-]+\.trycloudflare\.com", line)
        if m:
            url = m.group(0)
            break

    if url:
        ok(f"Tunnel active: {BOLD}{url}{R}")
        # Drain stdout in background so process doesn't block
        threading.Thread(target=lambda: [_ for _ in proc.stdout], daemon=True).start()
        return proc, url

    proc.terminate()
    return None, None


def start_ngrok_tunnel(port: int) -> tuple[subprocess.Popen, str] | tuple[None, None]:
    """Start ngrok, return (process, https_url) via ngrok local API."""
    import urllib.request, json as _json
    ng = _find_ngrok()
    if not ng:
        return None, None

    info("Starting ngrok tunnel …")
    proc = subprocess.Popen(
        [ng, "http", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # ngrok exposes a local API at 4040
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=2) as r:
                data = _json.loads(r.read())
                for t in data.get("tunnels", []):
                    if t.get("proto") == "https":
                        url = t["public_url"]
                        ok(f"ngrok tunnel active: {BOLD}{url}{R}")
                        return proc, url
        except Exception:
            pass
        time.sleep(1)

    proc.terminate()
    return None, None


def start_tunnel(port: int) -> tuple[subprocess.Popen | None, str | None]:
    """Try cloudflared first, then ngrok, then give up."""
    proc, url = start_cloudflare_tunnel(port)
    if url:
        return proc, url
    proc, url = start_ngrok_tunnel(port)
    if url:
        return proc, url
    return None, None


# ── Twilio webhook auto-config ─────────────────────────────────────────────────

def update_twilio_webhook(env: dict[str, str], public_url: str) -> None:
    """Set Twilio WhatsApp incoming webhook to {public_url}/twilio/whatsapp."""
    sid   = env.get("TWILIO_ACCOUNT_SID", "")
    token = env.get("TWILIO_AUTH_TOKEN", "")
    phone = env.get("TWILIO_PHONE_NUMBER", "")   # e.g. whatsapp:+14155238886

    if not (sid and token and phone):
        warn("Twilio not fully configured — skipping webhook update.")
        warn("Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER in .env")
        return

    webhook_url = f"{public_url}/twilio/whatsapp"
    info(f"Updating Twilio webhook → {webhook_url}")

    try:
        from twilio.rest import Client
        client = Client(sid, token)

        # Strip "whatsapp:" prefix to get the raw E.164 number
        raw_number = phone.replace("whatsapp:", "").strip()

        # Find the matching incoming number and update it
        numbers = client.incoming_phone_numbers.list(phone_number=raw_number)
        if numbers:
            numbers[0].update(sms_url=webhook_url, sms_method="POST")
            ok(f"Twilio webhook updated ✓")
        else:
            # Try sandbox update (Twilio sandbox uses a different API)
            try:
                client.messaging.v1.services.list()  # just a connectivity check
            except Exception:
                pass
            # Sandbox: update via sandbox participant URL
            warn("Phone number not found in your account — if you're using the")
            warn(f"Twilio sandbox, manually set the webhook to: {webhook_url}")

    except ImportError:
        warn("twilio not installed — skipping auto-webhook.")
        warn(f"Manually set your Twilio webhook to: {webhook_url}")
    except Exception as e:
        warn(f"Twilio webhook update failed: {e}")
        warn(f"Manually set your Twilio webhook to: {webhook_url}")

    # Always persist the new public URL
    set_env_key("PUBLIC_BASE_URL", public_url)


# ── Preflight checks ──────────────────────────────────────────────────────────

def check_deps() -> bool:
    missing = []
    for pkg in ["fastapi", "uvicorn", "httpx", "pydub"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        err(f"Missing packages: {', '.join(missing)}")
        err(f"Run: pip install -r requirements.txt")
        return False

    # ffmpeg is required by pydub for WhatsApp audio conversion
    if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
        warn("ffmpeg not found — WhatsApp voice notes won't work without it.")
        warn("Install with:  brew install ffmpeg")

    return True


def check_minimum_config(env: dict[str, str]) -> bool:
    """Must have at least one LLM key."""
    has_llm = any([
        env.get("OPENAI_API_KEY"),
        env.get("ANTHROPIC_API_KEY"),
        env.get("OLLAMA_BASE_URL"),
    ])
    if not has_llm:
        err("No LLM configured. Re-run: python setup_env.py")
        return False
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="OmniVoice launcher")
    parser.add_argument("--setup",     action="store_true", help="Run setup wizard before starting")
    parser.add_argument("--no-tunnel", action="store_true", help="Skip tunnel (use existing PUBLIC_BASE_URL)")
    parser.add_argument("--port",      type=int, default=8000, help="Server port (default 8000)")
    args = parser.parse_args()

    print(f"""
{BOLD}{C}╔══════════════════════════════════════════╗
║        OmniVoice — Starting Up           ║
╚══════════════════════════════════════════╝{R}""")

    # ── Step 1: Setup wizard ───────────────────────────────────────────────────
    if args.setup or not ENV_FILE.exists():
        banner("Step 1 · Configuration")
        if not ENV_FILE.exists():
            info("No .env found — running setup wizard …\n")
        subprocess.run([sys.executable, str(ROOT / "setup_env.py")], check=True)

    env = load_env()

    # ── Step 2: Preflight ──────────────────────────────────────────────────────
    banner("Step 2 · Checking dependencies")
    if not check_deps():
        sys.exit(1)
    ok("All dependencies present")

    if not check_minimum_config(env):
        sys.exit(1)
    ok("Minimum config present")

    # ── Step 3: Tunnel ────────────────────────────────────────────────────────
    tunnel_proc = None
    if not args.no_tunnel and env.get("TWILIO_ACCOUNT_SID"):
        banner("Step 3 · Public tunnel")
        tunnel_proc, public_url = start_tunnel(args.port)
        if public_url:
            update_twilio_webhook(env, public_url)
            # Reload env after update
            env = load_env()
        else:
            warn("No tunnel tool found (cloudflared or ngrok).")
            warn("Install cloudflared:  brew install cloudflare/cloudflare/cloudflared")
            warn("Or ngrok:            brew install ngrok/ngrok/ngrok")
            if env.get("PUBLIC_BASE_URL"):
                warn(f"Using existing PUBLIC_BASE_URL: {env['PUBLIC_BASE_URL']}")
            else:
                warn("WhatsApp webhook won't work without a public URL.")
    else:
        banner("Step 3 · Tunnel")
        if args.no_tunnel:
            info("Tunnel skipped (--no-tunnel)")
        else:
            info("Twilio not configured — skipping tunnel")
        if env.get("PUBLIC_BASE_URL"):
            ok(f"Using PUBLIC_BASE_URL: {env['PUBLIC_BASE_URL']}")

    # ── Step 4: Start server ──────────────────────────────────────────────────
    banner("Step 4 · Starting OmniVoice server")

    asr = env.get("ASR_PROVIDER", "deepgram")
    llm = env.get("LLM_PROVIDER", "openai")
    model = env.get("OPENAI_MODEL") or env.get("ANTHROPIC_MODEL") or env.get("OLLAMA_MODEL", "?")
    tts = env.get("TTS_PROVIDER", "edge")

    print(f"""
  {DIM}ASR  {R}{C}{asr}{R}
  {DIM}LLM  {R}{C}{llm} / {model}{R}
  {DIM}TTS  {R}{C}{tts}{R}
  {DIM}PORT {R}{C}{args.port}{R}

  {DIM}Voice CLI   →  {R}python -m omni_voice.voice_cli
  {DIM}API docs    →  {R}http://localhost:{args.port}/docs
  {DIM}Landing     →  {R}http://localhost:{args.port}/
""")

    try:
        subprocess.run(
            [sys.executable, "-m", "uvicorn", "omni_voice.main:app",
             "--host", "0.0.0.0", "--port", str(args.port), "--reload"],
            cwd=ROOT,
        )
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel_proc:
            tunnel_proc.terminate()
        print(f"\n  {Y}OmniVoice shut down.{R}\n")


if __name__ == "__main__":
    main()
