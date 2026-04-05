"""
WebSocket handler for real-time voice sessions.

Protocol (JSON control messages + binary audio frames)
------------------------------------------------------
Client → Server (JSON):
  { "type": "start_session" }
  { "type": "network_metrics", "rtt_ms": 30, "loss_pct": 0.1, "bw_kbps": 1000 }
  { "type": "end_session" }

Client → Server (binary):
  Raw PCM audio bytes (16-bit, 16 kHz, mono)

Server → Client (JSON):
  { "type": "session_started", "session_id": "..." }
  { "type": "transcript", "text": "..." }
  { "type": "session_ended" }
  { "type": "error", "message": "..." }

Server → Client (binary):
  Synthesised audio bytes (format depends on TTS provider)
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from omni_voice.core.aqal import NetworkMetrics
from omni_voice.providers import build_asr, build_llm, build_tts
from omni_voice.session.manager import VoiceSession, session_manager

logger = logging.getLogger(__name__)


async def voice_session_handler(websocket: WebSocket) -> None:
    """
    Main WebSocket handler.  One call per connected client.
    """
    await websocket.accept()
    session: Optional[VoiceSession] = None

    async def send_audio(audio_bytes: bytes) -> None:
        """Deliver synthesised audio to the WebSocket client."""
        try:
            await websocket.send_bytes(audio_bytes)
        except Exception as exc:
            logger.warning("Failed to send audio: %s", exc)

    async def send_transcript(text: str) -> None:
        """Send partial transcript as a JSON control message."""
        try:
            await websocket.send_json({"type": "transcript", "text": text})
        except Exception:
            pass

    try:
        async for message in websocket.iter_text():
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = data.get("type")

            if msg_type == "start_session":
                if session:
                    # Already in a session — restart
                    await session_manager.destroy(session.session_id)

                session = session_manager.create(
                    asr=build_asr(),
                    llm=build_llm(),
                    tts=build_tts(),
                    audio_sink=send_audio,
                    transcript_sink=send_transcript,
                )
                await session.start()
                await websocket.send_json({
                    "type": "session_started",
                    "session_id": session.session_id,
                })
                logger.info("Session started: %s", session.session_id)

                # Switch to binary audio mode on a separate task
                asyncio.create_task(
                    _binary_audio_loop(websocket, session),
                    name=f"binary-{session.session_id}",
                )

            elif msg_type == "network_metrics":
                if session:
                    metrics = NetworkMetrics(
                        rtt_ms=float(data.get("rtt_ms", 0)),
                        loss_pct=float(data.get("loss_pct", 0)),
                        bw_kbps=float(data.get("bw_kbps", 1000)),
                    )
                    await session.update_network_metrics(metrics)

            elif msg_type == "end_session":
                if session:
                    await session_manager.destroy(session.session_id)
                    session = None
                    await websocket.send_json({"type": "session_ended"})

            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as exc:
        logger.error("WebSocket handler error: %s", exc)
    finally:
        if session:
            await session_manager.destroy(session.session_id)


async def _binary_audio_loop(websocket: WebSocket, session: VoiceSession) -> None:
    """
    Separate coroutine that reads binary audio frames from the WebSocket
    and feeds them into the session's audio pipeline.

    Note: FastAPI WebSocket can only be read from one coroutine at a time;
    this runs alongside the text loop because FastAPI internally serialises
    reads. In production use two WebSockets or multiplex via a framing protocol.

    For simplicity here we poll the bytes channel; the demo SDK handles
    keeping audio frames in the binary stream.
    """
    try:
        while True:
            try:
                audio_bytes = await asyncio.wait_for(
                    websocket.receive_bytes(), timeout=5.0
                )
                await session.ingest_audio(audio_bytes)
            except asyncio.TimeoutError:
                continue
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as exc:
        logger.debug("Binary audio loop ended: %s", exc)
