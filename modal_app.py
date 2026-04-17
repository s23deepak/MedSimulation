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
        # vLLM and GPU dependencies
        "vllm>=0.6.0",
        "bitsandbytes>=0.46.1",
        "transformers>=4.40.0",
        "accelerate>=0.27.0",
        "sentencepiece>=0.2.0",
        # Used by download_model()
        "huggingface_hub>=0.23.0",
    )
    # HF_HOME → model volume so weights are cached there automatically
    .env({"HF_HOME": "/models/hf_cache"})
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
    .add_local_file("data/medsim.db", remote_path="/root/seed_medsim.db")
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

@app.cls(
    gpu="A10G",            # bfloat16 requires compute capability ≥ 8.0 (A10G = 8.6)
    image=image,
    scaledown_window=300,  # Shut down after 5 min idle
    timeout=600,
    enable_memory_snapshot=True,   # Required for snap=True in @modal.enter
    volumes={
        "/data": data_volume,
        "/models": model_volume,
    },
    secrets=[
        Secret.from_name("medsimulation-secrets"),
        Secret.from_dotenv(".env.modal"),
    ],
)
@modal.concurrent(max_inputs=50)
class VLLMService:
    """
    In-process vLLM AsyncLLMEngine running as a Modal class.

    Using @modal.enter(snap=True) means:
      - On the FIRST cold start: load_model() runs fully, then Modal snapshots GPU state.
      - On SUBSEQUENT cold starts: Modal restores the snapshot, skipping load_model().
        This reduces the restart from ~5 min → ~5-15 s.
    """

    @modal.enter(snap=True)
    def load_model(self):
        """
        Initialise the vLLM AsyncLLMEngine.
        Called once before the GPU snapshot is taken; subsequent boots restore from snapshot.
        """
        import os
        import asyncio

        # Ensure vLLM can detect the GPU — Modal sets this but vLLM's subprocess
        # inspection may not see it without explicit declaration.
        # CRITICAL: Set BEFORE importing vLLM so subprocess inherits it.
        os.environ["CUDA_VISIBLE_DEVICES"] = "0"

        # Clear any vLLM-related env vars that might confuse detection
        os.environ.pop("VLLM_LOCAL_URL", None)
        os.environ.pop("VLLM_MODE", None)

        model_id = os.getenv("VLLM_MODEL", "google/medgemma-4b-it")
        model_name = model_id.split("/")[-1]
        local_path = f"/models/{model_name}"

        # Prefer the pre-downloaded local copy; fall back to HF download if missing
        import pathlib
        model_source = local_path if pathlib.Path(local_path).exists() else model_id

        print(f"Loading vLLM engine from: {model_source}")

        from vllm import AsyncLLMEngine, AsyncEngineArgs

        engine_args = AsyncEngineArgs(
            model=model_source,
            dtype="bfloat16",              # Required for Gemma3 / MedGemma
            gpu_memory_utilization=float(os.getenv("VLLM_GPU_MEMORY", "0.85")),
            max_model_len=int(os.getenv("VLLM_MAX_MODEL_LEN", "4096")),
            trust_remote_code=True,
            # ── Fast-boot flags: skip JIT that's already paid for in the snapshot ──
            enforce_eager=True,            # Disable CUDA graph capture (~20 s saved)
            # torch.compile is off by default in vLLM ≥ 0.6; leave it that way
            max_num_batched_tokens=4096,
        )

        # Store on self so it survives across requests
        self.engine = AsyncLLMEngine.from_engine_args(engine_args)

        # Store model name for health responses
        self.model_id = model_id

        print("vLLM engine ready!")

    @modal.method()
    async def generate(
        self,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        request_id: str | None = None,
    ) -> str:
        """Generate a completion for `prompt`. Returns the full output text."""
        import uuid
        from vllm import SamplingParams

        sampling_params = SamplingParams(
            max_tokens=max_tokens,
            temperature=temperature,
        )
        req_id = request_id or str(uuid.uuid4())

        output_text = ""
        async for request_output in self.engine.generate(prompt, sampling_params, req_id):
            if request_output.finished:
                output_text = request_output.outputs[0].text

        return output_text

    @modal.method()
    async def health(self) -> dict:
        """Quick liveness check — returns model name and status."""
        return {"status": "ok", "model": self.model_id}


# ── Web Application (FastAPI + VLLMService) ─────────────────────────────────────

@app.function(
    gpu="A10G",
    scaledown_window=300,  # Shut down after 5 min idle
    timeout=600,
    volumes={
        "/data": data_volume,
        "/models": model_volume,
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

    # ── Seed DB from image if volume is empty ──────────────────────────────────
    import shutil
    vol_db = Path("/data/medsim.db")
    seed_db = Path("/root/seed_medsim.db")
    if (not vol_db.exists() or vol_db.stat().st_size == 0) and seed_db.exists() and seed_db.stat().st_size > 0:
        shutil.copy2(str(seed_db), str(vol_db))
        logger.info("Seeded production DB from image (%d bytes)", vol_db.stat().st_size)

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
    )
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
        return serve_cpu.web_url
    else:
        print(f"Deploying GPU version with model: {model}")
        return serve.web_url
