"""
MedSimulation — Modal All-in-One Deployment

Deploy the entire application (frontend + backend + LLM) on Modal with a single command.

Usage:
    modal deploy modal_app.py

This creates a persistent endpoint at:
    https://<workspace>--medsimulation-serve.modal.run

"""

import modal
from modal import App, Image, Volume, Secret

# ── App Definition ─────────────────────────────────────────────────────────────

app = modal.App("medsimulation")

# ── Volume for persisting cases and data ───────────────────────────────────────

# Persistent volume for case data, database, and imaging files
data_volume = modal.Volume.from_name("medsimulation-data", create_if_missing=True)

# ── Image Definition ───────────────────────────────────────────────────────────

# Base image with all dependencies
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "git",
        "curl",
        "libxml2-dev",
        "libxslt-dev",
    )
    .pip_install(
        # Core dependencies
        "fastapi>=0.128.4",
        "uvicorn[standard]>=0.30.0",
        "jinja2>=3.1.6",
        "pydantic>=2.12.0",
        "python-dotenv>=1.0.0",
        "python-multipart>=0.0.22",
        "openai>=1.40",
        "httpx>=0.27",
        "pydicom>=2.4.0",
        "beautifulsoup4>=4.12.3",
        "fpdf2>=2.7.9",
        "langchain-core>=0.3.0",
        # vLLM and GPU dependencies
        "vllm>=0.6.0",
        "bitsandbytes>=0.46.1",
        "transformers>=4.40.0",
        "accelerate>=0.27.0",
        "sentencepiece>=0.2.0",
    )
)

# ── Alternative: Lighter image (use Together AI for LLM) ───────────────────────

image_cpu = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "curl", "libxml2-dev", "libxslt-dev")
    .pip_install(
        "fastapi>=0.128.4",
        "uvicorn[standard]>=0.30.0",
        "jinja2>=3.1.6",
        "pydantic>=2.12.0",
        "python-dotenv>=1.0.0",
        "python-multipart>=0.0.22",
        "openai>=1.40",
        "httpx>=0.27",
        "pydicom>=2.4.0",
        "beautifulsoup4>=4.12.3",
        "fpdf2>=2.7.9",
        "langchain-core>=0.3.0",
    )
)


# ── GPU Function (vLLM + FastAPI combined) ─────────────────────────────────────

