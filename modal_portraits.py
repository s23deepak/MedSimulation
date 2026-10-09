"""Serverless ComfyUI worker for synthetic case portraits.

Run `modal run modal_portraits.py::download_models` once, then
`modal run modal_portraits.py --case-id SIM-001` to generate a portrait.
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
    .pip_install("httpx>=0.27", "pillow>=11.0", "sqlalchemy>=2.0", "psycopg[binary]>=3.2")
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
    def generate(self, case_id: str, seed: int = -1):
        from src.simulation.comfyui_client import ComfyUIClient
        from src.simulation.portraits import generate_case_portrait

        result = generate_case_portrait(
            case_id,
            ComfyUIClient("http://127.0.0.1:8188"),
            seed=None if seed < 0 else seed,
        )
        data_volume.commit()
        return result

    @modal.exit()
    def stop_comfyui(self):
        self.process.terminate()
        self.process.wait(timeout=10)


@app.local_entrypoint()
def main(case_id: str, seed: int = -1):
    print(PortraitGenerator().generate.remote(case_id, seed))
