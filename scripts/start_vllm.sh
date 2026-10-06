#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# MedSimulation — Start vLLM server locally
#
# Requirements:
#   - NVIDIA GPU with ≥8GB VRAM (tested on RTX 5060)
#   - vLLM 0.16.x and bitsandbytes installed
#   - HuggingFace access to google/medgemma-4b-it
#
# Usage:
#   ./scripts/start_vllm.sh              # default: medgemma-4b-it on port 8001
#   ./scripts/start_vllm.sh --port 8002  # custom port
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

MODEL="${VLLM_MODEL:-google/medgemma-4b-it}"
PORT="${VLLM_PORT:-8001}"
MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-1024}"
GPU_UTIL="${VLLM_GPU_UTIL:-0.85}"
QUANTIZATION="${VLLM_QUANTIZATION:-bitsandbytes}"
VLLM_BIN="$(command -v vllm || true)"
ZIG_CC="$(dirname "${VLLM_BIN}")/zig-cc"

if [[ -z "${VLLM_BIN}" ]]; then
    cat >&2 <<'EOF'
vllm is not installed or not on PATH.

For the tested 8 GB local setup, install:
  uv pip install --python .llm-venv/bin/python \
    'vllm==0.16.0' 'bitsandbytes==0.49.2' 'ziglang==0.16.0'
EOF
    exit 1
fi

PYTHON_BIN="$(dirname "${VLLM_BIN}")/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="python3"
fi

echo "╔═══════════════════════════════════════════════════════╗"
echo "║  MedSimulation — vLLM Server                          ║"
echo "╠═══════════════════════════════════════════════════════╣"
echo "║  Model:   ${MODEL}"
echo "║  Port:    ${PORT}"
echo "║  MaxLen:  ${MAX_MODEL_LEN}"
echo "║  GPU Mem: ${GPU_UTIL}"
echo "║  Quant:   ${QUANTIZATION}"
echo "╚═══════════════════════════════════════════════════════╝"

ARGS=(
    serve "${MODEL}"
    --host 127.0.0.1
    --port "${PORT}"
    --dtype bfloat16
    --max-model-len "${MAX_MODEL_LEN}"
    --gpu-memory-utilization "${GPU_UTIL}"
    --trust-remote-code
    --enforce-eager
    --max-num-seqs 1
    --disable-log-stats
    --limit-mm-per-prompt '{"image":0}'
)

case "${QUANTIZATION}" in
    ""|none|off)
        ;;
    bitsandbytes)
        if ! "${PYTHON_BIN}" -c "import bitsandbytes" >/dev/null 2>&1; then
            cat >&2 <<'EOF'
bitsandbytes is required for VLLM_QUANTIZATION=bitsandbytes.

Install the tested local GPU dependency with:
  uv pip install --python .llm-venv/bin/python 'bitsandbytes==0.49.2'

Or disable quantization for this run:
  VLLM_QUANTIZATION=none bash scripts/start_vllm.sh
EOF
            exit 1
        fi

        ARGS+=(
            --allow-deprecated-quantization
            --quantization bitsandbytes
            --load-format bitsandbytes
        )
        ;;
    *)
        echo "Unsupported VLLM_QUANTIZATION='${QUANTIZATION}'. Use 'bitsandbytes' or 'none'." >&2
        exit 1
        ;;
esac

if [[ -z "${CC:-}" && -x "${ZIG_CC}" ]]; then
    export CC="${ZIG_CC}"
fi

exec "${VLLM_BIN}" "${ARGS[@]}" "$@"
