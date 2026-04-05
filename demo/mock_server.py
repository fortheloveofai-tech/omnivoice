#!/usr/bin/env python3
"""
mock_server.py — Zero-dependency mock OmniVoice WebSocket server.

Implements the WebSocket protocol from scratch (stdlib only: socket,
hashlib, base64, struct, threading, json, math, os).

Behaviour
─────────
• Responds to start_session → session_started
• Responds to ping          → pong
• On audio bursts           → simulates ASR + LLM + TTS pipeline:
    1. Sends transcript message (250 ms latency)
    2. Streams 8 × binary audio chunks (TTS output — audible sine tone)
• Demonstrates realistic latency budgets and conversation flow

Run
───
    python3 demo/mock_server.py

Then open demo/index.html in your browser and click Connect.
"""

import base64
import hashlib
import json
import math
import os
import socket
import struct
import threading
import time
import random


# ── Config ──────────────────────────────────────────────────────────────────

HOST = "0.0.0.0"
PORT = 8000
SAMPLE_RATE = 16000   # Hz — matches demo capture rate
CHUNK_SAMPLES = 1600  # 100 ms of audio per chunk
CHUNK_BYTES = CHUNK_SAMPLES * 2  # PCM-16 → 2 bytes/sample

# Simulated conversation pairs (user utterance → assistant reply)
CONVERSATIONS = [
    ("Hello, I'd like to test the voice pipeline.",
     "Hello! I'm the Omni Voice mock assistant. The pipeline is working. "
     "I can hear you clearly through the WebSocket connection."),
    ("What can you tell me about the latency?",
     "The target end-to-end latency is under 300 milliseconds. "
     "That covers ASR transcription, LLM token generation, and TTS synthesis."),
    ("How does the barge-in feature work?",
     "Barge-in lets you interrupt me mid-sentence. The ATTS scheduler detects "
     "voice activity and immediately cancels the current TTS playback."),
    ("Tell me about the Temporal Alignment Buffer.",
     "The TAB rate-shapes bursty LLM token streams into a steady phoneme flow "
     "for the TTS engine, using a leaky-bucket with a monotonic timestamp cursor."),
]
_conv_idx = 0
_conv_lock = threading.Lock()


def next_conversation():
    global _conv_idx
    with _conv_lock:
        pair = CONVERSATIONS[_conv_idx % len(CONVERSATIONS)]
        _conv_idx += 1
    return pair


# ── PCM-16 tone generator ────────────────────────────────────────────────────

def make_tone(freq_hz: float, duration_s: float,
              sample_rate: int = SAMPLE_RATE, amplitude: float = 0.25) -> bytes:
    """Generate a pure sine wave as signed 16-bit PCM."""
    n = int(sample_rate * duration_s)
    out = bytearray(n * 2)
    for i in range(n):
        sample = amplitude * math.sin(2 * math.pi * freq_hz * i / sample_rate)
        val = int(sample * 32767)
        struct.pack_into('<h', out, i * 2, val)
    return bytes(out)


def tts_audio_for(text: str) -> bytes:
    """Return ~1-3 s of synthetic speech audio for the given text."""
    # Approximate speaking duration: ~150 wpm → ~0.4 s/word
    words = len(text.split())
    duration = max(1.0, min(words * 0.35, 8.0))
    # Use a chord (root + 5th) to sound slightly speech-like
    base = 220.0
    tone_a = make_tone(base, duration)
    tone_b = make_tone(base * 1.5, duration, amplitude=0.12)
    # Mix
    n = len(tone_a) // 2
    mixed = bytearray(n * 2)
    for i in range(n):
        a = struct.unpack_from('<h', tone_a, i * 2)[0]
        b = struct.unpack_from('<h', tone_b, i * 2)[0]
        val = max(-32768, min(32767, a + b))
        struct.pack_into('<h', mixed, i * 2, val)
    return bytes(mixed)


# ── WebSocket frame codec ────────────────────────────────────────────────────

OPCODE_TEXT   = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE  = 0x8
OPCODE_PING   = 0x9
OPCODE_PONG   = 0xA


def _encode_frame(opcode: int, data: bytes) -> bytes:
    """Encode a single, unmasked WebSocket frame (server→client).
    WebSocket spec §5.2 requires network (big-endian) byte order for
    extended-length fields, so all multi-byte lengths use '!' prefix.
    """
    fin_op = 0x80 | opcode
    length = len(data)
    if length < 126:
        header = struct.pack('!BB', fin_op, length)
    elif length < 65536:
        header = struct.pack('!BBH', fin_op, 126, length)
    else:
        header = struct.pack('!BBQ', fin_op, 127, length)
    return header + data


def send_text(conn, obj):
    """Serialise dict to JSON and send as a text frame."""
    payload = json.dumps(obj).encode()
    conn.sendall(_encode_frame(OPCODE_TEXT, payload))


def send_binary(conn, data: bytes):
    """Send raw bytes as a binary frame."""
    conn.sendall(_encode_frame(OPCODE_BINARY, data))


