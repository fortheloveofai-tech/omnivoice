"""
Prometheus metrics for all pipeline stages.

Exported metrics match the catalogue defined in cycle_04 observability guide.
Each metric is registered once at module import and shared across all sessions.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

# ── Histograms (latency) ──────────────────────────────────────────────────────

LATENCY_BUCKETS = (5, 10, 20, 50, 100, 150, 200, 300, 500, 1000)

asr_decode_latency = Histogram(
    "asr_decode_latency_ms",
    "Latency from audio chunk receipt to first ASR transcript fragment",
    buckets=LATENCY_BUCKETS,
)

llm_token_latency = Histogram(
    "llm_token_generation_latency_ms",
    "Per-token LLM generation latency",
    buckets=LATENCY_BUCKETS,
)

tts_synthesis_latency = Histogram(
    "tts_synthesis_latency_ms",
    "Latency from token receipt to audio frame ready",
    buckets=LATENCY_BUCKETS,
)

call_round_trip_latency = Histogram(
    "call_round_trip_latency_ms",
    "Full user-utterance to first audio response latency",
    buckets=LATENCY_BUCKETS,
)

# ── Gauges (state) ────────────────────────────────────────────────────────────

tab_buffer_occupancy = Gauge(
    "tab_buffer_occupancy_tokens",
    "Number of tokens currently queued in the Temporal Alignment Buffer",
)

tab_buffered_seconds = Gauge(
    "tab_buffered_seconds",
    "Seconds of audio currently queued in TAB",
)

tab_backpressure = Gauge(
    "tab_backpressure",
    "1 if TAB back-pressure is active, 0 otherwise",
)

aqal_bucket_fill = Gauge(
    "aqal_token_bucket_fill_pct",
    "AQAL token bucket fill fraction (0-1)",
)

aqal_codec = Gauge(
    "aqal_codec_bitrate_kbps",
    "Current codec bitrate selected by AQAL",
)

active_sessions = Gauge(
    "active_voice_sessions",
    "Number of currently active voice sessions",
)

# ── Counters ──────────────────────────────────────────────────────────────────

tokens_generated = Counter(
    "llm_tokens_generated_total",
    "Total LLM tokens generated across all sessions",
)

audio_bytes_sent = Counter(
    "tts_audio_bytes_sent_total",
    "Total audio bytes sent to clients",
)

barge_in_count = Counter(
    "atts_barge_in_total",
    "Total number of barge-in interrupts",
)

errors_total = Counter(
    "pipeline_errors_total",
    "Total pipeline errors",
    ["stage"],  # label: asr | llm | tts | transport
)


def start_metrics_server(port: int = 9090) -> None:
    """Start the Prometheus HTTP metrics exporter."""
    start_http_server(port)
