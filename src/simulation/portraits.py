"""Generate immutable, synthetic case portraits."""

import io
import json
import os
import re
import secrets
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import database

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / "workflows" / "patient_portrait_api.json"
MODEL_NAME = "Comfy-Org/z_image_turbo@6fc90a3b1b653e935a0d175e260736de25b84df5"
WORKFLOW_VERSION = 1


def portrait_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(ROOT / "data"))) / "portraits"


def build_prompt(case: dict) -> str:
    # The presentation supplies only broad fictional demographics, never a source image.
    presentation = case.get("presentation", "")
    age = re.search(r"\b(\d{1,3})[- ]year[- ]old\b", presentation, re.IGNORECASE)
    person = "adult"
    if age and 18 <= int(age.group(1)) <= 100:
        person = f"{age.group(1)}-year-old adult"
    gender = re.search(r"\b(man|woman|male|female)\b", presentation, re.IGNORECASE)
    if gender:
        person += f", {gender.group(1).lower()}"
    return (
        f"Photorealistic head-and-shoulders portrait of a fictional {person} in a clinical "
        "simulation. One person, centered, facing camera, neutral everyday clothing, "
        "plain softly lit background, natural skin texture, attentive expression. "
        "No medical equipment, injuries, text, logo, or identifiable real person."
    )


def workflow_for(prompt: str, seed: int) -> dict:
    workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    workflow["27"]["inputs"]["text"] = prompt
    workflow["3"]["inputs"]["seed"] = seed
    return workflow


def _webp_bytes(raw: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width < 256 or image.height < 256 or image.width > 2048 or image.height > 2048:
                raise ValueError("Portrait dimensions are outside the allowed range")
            output = io.BytesIO()
            image.convert("RGB").save(output, format="WEBP", quality=85)
            return output.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("ComfyUI returned an invalid image") from exc


def generate_case_portrait(case_id: str, client, seed: int | None = None) -> dict:
    case = database.case_record(case_id)
    if not case:
        raise ValueError(f"Case {case_id} was not found")
    if not re.fullmatch(r"[\w-]{1,100}", case_id):
        raise ValueError("Invalid case ID")
    seed = seed if seed is not None else secrets.randbits(63)
    if not 0 <= seed < 2**63:
        raise ValueError("Seed must fit in a signed 64-bit integer")
    prompt = build_prompt(case)
    raw = client.generate(workflow_for(prompt, seed))
    encoded = _webp_bytes(raw)
    asset_id = uuid.uuid4().hex
    relative_path = f"{case_id}/v{case['version']}/{asset_id}.webp"
    target = portrait_dir() / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(target)
    try:
        database.save_portrait_asset(
            asset_id, case_id, case["version"], relative_path, seed, MODEL_NAME, WORKFLOW_VERSION
        )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {"asset_id": asset_id, "file_path": relative_path, "case_version": case["version"]}