def recv_frame(conn):
    """
    Read exactly one frame from the socket.
    Returns (opcode, payload_bytes) or raises ConnectionError on close/error.
    """
    def recv_exact(n):
        buf = b''
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("socket closed")
            buf += chunk
        return buf

    hdr = recv_exact(2)
    # fin = (hdr[0] & 0x80) != 0  # not needed for mock
    opcode = hdr[0] & 0x0F
    masked = (hdr[1] & 0x80) != 0
    length = hdr[1] & 0x7F

    if length == 126:
        length = struct.unpack('!H', recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack('!Q', recv_exact(8))[0]

    mask_key = recv_exact(4) if masked else b'\x00\x00\x00\x00'
    payload = bytearray(recv_exact(length))

    if masked:
        for i in range(length):
            payload[i] ^= mask_key[i % 4]

    return opcode, bytes(payload)


# ── HTTP → WebSocket upgrade ─────────────────────────────────────────────────

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def do_handshake(conn):
    """
    Read the HTTP upgrade request, send the 101 Switching Protocols response.
    Returns True on success.
    """
    request = b''
    while b'\r\n\r\n' not in request:
        chunk = conn.recv(4096)
        if not chunk:
            return False
        request += chunk

    lines = request.decode(errors='replace').split('\r\n')
    headers = {}
    for line in lines[1:]:
        if ':' in line:
            k, _, v = line.partition(':')
            headers[k.strip().lower()] = v.strip()

    key = headers.get('sec-websocket-key', '')
    if not key:
        return False

    accept = base64.b64encode(
        hashlib.sha1((key + WS_MAGIC).encode()).digest()
    ).decode()

    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    conn.sendall(response.encode())
    return True


# ── Pipeline simulation ───────────────────────────────────────────────────────

def simulate_pipeline(conn, session_id: str):
    """
    Called once the client starts sending audio.
    Simulates ASR → LLM → TTS with realistic timing.
    """
    user_text, assistant_text = next_conversation()

    # ── ASR: transcript arrives after ~200 ms
    time.sleep(0.20)
    print(f"  [{session_id[:8]}] transcript → \"{user_text[:40]}…\"")
    send_text(conn, {"type": "transcript", "text": user_text,
                     "is_final": True, "confidence": 0.97})

    # ── LLM: thinking starts, first token at ~150 ms
    time.sleep(0.15)

    # ── TTS: stream audio in ~100-ms chunks
    audio = tts_audio_for(assistant_text)
    chunk_size = CHUNK_BYTES
    total = len(audio)
    sent = 0

    print(f"  [{session_id[:8]}] tts → {total} bytes ({len(assistant_text.split())} words)")
    while sent < total:
        end = min(sent + chunk_size, total)
        send_binary(conn, audio[sent:end])
        sent = end
        time.sleep(0.08)  # 80 ms between chunks ≈ real-time

    # Signal end of assistant turn
    send_text(conn, {"type": "assistant_turn_complete",
                     "text": assistant_text})


# ── Per-connection handler ───────────────────────────────────────────────────

def handle_client(conn, addr):
    print(f"[+] {addr[0]}:{addr[1]} — handshaking")
    try:
        if not do_handshake(conn):
            print(f"[-] {addr[0]}:{addr[1]} — bad handshake")
            return
        print(f"[+] {addr[0]}:{addr[1]} — WebSocket open")

        session_id = None
        audio_buf = bytearray()
        silence_timer = None
        pipeline_running = False
        pipeline_lock = threading.Lock()

        def maybe_trigger_pipeline():
            nonlocal audio_buf, pipeline_running, silence_timer
            with pipeline_lock:
                if pipeline_running:
                    return
                if len(audio_buf) < CHUNK_BYTES * 3:
                    return
                pipeline_running = True
                audio_buf.clear()

            t = threading.Thread(target=_run_pipeline, daemon=True)
            t.start()

        def _run_pipeline():
            nonlocal pipeline_running
            try:
                simulate_pipeline(conn, session_id or "unknown")
            except Exception as e:
                print(f"  pipeline error: {e}")
            finally:
                with pipeline_lock:
                    pipeline_running = False

        def schedule_pipeline():
            nonlocal silence_timer
            if silence_timer:
                silence_timer.cancel()
            silence_timer = threading.Timer(0.4, maybe_trigger_pipeline)
            silence_timer.start()

        while True:
            opcode, payload = recv_frame(conn)

            if opcode == OPCODE_CLOSE:
                conn.sendall(_encode_frame(OPCODE_CLOSE, b''))
                break

            if opcode == OPCODE_PING:
                conn.sendall(_encode_frame(OPCODE_PONG, payload))
                continue

            if opcode == OPCODE_BINARY:
                # Audio PCM frame
                audio_buf.extend(payload)
                schedule_pipeline()
                continue

            if opcode == OPCODE_TEXT:
                try:
                    msg = json.loads(payload.decode())
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "start_session":
                    session_id = f"sess-{os.urandom(4).hex()}"
                    print(f"  [{session_id[:8]}] session started")
                    send_text(conn, {
                        "type": "session_started",
                        "session_id": session_id,
                        "server": "OmniVoice MockServer v0.1",
                        "asr": "MockASR",
                        "llm": "MockLLM",
                        "tts": "MockTTS",
                    })

                elif msg_type == "ping":
                    send_text(conn, {"type": "pong"})

                elif msg_type == "network_metrics":
                    # Acknowledge quietly
                    pass

                elif msg_type == "end_session":
                    send_text(conn, {"type": "session_ended"})
                    break

                else:
                    print(f"  unknown msg type: {msg_type!r}")

    except ConnectionError:
        pass
    except Exception as e:
        print(f"[-] {addr[0]}:{addr[1]} error: {e}")
    finally:
        if 'silence_timer' in dir() and silence_timer:
            silence_timer.cancel()
        conn.close()
        print(f"[-] {addr[0]}:{addr[1]} — disconnected")


# ── Main server loop ─────────────────────────────────────────────────────────

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(16)

    print("╔══════════════════════════════════════════════════════════╗")
    print("║  OmniVoice Mock Server — zero-dependency WebSocket mock  ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  Listening on  ws://localhost:{PORT}/ws/voice               ║")
    print("║                                                          ║")
    print("║  Open demo/index.html → click Connect → speak!          ║")
    print("║  Ctrl+C to stop                                          ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    try:
        while True:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client,
                                 args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n[*] Shutting down.")
    finally:
        server.close()


if __name__ == "__main__":
    main()
