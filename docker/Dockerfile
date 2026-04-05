# ──────────────────────────────────────────────────────────────────
#  Omni Voice Platform — Production Docker image
#  Multi-stage build for a lean final image.
# ──────────────────────────────────────────────────────────────────

# ── Stage 1: build dependencies ─────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for audio processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev libssl-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install --prefix=/install . && \
    pip install --prefix=/install uvicorn[standard]

# ── Stage 2: runtime ─────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime-only system libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source
COPY omni_voice/ ./omni_voice/

# Non-root user for security
RUN useradd -m -u 1000 omnivoice && chown -R omnivoice:omnivoice /app
USER omnivoice

EXPOSE 8000 9090

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "omni_voice.main"]
