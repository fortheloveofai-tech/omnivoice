"""
REST API routes for health, metrics, and session inspection.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from omni_voice.config import settings
from omni_voice.session.manager import session_manager

router = APIRouter()


@router.get("/health")
async def health() -> JSONResponse:
    """Health check endpoint — used by Docker, load balancers, and the demo script."""
    return JSONResponse({
        "status": "ok",
        "active_sessions": session_manager.count,
        "providers": {
            "asr": settings.asr_provider,
            "llm": settings.llm_provider,
            "tts": settings.tts_provider,
        },
    })


@router.get("/v1/sessions")
async def list_sessions() -> JSONResponse:
    """List active session IDs (admin use)."""
    return JSONResponse({"count": session_manager.count})


@router.get("/v1/config")
async def get_config() -> JSONResponse:
    """Return current provider configuration (no secrets)."""
    return JSONResponse({
        "asr_provider": settings.asr_provider,
        "llm_provider": settings.llm_provider,
        "tts_provider": settings.tts_provider,
        "system_prompt": settings.system_prompt,
        "tab": {
            "speech_rate": settings.tab_speech_rate_tokens_per_sec,
            "high_water_sec": settings.tab_high_water_sec,
            "low_water_sec": settings.tab_low_water_sec,
        },
        "atts": {
            "epoch_ms": settings.atts_epoch_ms,
            "margin_ms": settings.atts_margin_ms,
        },
    })
