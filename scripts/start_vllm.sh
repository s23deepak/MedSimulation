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

echo "╔═══════════════════════════════════════════════════════╗"
echo "║  MedSimulation — vLLM Server                         ║"
echo "╠═══════════════════════════════════════════════════════╣"
echo "║  Model:   ${MODEL}"
echo "║  Port:    ${PORT}"
echo "║  MaxLen:  ${MAX_MODEL_LEN}"
echo "║  GPU Mem: ${GPU_UTIL}"
echo "╚═══════════════════════════════════════════════════════╝"

exec vllm serve "${MODEL}" \
    --port "${PORT}" \
    --dtype float16 \
    --max-model-len "${MAX_MODEL_LEN}" \
    --gpu-memory-utilization "${GPU_UTIL}" \
    --trust-remote-code \
    "$@"
