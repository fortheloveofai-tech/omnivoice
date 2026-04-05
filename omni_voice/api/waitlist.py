"""
waitlist.py — Email waitlist endpoint
======================================

POST /waitlist  { "email": "user@example.com" }
  → stores email in waitlist.json (next to this file, persists across restarts)
  → sends a notification email to NOTIFY_EMAIL via Gmail SMTP
  → returns 200 OK or 409 if already signed up

Setup
-----
Add to .env:
  NOTIFY_EMAIL=fortheloveofai082@gmail.com
  GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # Gmail App Password (not your main password)

To generate a Gmail App Password:
  1. Go to myaccount.google.com/security
  2. Enable 2-Step Verification if not already on
  3. Go to App Passwords → create one for "OmniVoice Waitlist"
  4. Paste the 16-char password into GMAIL_APP_PASSWORD
"""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from omni_voice.config import settings

log = logging.getLogger(__name__)
router = APIRouter(tags=["waitlist"])

# ── Storage ───────────────────────────────────────────────────────────────────

WAITLIST_FILE = Path(__file__).parent.parent.parent / "waitlist.json"


def _load() -> list[dict]:
    if WAITLIST_FILE.exists():
        try:
            return json.loads(WAITLIST_FILE.read_text())
        except Exception:
            return []
    return []


def _save(entries: list[dict]) -> None:
    WAITLIST_FILE.write_text(json.dumps(entries, indent=2))


# ── Email notification ────────────────────────────────────────────────────────

def _send_notification(email: str, total: int) -> None:
    """Send a Gmail notification to NOTIFY_EMAIL when someone joins."""
    notify_email: Optional[str] = getattr(settings, "notify_email", None)
    app_password: Optional[str] = getattr(settings, "gmail_app_password", None)

    if not notify_email or not app_password:
        log.warning("NOTIFY_EMAIL or GMAIL_APP_PASSWORD not set — skipping email notification")
        return

    try:
        msg = MIMEText(
            f"New waitlist signup!\n\n"
            f"Email: {email}\n"
            f"Time:  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Total signups: {total}\n\n"
            f"— OmniVoice waitlist"
        )
        msg["Subject"] = f"🎙️ New OmniVoice signup: {email}"
        msg["From"]    = notify_email
        msg["To"]      = notify_email

        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(notify_email, app_password)
            server.sendmail(notify_email, notify_email, msg.as_string())

        log.info("Notification sent to %s", notify_email)
    except Exception as e:
        log.warning("Failed to send notification email: %s", e)


# ── Endpoint ──────────────────────────────────────────────────────────────────

class WaitlistRequest(BaseModel):
    email: EmailStr


@router.post("/waitlist")
async def join_waitlist(body: WaitlistRequest) -> JSONResponse:
    email = body.email.lower().strip()
    entries = _load()

    # Check duplicate
    if any(e["email"] == email for e in entries):
        return JSONResponse(
            status_code=409,
            content={"status": "already_registered", "message": "You're already on the list!"}
        )

    # Store
    entries.append({
        "email":      email,
        "joined_at":  datetime.now(timezone.utc).isoformat(),
    })
    _save(entries)
    log.info("Waitlist signup: %s (total: %d)", email, len(entries))

    # Notify
    _send_notification(email, len(entries))

    return JSONResponse(
        status_code=200,
        content={
            "status":  "ok",
            "message": "You're on the list! We'll email you at launch.",
            "total":   len(entries),
        }
    )


@router.get("/waitlist/count")
async def waitlist_count() -> JSONResponse:
    """Public endpoint — returns total signup count."""
    return JSONResponse(content={"count": len(_load())})
