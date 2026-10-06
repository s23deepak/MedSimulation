"""
MedSimulation — Modal All-in-One Deployment

Deploy the entire application (frontend + backend + LLM) on Modal with a single command.

Usage:
    # One-time model pre-cache (run after first deploy):
    modal run modal_app.py::download_model

    # Deploy (production):
    modal deploy modal_app.py

This creates a persistent endpoint at:
    https://<workspace>--medsimulation-serve.modal.run

Cold-start optimisation summary
────────────────────────────────
• Model weights live in a Modal Volume → zero HF download time after first run
• GPU memory snapshot (experimental) → restores pre-loaded engine in ~5 s
• vLLM runs as AsyncLLMEngine inside VLLMService (@app.cls) with snap=True
• torch.compile and CUDA graphs disabled → faster first-boot before snapshot exists
"""

import modal
from modal import App, Image, Volume, Secret

# ── App Definition ──────────────────────────────────────────────────────────────
# experimental_options enables GPU memory snapshots (currently alpha on Modal).
# After the first full cold start, Modal snapshots the GPU state so that
# subsequent boots restore in ~5-10 s instead of ~5 min.
# If your Modal tier/region does not support this yet, Modal will simply ignore
# the flag and fall back to a normal cold start — it is safe to leave enabled.
app = App("medsimulation")

# ── Volumes ─────────────────────────────────────────────────────────────────────

# Persistent volume for case data, database, and imaging files
data_volume = Volume.from_name("medsimulation-data", create_if_missing=True)

# Persistent volume that stores pre-downloaded model weights.
# Populated once by `modal run modal_app.py::download_model`.
# All GPU containers mount this so they never re-download from HuggingFace.
model_volume = Volume.from_name("medsimulation-models", create_if_missing=True)

# ── Image Definition ────────────────────────────────────────────────────────────

# HF_HOME points into the model volume so that HuggingFace libraries
# automatically use cached weights without any extra code.
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
        "sqlalchemy>=2.0",
        "psycopg[binary]>=3.2",
        # vLLM and GPU dependencies
        "vllm>=0.6.0",
        "bitsandbytes>=0.46.1",
        "transformers>=4.40.0",
        "accelerate>=0.27.0",
        "sentencepiece>=0.2.0",
        # Used by download_model()
        "huggingface_hub>=0.23.0",
        "requests>=2.31.0",  # For vLLM sleep/wake/warmup helpers
    )
    # HF_HOME → model volume so weights are cached there automatically
    .env({
        "HF_HOME": "/models/hf_cache",
        "HF_XET_HIGH_PERFORMANCE": "1",
    })
    .run_commands([
        "mkdir -p /root/data/imaging/dicom",
        "mkdir -p /root/.cache/vllm",
    ])
    # Note: VLLM_* env vars are set per-function, not at image level,
    # to avoid vLLM warnings about unknown environment variables
    # add_local_* must come LAST — Modal injects these at container start, not build time
    .add_local_dir("src", remote_path="/root/src")
    .add_local_dir("templates", remote_path="/root/templates")
    .add_local_dir("static", remote_path="/root/static")
    .add_local_file("main.py", remote_path="/root/main.py")
)

# ── One-time Model Download ──────────────────────────────────────────────────────

@app.function(
    image=image,
    gpu="A10G",
    volumes={
        "/models": model_volume,
    },
    secrets=[
        Secret.from_name("medsimulation-secrets"),
    ],
    timeout=1800,  # 30 min — large model download
)
def download_model(model_id: str = "google/medgemma-4b-it"):
    """
    One-time setup: download model weights into the model volume.

    Run with:
        modal run modal_app.py::download_model

    This only needs to be re-run if you change the model.
    The downloaded weights are shared by all GPU containers via the volume.
    """
    import os
    from huggingface_hub import snapshot_download

    # Destination inside the volume (not the HF cache, so it's directly usable)
    dest = f"/models/{model_id.split('/')[-1]}"
    os.makedirs(dest, exist_ok=True)

    print(f"Downloading {model_id} → {dest} ...")
    snapshot_download(
        repo_id=model_id,
        local_dir=dest,
        # Skip large binary formats we don't need for vLLM
        ignore_patterns=["*.msgpack", "*.h5", "flax_model*", "tf_model*"],
    )
    # Flush changes to the persistent volume
    model_volume.commit()
    print(f"Done! Model cached at {dest}")
    print("You can now deploy: modal deploy modal_app.py")


