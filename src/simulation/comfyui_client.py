"""Small HTTP adapter for ComfyUI's API-format workflow."""

import time

import httpx


class ComfyUIClient:
    def __init__(self, base_url: str, timeout: int = 180, transport=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def generate(self, workflow: dict) -> bytes:
        deadline = time.monotonic() + self.timeout
        with httpx.Client(base_url=self.base_url, timeout=30, transport=self.transport) as client:
            queued = client.post("/prompt", json={"prompt": workflow})
            queued.raise_for_status()
            prompt_id = queued.json()["prompt_id"]
            while time.monotonic() < deadline:
                result = client.get(f"/history/{prompt_id}")
                result.raise_for_status()
                history = result.json().get(prompt_id)
                if history:
                    status = history.get("status", {})
                    if status.get("status_str") == "error" or status.get("completed") is False:
                        raise RuntimeError("ComfyUI portrait generation failed")
                    images = history.get("outputs", {}).get("9", {}).get("images", [])
                    if not images:
                        raise RuntimeError("ComfyUI returned no portrait")
                    image = images[0]
                    response = client.get(
                        "/view",
                        params={
                            "filename": image["filename"],
                            "subfolder": image.get("subfolder", ""),
                            "type": image.get("type", "output"),
                        },
                    )
                    response.raise_for_status()
                    if len(response.content) > 20 * 1024 * 1024:
                        raise ValueError("ComfyUI image exceeds 20 MiB")
                    return response.content
                time.sleep(1)
        raise TimeoutError("ComfyUI portrait generation timed out")
