"""Serverless ComfyUI worker for synthetic case portraits.

Run `modal run modal_portraits.py::download_models` once, then
`modal run modal_portraits.py::generate_missing_local` to generate local cases.
"""

import modal

app = modal.App("medsimulation-portraits")
data_volume = modal.Volume.from_name("medsimulation-data", create_if_missing=True)
model_volume = modal.Volume.from_name("medsimulation-portrait-models", create_if_missing=True)

COMFYUI_REVISION = "08ff3c11b3a85eb07c2c06f21cb378ac2e086840"
MODEL_REVISION = "6fc90a3b1b653e935a0d175e260736de25b84df5"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git")
    .run_commands(
        "git clone https://github.com/Comfy-Org/ComfyUI.git /opt/ComfyUI",
        f"cd /opt/ComfyUI && git checkout {COMFYUI_REVISION}",
        "pip install -r /opt/ComfyUI/requirements.txt",
    )
    .pip_install(
        "httpx>=0.27", "pillow>=11.0", "sqlalchemy>=2.0",
        "psycopg[binary]>=3.2", "langchain-core>=0.3.0",
    )
    .add_local_dir("src", remote_path="/root/src")
    .add_local_dir("workflows", remote_path="/root/workflows")
)

download_image = modal.Image.debian_slim(python_version="3.12").pip_install("huggingface_hub>=0.23")


@app.function(image=download_image, volumes={"/models": model_volume}, timeout=1800)
def download_models():
    from huggingface_hub import hf_hub_download

    for part in (
        "text_encoders/qwen_3_4b.safetensors",
        "diffusion_models/z_image_turbo_bf16.safetensors",
        "vae/ae.safetensors",
    ):
        hf_hub_download(
            repo_id="Comfy-Org/z_image_turbo",
            filename=f"split_files/{part}",
            revision=MODEL_REVISION,
            local_dir="/models",
        )
    model_volume.commit()


@app.cls(
    image=image,
    gpu="A10G",
    volumes={"/data": data_volume, "/models": model_volume},
    timeout=600,
    scaledown_window=300,
)
class PortraitGenerator:
    @modal.enter()
    def start_comfyui(self):
        import os
        import subprocess
        import time

        import httpx

        os.environ["DATABASE_PATH"] = "/data/medsim.db"
        os.environ["DATA_DIR"] = "/data"
        self.process = subprocess.Popen(
            [
                "python",
                "/opt/ComfyUI/main.py",
                "--listen",
                "127.0.0.1",
                "--port",
                "8188",
                "--extra-model-paths-config",
                "/root/workflows/comfyui_extra_model_paths.yaml",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        for _ in range(120):
            if self.process.poll() is not None:
                raise RuntimeError("ComfyUI exited during startup")
            try:
                if httpx.get("http://127.0.0.1:8188/system_stats", timeout=2).is_success:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise TimeoutError("ComfyUI did not start")

    @modal.method()
    def render(self, workflow: dict) -> bytes:
        from src.simulation.comfyui_client import ComfyUIClient

        return ComfyUIClient("http://127.0.0.1:8188").generate(workflow)

    @modal.method()
    def generate(self, case_id: str, seed: int = -1):
        from src.simulation import database
        from src.simulation.comfyui_client import ComfyUIClient
        from src.simulation.portraits import generate_case_portrait

        database.init_db()
        result = generate_case_portrait(
            case_id,
            ComfyUIClient("http://127.0.0.1:8188"),
            seed=None if seed < 0 else seed,
        )
        data_volume.commit()
        return result

    @modal.method()
    def generate_missing(self, limit: int = 0):
        import time

        from src.simulation import database
        from src.simulation.comfyui_client import ComfyUIClient
        from src.simulation.portraits import cases_missing_portraits, generate_case_portrait

        database.init_db()
        pending = cases_missing_portraits(limit)
        print(f"Generating {len(pending)} missing hosted portraits", flush=True)
        client = ComfyUIClient("http://127.0.0.1:8188")
        failures = []
        started = time.monotonic()
        for number, case_id in enumerate(pending, 1):
            for attempt in range(2):
                try:
                    result = generate_case_portrait(case_id, client)
                    data_volume.commit()
                    print(f"[{number}/{len(pending)}] {case_id}: {result['file_path']}", flush=True)
                    break
                except Exception as exc:
                    if attempt:
                        failures.append(case_id)
                        print(f"[{number}/{len(pending)}] {case_id}: FAILED: {exc}", flush=True)
                    else:
                        print(f"{case_id}: retrying after {exc}", flush=True)
        summary = {
            "requested": len(pending),
            "generated": len(pending) - len(failures),
            "failed": failures,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }
        print(summary, flush=True)
        if failures:
            raise RuntimeError(f"Portrait generation failed for: {', '.join(failures)}")
        return summary

    @modal.exit()
    def stop_comfyui(self):
        self.process.terminate()
        self.process.wait(timeout=10)


class RemoteRenderer:
    def __init__(self):
        self.worker = PortraitGenerator()

    def generate(self, workflow: dict) -> bytes:
        return self.worker.render.remote(workflow)


@app.local_entrypoint()
def main(case_id: str, seed: int = -1):
    print(PortraitGenerator().generate.remote(case_id, seed))


@app.local_entrypoint()
def generate_missing_hosted(limit: int = 0):
    print(PortraitGenerator().generate_missing.remote(limit))


@app.local_entrypoint()
def generate_local(case_id: str, seed: int = -1):
    from src.simulation import database
    from src.simulation.portraits import generate_case_portrait

    database.init_db()
    result = generate_case_portrait(
        case_id, RemoteRenderer(), seed=None if seed < 0 else seed
    )
    print(result)


@app.local_entrypoint()
def generate_missing_local(limit: int = 0):
    import time

    from src.simulation import database
    from src.simulation.portraits import cases_missing_portraits, generate_case_portrait

    database.init_db()
    pending = cases_missing_portraits(limit)
    print(f"Generating {len(pending)} missing local portraits", flush=True)
    renderer = RemoteRenderer()
    failures = []
    started = time.monotonic()
    for number, case_id in enumerate(pending, 1):
        for attempt in range(2):
            try:
                item_started = time.monotonic()
                result = generate_case_portrait(case_id, renderer)
                elapsed = time.monotonic() - item_started
                print(
                    f"[{number}/{len(pending)}] {case_id}: {result['file_path']} ({elapsed:.1f}s)",
                    flush=True,
                )
                break
            except Exception as exc:
                if attempt:
                    failures.append(case_id)
                    print(f"[{number}/{len(pending)}] {case_id}: FAILED: {exc}", flush=True)
                else:
                    print(f"{case_id}: retrying after {exc}", flush=True)
    print(
        f"Finished in {(time.monotonic() - started) / 60:.1f} min: "
        f"{len(pending) - len(failures)} generated, {len(failures)} failed",
        flush=True,
    )
    if failures:
        raise RuntimeError(f"Portrait generation failed for: {', '.join(failures)}")