# ── VLLMService ─────────────────────────────────────────────────────────────────

VLLM_PORT = 8001


def wait_for_vllm(port: int = VLLM_PORT, timeout: int = 300):
    """Wait for vLLM server to be ready."""
    import socket
    import time
    start = time.time()
    while time.time() - start < timeout:
        try:
            socket.create_connection(("localhost", port), timeout=1).close()
            return True
        except (socket.error, ConnectionRefusedError):
            time.sleep(0.5)
    raise TimeoutError("vLLM server did not start")


def warmup_vllm(port: int = VLLM_PORT):
    """Run warmup request to capture JIT state."""
    import requests
    # Use the served model name
    payload = {
        "model": "medgemma",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 16,
    }
    try:
        resp = requests.post(
            f"http://localhost:{port}/v1/chat/completions",
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        print(f"Warmup complete: {resp.json()['choices'][0]['message']['content'][:50]}")
    except Exception as e:
        print(f"Warmup failed: {e}")


def sleep_vllm(port: int = VLLM_PORT, level: int = 1):
    """Put vLLM to sleep (offload weights to CPU)."""
    import requests
    # Try v1 endpoint first, fall back to root endpoint
    for endpoint in [f"/v1/sleep?level={level}", f"/sleep?level={level}"]:
        try:
            resp = requests.post(f"http://localhost:{port}{endpoint}", timeout=30)
            if resp.status_code == 200:
                print("vLLM entered sleep mode")
                return
        except Exception as e:
            pass
    print("Sleep mode not available (this is OK)")


def wake_vllm(port: int = VLLM_PORT):
    """Wake vLLM from sleep."""
    import requests
    # Try v1 endpoint first, fall back to root endpoint
    for endpoint in ["/v1/wake_up", "/wake_up"]:
        try:
            resp = requests.post(f"http://localhost:{port}{endpoint}", timeout=30)
            if resp.status_code == 200:
                print("vLLM woke up")
                return
        except Exception as e:
            pass
    print("Wake called (sleep mode may not be active)")


@app.cls(
    gpu="A10G",
    image=image,
    scaledown_window=300,
    timeout=600,
    volumes={
        "/data": data_volume,
        "/models": model_volume,
    },
    secrets=[
        Secret.from_name("medsimulation-secrets"),
        Secret.from_dotenv(".env.modal"),
    ],
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
)
@modal.concurrent(max_inputs=50)
class VLLMService:
    """
    vLLM server running as a subprocess with sleep mode for GPU snapshots.
    """

    @modal.enter(snap=True)
    def start(self):
        """Start vLLM server and warmup for snapshot."""
        import os
        import subprocess

        model_id = os.getenv("VLLM_MODEL", "google/medgemma-4b-it")
        model_name = model_id.split("/")[-1]
        local_path = f"/models/{model_name}"

        import pathlib
        model_source = local_path if pathlib.Path(local_path).exists() else model_id

        print(f"Starting vLLM serve from: {model_source}")

        cmd = [
            "vllm", "serve",
            model_source,
            "--host", "0.0.0.0",
            "--port", str(VLLM_PORT),
            "--dtype", "bfloat16",
            "--gpu-memory-utilization", os.getenv("VLLM_GPU_MEMORY", "0.8"),
            "--max-model-len", os.getenv("VLLM_MAX_MODEL_LEN", "4096"),
            "--enforce-eager",
            "--disable-log-stats",
            "--max-num-seqs", "16",
            "--max-num-batched-tokens", "4096",
            "--served-model-name", "medgemma",
            "--disable-custom-all-reduce",  # Reduces NCCL noise
            "--enable-prefix-caching",      # RadixAttention for KV cache reuse
            "--enable-sleep-mode",          # Required for GPU memory snapshots
        ]

        env = {
            **os.environ,
            # Required for sleep mode (GPU snapshot support)
            "VLLM_SERVER_DEV_MODE": "1",
            # Disables the NCCL heartbeat monitor thread that spams
            # "Failed to check should_dump flag" and TCPStore broken pipe errors
            "TORCH_NCCL_ENABLE_MONITORING": "0",
            # Suppress NCCL logs entirely (single-GPU mode doesn't need distributed)
            "NCCL_DEBUG": "ERROR",
            "NCCL_DEBUG_SUBSYS": "NONE",
            # Suppress PyTorch distributed warnings
            "TORCH_DISTRIBUTED_DEBUG": "OFF",
            # Suppress vLLM Python-level logs below WARNING
            "VLLM_LOGGING_LEVEL": "WARNING",
            # Suppress HuggingFace tokenizer parallelism fork warnings
            "TOKENIZERS_PARALLELISM": "false",
        }

        # Explicitly remove noisy inherited env vars
        env.pop("TORCH_LOGS", None)

        print(f"vLLM cmd: {' '.join(cmd)}")
        self.vllm_proc = subprocess.Popen(cmd, env=env, stderr=subprocess.DEVNULL)
        wait_for_vllm(VLLM_PORT, timeout=300)
        print("vLLM server ready")
        warmup_vllm(VLLM_PORT)
        print("vLLM warmup complete")
        sleep_vllm(VLLM_PORT, level=1)
        print("vLLM entered sleep mode - snapshot will be created")

    @modal.enter(snap=False)
    def wake_up(self):
        """Ensure vLLM is ready after snapshot restore."""
        # Wake vLLM from sleep mode first
        wake_vllm(VLLM_PORT)
        # Then verify server is responsive
        wait_for_vllm(VLLM_PORT, timeout=60)
        print("vLLM ready after restore")

    @modal.method()
    async def generate(
        self,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        request_id: str | None = None,
    ) -> str:
        """Generate via HTTP to vLLM serve.

        Default max_tokens increased to 1024 for long-form outputs like debriefs.
        """
        import aiohttp
        payload = {
            "model": "medgemma",
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stop": ["USER:", "RESIDENT:", "DOCTOR:", "PATIENT:", "ASSISTANT:"],
            "repetition_penalty": 1.1,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://localhost:{VLLM_PORT}/v1/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data["choices"][0]["text"]

    @modal.method()
    async def chat(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        request_id: str | None = None,
    ) -> str:
        """Chat completion via HTTP to vLLM serve.

        Default max_tokens increased to 1024 for long-form outputs like debriefs.
        """
        import aiohttp
        payload = {
            "model": "medgemma",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stop": ["USER:", "RESIDENT:", "DOCTOR:", "PATIENT:", "ASSISTANT:"],
            "repetition_penalty": 1.1,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://localhost:{VLLM_PORT}/v1/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data["choices"][0]["message"]["content"]

    @modal.method()
    async def health(self) -> dict:
        """Health check via HTTP."""
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://localhost:{VLLM_PORT}/health",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return {"status": "ok" if resp.status == 200 else "unhealthy"}

    # NOTE: No @modal.exit() handler - vLLM process must stay alive for GPU snapshot
    # Modal will clean up the process automatically when the container is destroyed


# ── Web Application (FastAPI + VLLMService) ─────────────────────────────────────

@app.function(
    gpu=None,  # No GPU needed - just proxies to VLLMService
    cpu=2,     # 2 vCPUs sufficient for FastAPI proxy
    memory=1024,  # 1GB RAM
    scaledown_window=300,  # Shut down after 5 min idle
    timeout=600,
    volumes={
        "/data": data_volume,  # Only need data volume for DB/imaging
    },
    image=image,
    secrets=[
        Secret.from_name("medsimulation-secrets"),
        Secret.from_dotenv(".env.modal"),
    ],
)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def serve():
    """
    All-in-one MedSimulation server.

    vLLM runs inside VLLMService (above) — no subprocess needed.
    We inject a ModalVLLMClient into vllm_client._injected_client BEFORE
    importing main.py so that VLLMClient.from_env() picks it up automatically
    when VLLM_MODE=modal.
    """
    import os
    import sys
    import logging
    from pathlib import Path

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("modal_app")

    # ── Set env vars before any src imports ────────────────────────────────────
    os.environ["VLLM_MODE"] = "modal"
    os.environ["VLLM_MODEL"] = os.getenv("VLLM_MODEL", "google/medgemma-4b-it")
    os.environ["APP_ENV"] = "production"
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("Modal hosting requires DATABASE_URL pointing to Postgres")
    # Use the correct env var name and correct filename
    os.environ["DATABASE_PATH"] = "/data/medsim.db"
    os.environ["DATA_DIR"] = "/data"

    # ── Source tree setup ───────────────────────────────────────────────────────
    app_dir = Path("/root")
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
    os.chdir("/root")

    (app_dir / "data").mkdir(exist_ok=True)
    (app_dir / "data" / "imaging").mkdir(exist_ok=True)
    (app_dir / "data" / "imaging" / "dicom").mkdir(exist_ok=True)

    # ── Inject Modal-native vLLM client ────────────────────────────────────────
    # This must happen BEFORE importing main so from_env() sees the injected client.
    import src.simulation.vllm_client as _vc
    from src.simulation.vllm_client import ModalVLLMClient

    _svc = VLLMService()   # Modal class handle — lightweight, no model loaded here
    _vc._injected_client = ModalVLLMClient(
        vllm_service_instance=_svc,
        model=os.environ["VLLM_MODEL"],
    )
    logger.info("ModalVLLMClient injected — vLLM will route via Modal RPC to VLLMService")

    from main import app as backend_app

    logger.info("MedSimulation backend ready")
    logger.info("Access at: https://<workspace>--medsimulation-serve.modal.run")

    return backend_app



# ── Alternative: CPU-Only Deployment (cloud LLM provider) ──────────────────────

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
        "sqlalchemy>=2.0",
        "psycopg[binary]>=3.2",
    )
    .add_local_dir("src", remote_path="/root/src")
    .add_local_dir("templates", remote_path="/root/templates")
    .add_local_dir("static", remote_path="/root/static")
    .add_local_file("main.py", remote_path="/root/main.py")
)


@app.function(
    gpu=None,
    scaledown_window=300,
    timeout=300,
    volumes={"/data": data_volume},
    image=image_cpu,
    secrets=[
        Secret.from_name("medsimulation-secrets"),
        Secret.from_dotenv(".env.modal"),
    ],
)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def serve_cpu():
    """
    CPU-only deployment using Together AI or other cloud LLM provider.
    Cheaper option (~$0.05/hr + token costs) but requires API key.
    """
    import os
    import sys
    import logging
    from pathlib import Path

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    vllm_mode = os.getenv("VLLM_MODE", "cloud")
    vllm_cloud_url = os.getenv("VLLM_CLOUD_URL")

    if vllm_mode == "cloud" and not vllm_cloud_url:
        raise ValueError(
            "VLLM_MODE=cloud but VLLM_CLOUD_URL not set. "
            "Set VLLM_CLOUD_URL and VLLM_CLOUD_API_KEY in .env.modal"
        )

    os.environ["VLLM_MODE"] = vllm_mode
    os.environ["DATA_DIR"] = "/data"
    os.environ["APP_ENV"] = "production"
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("Modal hosting requires DATABASE_URL pointing to Postgres")

    app_dir = Path(__file__).parent
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))

    (app_dir / "data").mkdir(exist_ok=True)
    (app_dir / "data" / "imaging").mkdir(exist_ok=True)
    (app_dir / "data" / "imaging" / "dicom").mkdir(exist_ok=True)

    from main import app as backend_app

    logger.info("MedSimulation CPU backend ready (cloud LLM mode)")

    return backend_app


# ── CLI Helpers ──────────────────────────────────────────────────────────────────

@app.local_entrypoint()
def main(
    cpu: bool = False,
    model: str = "google/medgemma-4b-it",
    gpu_memory: float = 0.85,
    max_model_len: int = 4096,
):
    """
    Deploy MedSimulation to Modal.

    Examples:
        modal run modal_app.py::download_model    # Pre-cache model weights (run first!)
        modal deploy modal_app.py                 # Production deployment
        modal run modal_app.py --cpu              # CPU version (cloud LLM)
        modal run modal_app.py --model google/gemma-2b-it
    """
    import os

    os.environ["VLLM_MODEL"] = model
    os.environ["VLLM_GPU_MEMORY"] = str(gpu_memory)
    os.environ["VLLM_MAX_MODEL_LEN"] = str(max_model_len)

    if cpu:
        print("Deploying CPU version (cloud LLM mode)")
        print("Access at: https://<workspace>--medsimulation-serve-cpu.modal.run")
    else:
        print(f"Deploying GPU version with model: {model}")
        print("Access at: https://<workspace>--medsimulation-serve.modal.run")
