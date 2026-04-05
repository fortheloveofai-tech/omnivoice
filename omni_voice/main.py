"""
Omni Voice – Platform Server Entry Point
=========================================

Usage
-----
  python -m omni_voice.main          # start server
  uvicorn omni_voice.main:app        # with uvicorn directly

Environment
-----------
  Copy .env.example → .env and fill in API keys before starting.
"""
from __future__ import annotations

import logging
import sys

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from omni_voice.api.routes import router
from omni_voice.api.waitlist import router as waitlist_router
from omni_voice.api.websocket import voice_session_handler
from omni_voice.config import settings
from omni_voice.observability.metrics import start_metrics_server

# Twilio integrations (WhatsApp + Phone calls)
try:
    from integrations.twilio.whatsapp_webhook import router as twilio_wa_router
    from integrations.twilio.voice_call_handler import router as twilio_call_router
    _TWILIO_ENABLED = True
except ImportError:
    _TWILIO_ENABLED = False

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Omni Voice — Real-Time Voice AI Platform",
    description=(
        "Sub-300ms voice AI infrastructure platform. "
        "Swap ASR, LLM, and TTS providers via environment variables."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow all origins in development; restrict in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST routes
app.include_router(router)
app.include_router(waitlist_router)

# Twilio routes (WhatsApp + Phone)
if _TWILIO_ENABLED:
    app.include_router(twilio_wa_router)
    app.include_router(twilio_call_router)
    logger.info("Twilio integration loaded  (WhatsApp + Phone)")

# Static assets (favicons, icons, manifest, robots, sitemap)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Serve root-level static files expected by browsers & crawlers
@app.get("/favicon.svg",         include_in_schema=False)
async def favicon_svg():         return FileResponse("static/favicon.svg", media_type="image/svg+xml")

@app.get("/favicon-32.png",      include_in_schema=False)
async def favicon_32():          return FileResponse("static/favicon-32.png", media_type="image/png")

@app.get("/favicon-16.png",      include_in_schema=False)
async def favicon_16():          return FileResponse("static/favicon-16.png", media_type="image/png")

@app.get("/apple-touch-icon.png",include_in_schema=False)
async def apple_touch():         return FileResponse("static/apple-touch-icon.png", media_type="image/png")

@app.get("/og-image.png",        include_in_schema=False)
async def og_image():            return FileResponse("static/og-image.png", media_type="image/png")

@app.get("/site.webmanifest",    include_in_schema=False)
async def webmanifest():         return FileResponse("static/site.webmanifest", media_type="application/manifest+json")

@app.get("/robots.txt",          include_in_schema=False)
async def robots():              return FileResponse("static/robots.txt", media_type="text/plain")

@app.get("/sitemap.xml",         include_in_schema=False)
async def sitemap():             return FileResponse("static/sitemap.xml", media_type="application/xml")

# Landing page
@app.get("/")
async def serve_landing() -> FileResponse:
    return FileResponse("demo/landing.html")


# WebSocket endpoint
@app.websocket("/ws/voice")
async def ws_voice(websocket: WebSocket) -> None:
    """
    Main voice session WebSocket.

    Connect with: ws://localhost:8000/ws/voice
    Protocol documented in omni_voice/api/websocket.py
    """
    await voice_session_handler(websocket)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("  Omni Voice Platform  v0.1.0")
    logger.info("  ASR : %s", settings.asr_provider)
    logger.info("  LLM : %s / %s", settings.llm_provider,
                settings.openai_model if settings.llm_provider == "openai" else
                settings.anthropic_model if settings.llm_provider == "anthropic" else
                settings.ollama_model)
    logger.info("  TTS : %s", settings.tts_provider)
    logger.info("  WS  : ws://localhost:%d/ws/voice", settings.port)
    logger.info("  API : http://localhost:%d/docs", settings.port)
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Start Prometheus exporter on a separate port
    try:
        start_metrics_server(settings.prometheus_port)
        logger.info("  Metrics: http://localhost:%d/metrics", settings.prometheus_port)
    except Exception as exc:
        logger.warning("Could not start metrics server: %s", exc)


@app.on_event("shutdown")
async def shutdown() -> None:
    logger.info("Shutting down Omni Voice Platform")


# ── CLI ───────────────────────────────────────────────────────────────────────

def cli() -> None:
    """Entry point for `omni-voice` console script."""
    uvicorn.run(
        "omni_voice.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )


if __name__ == "__main__":
    cli()
