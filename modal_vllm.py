"""
MedSimulation — Standalone vLLM Server on Modal

Deploy MedGemma 4B as a standalone OpenAI-compatible vLLM server.
Use this if you want a separate LLM endpoint paired with a CPU-only web app.

Usage:
    # One-time model pre-cache (run this first!):
    modal run modal_app.py::download_model

    # Deploy standalone server:
    modal deploy modal_vllm.py

Then set in the web app environment:
    VLLM_MODE=cloud
    VLLM_CLOUD_URL=https://your-username--medsimulation-vllm-server.modal.run/v1
    VLLM_CLOUD_API_KEY=your-modal-api-key
    VLLM_MODEL=google/medgemma-4b-it

Cold-start optimisations applied here:
  - Model weights from Volume (no HF download on cold start)
  - --enforce-eager  → disables CUDA graph capture (~20 s saved)
  - --disable-torch-compile → skips JIT compilation (~75 s saved)
  - container_idle_timeout=300  (unchanged — cost-conscious)
"""

import modal
from modal import App, Image, Secret, Volume, gpu

app = App("medsimulation-vllm")

# Shared model volume — populated by `modal run modal_app.py::download_model`
model_volume = Volume.from_name("medsimulation-models", create_if_missing=True)

image = (
    Image.debian_slim(python_version="3.11")
    .apt_install("git", "curl", "wget")
    .pip_install(
        "vllm>=0.6",
        "torch",
        "transformers",
        "accelerate",
        "huggingface_hub>=0.23.0",
        "asgi-proxy-lib",
    )
    # HF_HOME → model volume so HuggingFace libraries use cached weights
    .env({"HF_HOME": "/models/hf_cache"})
)

# A10G is required for bfloat16 (MedGemma). T4 does not support bfloat16.
GPU = gpu.A10G


@app.function(
    image=image,
    gpu=GPU,
    timeout=600,                    # 10 min max per request
    allow_concurrent_inputs=10,
    container_idle_timeout=300,     # Spin down after 5 min idle (cost-conscious)
    volumes={"/models": model_volume},
    secrets=[Secret.from_name("huggingface", required=False)],
)
@modal.asgi_app()
def vllm_server():
    """vLLM OpenAI-compatible API server serving MedGemma 4B."""
    import os
    import subprocess
    import socket
    import time
    import pathlib
    from asgi_proxy import asgi_proxy

    # Find a free port
    sock = socket.socket()
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()

    model_id = os.getenv("MODEL_NAME", "google/medgemma-4b-it")
    model_name = model_id.split("/")[-1]
    local_path = f"/models/{model_name}"

    # Use pre-downloaded weights if available; otherwise fall back to HF download
    model_source = local_path if pathlib.Path(local_path).exists() else model_id
    print(f"Starting vLLM server | model: {model_source} | port: {port}")

    cmd = [
        "python", "-m", "vllm.entrypoints.openai.api_server",
        "--host", "localhost",
        "--port", str(port),
        "--model", model_source,
        "--trust-remote-code",
        "--gpu-memory-utilization", "0.85",
        "--max-model-len", "4096",
        "--dtype", "bfloat16",
        "--enforce-eager",           # Disable CUDA graph capture → faster startup
        "--disable-torch-compile",   # Skip JIT compilation → faster startup
        "--max-num-batched-tokens", "4096",
    ]

    proc = subprocess.Popen(cmd)

    # Poll until vLLM health endpoint responds (up to 5 min)
    import httpx
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://localhost:{port}/health", timeout=5)
            if r.status_code == 200:
                print("vLLM server ready!")
                break
        except Exception:
            pass
        time.sleep(5)
    else:
        proc.terminate()
        raise RuntimeError("vLLM failed to start within 5 minutes")

    return asgi_proxy(f"http://localhost:{port}")


@app.local_entrypoint()
def main():
    """Deploy with: modal deploy modal_vllm.py"""
    print("Deploy with:  modal deploy modal_vllm.py")
    print("Access at:    https://your-username--medsimulation-vllm-server.modal.run/v1")
    print()
    print("Tip: run `modal run modal_app.py::download_model` first to pre-cache weights.")
