#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# MedSimulation — Start vLLM server locally
#
# Requirements:
#   - NVIDIA GPU with ≥8GB VRAM (tested on RTX 5060)
#   - vLLM installed: pip install vllm
#   - HuggingFace access to google/medgemma-4b-it
#
# Usage:
#   ./scripts/start_vllm.sh              # default: medgemma-4b-it on port 8001
#   ./scripts/start_vllm.sh --port 8002  # custom port
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

MODEL="${VLLM_MODEL:-google/medgemma-4b-it}"
PORT="${VLLM_PORT:-8001}"
MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-4096}"
GPU_UTIL="${VLLM_GPU_UTIL:-0.85}"
QUANTIZATION="${VLLM_QUANTIZATION:-bitsandbytes}"
VLLM_BIN="$(command -v vllm || true)"

if [[ -z "${VLLM_BIN}" ]]; then
    echo "vllm is not installed or not on PATH. Run: uv sync --extra gpu" >&2
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
    --port "${PORT}"
    --dtype bfloat16
    --max-model-len "${MAX_MODEL_LEN}"
    --gpu-memory-utilization "${GPU_UTIL}"
    --trust-remote-code
)

case "${QUANTIZATION}" in
    ""|none|off)
        ;;
    bitsandbytes)
        if ! "${PYTHON_BIN}" -c "import bitsandbytes" >/dev/null 2>&1; then
            cat >&2 <<'EOF'
bitsandbytes is required for VLLM_QUANTIZATION=bitsandbytes.

Install GPU dependencies with:
  uv sync --extra gpu

Or install only the missing package with:
  uv add --optional gpu 'bitsandbytes>=0.46.1'

Or disable quantization for this run:
  VLLM_QUANTIZATION=none bash scripts/start_vllm.sh
EOF
            exit 1
        fi

        ARGS+=(
            --quantization bitsandbytes
            --load-format bitsandbytes
        )
        ;;
    *)
        echo "Unsupported VLLM_QUANTIZATION='${QUANTIZATION}'. Use 'bitsandbytes' or 'none'." >&2
        exit 1
        ;;
esac

exec "${VLLM_BIN}" "${ARGS[@]}" "$@"
