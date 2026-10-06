#!/bin/bash
# MedSimulation Docker Entrypoint
# Starts vLLM server (if configured) then runs FastAPI backend

set -e

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  MedSimulation — Starting                                 ║"
echo "╚═══════════════════════════════════════════════════════════╝"

# Read environment variables
VLLM_MODE="${VLLM_MODE:-local}"
VLLM_MODEL="${VLLM_MODEL:-google/medgemma-4b-it}"
VLLM_PORT="${VLLM_PORT:-8001}"
VLLM_GPU_MEMORY="${VLLM_GPU_MEMORY:-0.7}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-4096}"
VLLM_QUANTIZATION="${VLLM_QUANTIZATION:-bitsandbytes}"
PORT="${PORT:-8000}"

echo "Configuration:"
echo "  VLLM_MODE: $VLLM_MODE"
echo "  PORT: $PORT"
echo ""

# Function to wait for vLLM to be ready
wait_for_vllm() {
    echo "Waiting for vLLM server to start..."
    local max_attempts=60
    local attempt=0
    while [ $attempt -lt $max_attempts ]; do
        if curl -s "http://localhost:${VLLM_PORT}/models" > /dev/null 2>&1; then
            echo "✓ vLLM server is ready"
            return 0
        fi
        attempt=$((attempt + 1))
        echo "  Waiting... (attempt $attempt/$max_attempts)"
        sleep 3
    done
    echo "✗ vLLM failed to start within timeout"
    return 1
}

# Start vLLM if in local mode
if [ "$VLLM_MODE" = "local" ]; then
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║  Starting vLLM Server                                     ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo "  Model: $VLLM_MODEL"
    echo "  Port: $VLLM_PORT"
    echo "  GPU Memory: $VLLM_GPU_MEMORY"
    echo "  Max Length: $VLLM_MAX_MODEL_LEN"
    echo ""

    # Build vLLM command
    VLLM_CMD=(
        vllm serve "$VLLM_MODEL"
        --host 0.0.0.0
        --port "$VLLM_PORT"
        --gpu-memory-utilization "$VLLM_GPU_MEMORY"
        --max-model-len "$VLLM_MAX_MODEL_LEN"
        --trust-remote-code
        --dtype bfloat16
    )

    # Add quantization if enabled
    if [ "$VLLM_QUANTIZATION" = "bitsandbytes" ]; then
        VLLM_CMD+=(--quantization bitsandbytes --load-format bitsandbytes)
    fi

    echo "Command: ${VLLM_CMD[*]}"
    echo ""

    # Start vLLM in background
    "${VLLM_CMD[@]}" &
    VLLM_PID=$!

    # Wait for vLLM to be ready
    if ! wait_for_vllm; then
        echo "Failed to start vLLM. Exiting."
        exit 1
    fi

    # Set environment for FastAPI
    export VLLM_MODE=local
    export VLLM_LOCAL_URL="http://localhost:${VLLM_PORT}/v1"
    export VLLM_MODEL="$VLLM_MODEL"

elif [ "$VLLM_MODE" = "cloud" ]; then
    echo "Cloud LLM mode enabled"
    if [ -z "$VLLM_CLOUD_URL" ]; then
        echo "WARNING: VLLM_CLOUD_URL not set. Cloud mode may fail."
    fi

elif [ "$VLLM_MODE" = "modal" ]; then
    echo "Modal LLM mode enabled"
else
    echo "Unsupported VLLM_MODE: $VLLM_MODE. Choose local, cloud, or modal."
    exit 1
fi

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  Starting FastAPI Backend                                 ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo "  Port: $PORT"
echo "  Host: 0.0.0.0"
echo ""

# Start FastAPI backend
exec python main.py --host 0.0.0.0 --port "${PORT:-8000}"
