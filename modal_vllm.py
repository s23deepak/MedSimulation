"""
Deploy MedGemma 4B on Modal with vLLM
Pay-per-use GPU: spins up on request, spins down after 5 min idle
Cost: ~$0.03 per case simulation (~$2-5/month for moderate usage)

Usage:
    modal deploy modal_vllm.py

Then set in Railway/Render:
    VLLM_MODE=cloud
    VLLM_CLOUD_URL=https://your-username--medsimulation-vllm-server.modal.run/v1
    VLLM_CLOUD_API_KEY=your-modal-api-key
    VLLM_MODEL=google/medgemma-4b-it
"""

import modal
from modal import App, Image, Secret, gpu

# Create Modal app
app = modal.App("medsimulation-vllm")

# Image with vLLM and MedGemma dependencies
image = (
    Image.debian_slim(python_version="3.11")
    .apt_install("git", "curl", "wget")
    .pip_install(
        "vllm>=0.6",
        "torch",
        "transformers",
        "accelerate",
    )
)

# GPU: T4 is cheapest, A10G is faster
GPU = gpu.T4


@app.function(
    image=image,
    gpu=GPU,
    timeout=600,  # 10 min max per request
    allow_concurrent_inputs=10,
    container_idle_timeout=300,  # Spin down after 5 min idle
    secrets=[Secret.from_name("huggingface", required=False)],
)
@modal.asgi_app()
def vllm_server():
    """vLLM OpenAI-compatible server serving MedGemma 4B."""
    import os
    import subprocess
    import socket
    import time
    from asgi_proxy import asgi_proxy

    # Find open port
    sock = socket.socket()
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()

    # Start vLLM server
    model = os.getenv("MODEL_NAME", "google/medgemma-4b-it")
    cmd = [
        "python", "-m", "vllm.entrypoints.openai.api_server",
        "--host", "localhost",
        "--port", str(port),
        "--model", model,
        "--trust-remote-code",
        "--gpu-memory-utilization", "0.9",
        "--max-model-len", "4096",
        "--dtype", "float16",
        "--enforce-eager",
    ]

    print(f"Starting vLLM server on port {port} with model {model}...")
    proc = subprocess.Popen(cmd)

    # Wait for server to start
    time.sleep(15)
    print("vLLM server ready!")

    # Proxy requests to vLLM
    return asgi_proxy(f"http://localhost:{port}")


@app.local_entrypoint()
def main():
    """Run locally for testing."""
    print("Deploy with: modal deploy modal_vllm.py")
    print("Then access at: https://your-username--medsimulation-vllm-server.modal.run/v1")