@app.function(
    gpu="T4",  # Options: "T4" ($0.35/hr), "A10G" ($0.60/hr), "A100" ($1.30/hr)
    container_idle_timeout=300,  # Shut down after 5 min of inactivity
    timeout=600,  # Max request timeout
    allow_concurrent_inputs=50,
    volumes={"/data": data_volume},
    image=image,
    secrets=[
        Secret.from_name("medsimulation-secrets", required=False),
        Secret.from_dotenv(".env.modal", required=False),
    ],
)
@modal.asgi_app()
def serve():
    """
    All-in-one MedSimulation server.

    Starts vLLM as a subprocess and runs the FastAPI backend.
    Everything accessible from a single URL.
    """
    import subprocess
    import os
    import time
    import socket
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # ── Configuration ──────────────────────────────────────────────────────────

    MODEL = os.getenv("VLLM_MODEL", "google/medgemma-4b-it")
    VLLM_PORT = 8001
    GPU_MEMORY = float(os.getenv("VLLM_GPU_MEMORY", "0.7"))
    MAX_MODEL_LEN = int(os.getenv("VLLM_MAX_MODEL_LEN", "4096"))

    # ── Start vLLM server as subprocess ────────────────────────────────────────

    logger.info("Starting vLLM server with model: %s", MODEL)

    vllm_cmd = [
        "vllm", "serve", MODEL,
        "--host", "0.0.0.0",
        "--port", str(VLLM_PORT),
        "--gpu-memory-utilization", str(GPU_MEMORY),
        "--max-model-len", str(MAX_MODEL_LEN),
        "--trust-remote-code",
        "--dtype", "bfloat16",
    ]

    # Add quantization if enabled
    quantization = os.getenv("VLLM_QUANTIZATION", "bitsandbytes")
    if quantization and quantization.lower() != "none":
        vllm_cmd.extend([
            "--quantization", "bitsandbytes",
            "--load-format", "bitsandbytes",
        ])

    logger.info("vLLM command: %s", " ".join(vllm_cmd))
    vllm_proc = subprocess.Popen(vllm_cmd)

    # ── Wait for vLLM to be ready ──────────────────────────────────────────────

    def wait_for_vllm(timeout=180):
        """Wait until vLLM is responding to health checks."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                result = sock.connect_ex(("localhost", VLLM_PORT))
                sock.close()
                if result == 0:
                    # Port is open, do HTTP health check
                    import httpx
                    resp = httpx.get(f"http://localhost:{VLLM_PORT}/models", timeout=5)
                    if resp.status_code == 200:
                        logger.info("vLLM is ready!")
                        return True
            except Exception:
                pass
            logger.info("Waiting for vLLM to start... (%.0fs)", time.time() - start)
            time.sleep(5)
        return False

    if not wait_for_vllm():
        logger.error("vLLM failed to start within timeout")
        vllm_proc.terminate()
        raise RuntimeError("vLLM failed to start")

    # ── Set environment for FastAPI backend ────────────────────────────────────

    os.environ["VLLM_MODE"] = "local"
    os.environ["VLLM_LOCAL_URL"] = f"http://localhost:{VLLM_PORT}/v1"
    os.environ["VLLM_MODEL"] = MODEL

    # Set data directory to mounted volume
    os.environ["DATA_DIR"] = "/data"

    # ── Import and return FastAPI app ──────────────────────────────────────────

    # Change to the app directory so imports work
    import sys
    from pathlib import Path

    app_dir = Path(__file__).parent
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))

    # Set up data directories
    (app_dir / "data").mkdir(exist_ok=True)
    (app_dir / "data" / "imaging").mkdir(exist_ok=True)
    (app_dir / "data" / "imaging" / "dicom").mkdir(exist_ok=True)

    from main import app as backend_app

    logger.info("MedSimulation backend ready")
    logger.info("Access at: https://<workspace>--medsimulation-serve.modal.run")

    return backend_app


# ── CPU-Only Function (use Together AI or other cloud LLM) ─────────────────────

@app.function(
    gpu=None,  # No GPU needed - uses cloud LLM
    container_idle_timeout=300,
    timeout=300,
    allow_concurrent_inputs=50,
    volumes={"/data": data_volume},
    image=image_cpu,
    secrets=[
        Secret.from_name("medsimulation-secrets", required=False),
        Secret.from_dotenv(".env.modal", required=False),
    ],
)
@modal.asgi_app()
def serve_cpu():
    """
    CPU-only deployment using Together AI or other cloud LLM provider.

    Cheaper option (~$0.05/hr + token costs) but requires API key.
    """
    import os
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Validate cloud configuration
    vllm_mode = os.getenv("VLLM_MODE", "cloud")
    vllm_cloud_url = os.getenv("VLLM_CLOUD_URL")
    vllm_cloud_key = os.getenv("VLLM_CLOUD_API_KEY")

    if vllm_mode == "cloud" and not vllm_cloud_url:
        raise ValueError(
            "VLLM_MODE=cloud but VLLM_CLOUD_URL not set. "
            "Set VLLM_CLOUD_URL and VLLM_CLOUD_API_KEY in .env.modal"
        )

    os.environ["VLLM_MODE"] = vllm_mode

    import sys
    from pathlib import Path

    app_dir = Path(__file__).parent
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))

    (app_dir / "data").mkdir(exist_ok=True)
    (app_dir / "data" / "imaging").mkdir(exist_ok=True)
    (app_dir / "data" / "imaging" / "dicom").mkdir(exist_ok=True)

    from main import app as backend_app

    logger.info("MedSimulation CPU backend ready (cloud LLM mode)")

    return backend_app


# ── CLI Helpers ────────────────────────────────────────────────────────────────

@app.local_entrypoint()
def main(
    cpu: bool = False,
    model: str = "google/medgemma-4b-it",
    gpu_memory: float = 0.7,
    max_model_len: int = 4096,
):
    """
    Deploy MedSimulation to Modal.

    Examples:
        modal run modal_app.py                    # Deploy GPU version
        modal run modal_app.py --cpu              # Deploy CPU version (cloud LLM)
        modal run modal_app.py --model google/gemma-2b-it
        modal deploy modal_app.py                 # Production deployment
    """
    import os

    # Set deployment-time environment variables
    os.environ["VLLM_MODEL"] = model
    os.environ["VLLM_GPU_MEMORY"] = str(gpu_memory)
    os.environ["VLLM_MAX_MODEL_LEN"] = str(max_model_len)

    if cpu:
        logger = modal.logging.getLogger(__name__)
        logger.info("Deploying CPU version (cloud LLM mode)")
        # CPU deployment requires .env.modal with cloud config
        return serve_cpu.web_url
    else:
        logger = modal.logging.getLogger(__name__)
        logger.info(f"Deploying GPU version with model: {model}")
        return serve.web_url
