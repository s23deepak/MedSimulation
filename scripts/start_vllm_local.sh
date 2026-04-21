#!/bin/bash
# Start vLLM locally with same parameters as Modal deployment
#
# Usage: ./scripts/start_vllm_local.sh
#
# Requirements:
#   - GPU with sufficient VRAM (A10G = 24GB, RTX 3090/4090 recommended)
#   - vllm >= 0.6.0 installed
#   - Model weights downloaded (or will download on first run)

set -e

# ── Configuration (matches modal_app.py) ──────────────────────────────────────
MODEL_ID="${VLLM_MODEL:-google/medgemma-4b-it}"
MODEL_NAME=$(basename "$MODEL_ID")
LOCAL_PATH="/models/${MODEL_NAME}"

HOST="${VLLM_HOST:-0.0.0.0}"
PORT="${VLLM_PORT:-8001}"

DTYPE="${VLLM_DTYPE:-bfloat16}"
GPU_MEMORY="${VLLM_GPU_MEMORY:-0.8}"
MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-4096}"
MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-16}"
MAX_NUM_BATCHED_TOKENS="${VLLM_MAX_BATCHED_TOKENS:-4096}"
SERVED_MODEL_NAME="${VLLM_SERVED_MODEL_NAME:-medgemma}"

# ── Determine model source ────────────────────────────────────────────────────
if [ -d "$LOCAL_PATH" ]; then
    MODEL_SOURCE="$LOCAL_PATH"
    echo "Using local model: $MODEL_SOURCE"
else
    MODEL_SOURCE="$MODEL_ID"
    echo "Model not found locally, will download from HuggingFace: $MODEL_SOURCE"
fi

# ── Build vLLM command ────────────────────────────────────────────────────────
# Matches modal_app.py exactly
VLLM_CMD=(
    vllm serve "$MODEL_SOURCE"
    --host "$HOST"
    --port "$PORT"
    --dtype "$DTYPE"
    --gpu-memory-utilization "$GPU_MEMORY"
    --max-model-len "$MAX_MODEL_LEN"
    --enforce-eager
    --disable-log-stats
    --max-num-seqs "$MAX_NUM_SEQS"
    --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS"
    --served-model-name "$SERVED_MODEL_NAME"
    --disable-custom-all-reduce
    --enable-prefix-caching
)

# ── Set environment (remove problematic TORCH_LOGS) ───────────────────────────
export NCCL_DEBUG=WARN
unset TORCH_LOGS  # Remove if set - causes invalid setting errors

# ── Print and execute ─────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "Starting vLLM locally (Modal-equivalent configuration)"
echo "═══════════════════════════════════════════════════════════════"
echo "Model:      $MODEL_SOURCE"
echo "Host:Port:  $HOST:$PORT"
echo "Dtype:      $DTYPE"
echo "GPU Memory: $GPU_MEMORY"
echo "Max Len:    $MAX_MODEL_LEN"
echo "Max Seqs:   $MAX_NUM_SEQS"
echo "Served As:  $SERVED_MODEL_NAME"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "vLLM command:"
echo "  ${VLLM_CMD[*]}"
echo ""
echo "Health check:"
echo "  curl http://localhost:$PORT/health"
echo ""
echo "Test completion:"
echo "  curl http://localhost:$PORT/v1/chat/completions \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"model\":\"$SERVED_MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"max_tokens\":16}'"
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Run vLLM
exec "${VLLM_CMD[@]}"
