# MedSimulation - Production Dockerfile
# Deployable on Railway, Fly.io, or any Docker host with an LLM backend
#
# Modes:
#   - local: vLLM + MedGemma (requires GPU)
#   - cloud: External LLM API (Together AI, OpenAI, etc.)

FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy project files
COPY pyproject.toml uv.lock ./
COPY README.md ./
COPY main.py ./
COPY templates/ ./templates/
COPY static/ ./static/
COPY src/ ./src/
RUN mkdir -p ./data/imaging
COPY docker-entrypoint.sh ./docker-entrypoint.sh

# Make entrypoint executable
RUN chmod +x ./docker-entrypoint.sh

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Create virtual environment and install dependencies.
# Cloud inference uses the small CPU image; local vLLM builds opt into GPU packages.
ARG MEDSIM_EXTRAS=""
RUN uv venv && if [ "$MEDSIM_EXTRAS" = "gpu" ]; then uv sync --frozen --no-dev --extra gpu; else uv sync --frozen --no-dev; fi

# Expose ports (FastAPI + vLLM)
EXPOSE 8000 8001

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8000
ENV VLLM_PORT=8001

# Health check (checks FastAPI, which depends on vLLM)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'))" || exit 1

# Use entrypoint script to start vLLM + FastAPI
ENTRYPOINT ["./docker-entrypoint.sh"]
