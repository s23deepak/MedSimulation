"""Generate immutable, synthetic case portraits."""

import io
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import database

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / "workflows" / "patient_portrait_api.json"
MODEL_NAME = "Comfy-Org/z_image_turbo@6fc90a3b1b653e935a0d175e260736de25b84df5"
WORKFLOW_VERSION = 1
PROMPT_VERSION = 2
logger = logging.getLogger(__name__)

APPEARANCE_PROFILES = (
    "deep brown skin and dark coiled hair",
    "light skin and brown wavy hair",
    "medium brown skin and dark curly hair",
    "olive skin and dark wavy hair",
    "tan skin and dark straight hair",
    "dark brown skin and short curly hair",
)


def portrait_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(ROOT / "data"))) / "portraits"


def cases_missing_portraits(limit: int = 0) -> list[str]:
    pending = []
    for case in database.list_db_cases():
        if case["status"] == "rejected" or (case["source"] == "dicom" and case["status"] != "approved"):
            continue
        case_id, version = case["case_id"], case["version"]
        candidates = (
            database.active_portrait(case_id, version),
            database.latest_portrait(case_id, version),
        )
        if any(
            asset and asset["prompt_version"] == PROMPT_VERSION
            and (portrait_dir() / asset["file_path"]).is_file()
            for asset in candidates
        ):
            continue
        pending.append(case_id)
        if limit > 0 and len(pending) >= limit:
            break
    return pending


def portrait_spec(case: dict) -> dict:
    presentation = case.get("presentation", "")
    text = f"{presentation} {case.get('title', '')}"
    match = re.search(r"\b(\d{1,3})[- ]year[- ]old\b", text, re.IGNORECASE)
    age = int(match.group(1)) if match and 1 <= int(match.group(1)) <= 100 else None
    gender = re.search(r"\b(boy|girl|man|woman|male|female)\b", text, re.IGNORECASE)
    label = gender.group(1).lower() if gender else "person"
    if age is not None:
        if age < 13:
            label = {"male": "boy", "man": "boy", "female": "girl", "woman": "girl"}.get(label, label)
            if label == "person":
                label = "child"
        elif age < 18 and label == "person":
            label = "teenager"
        label = f"{age}-year-old {label}"
    explicit = re.search(
        r"\b(Black African|East Asian|South Asian|Middle Eastern|Black|African|Asian|White|Latino|Hispanic)\b",
        presentation, re.IGNORECASE,
    )
    if explicit:
        appearance = explicit.group(1)
    else:
        case_key = case.get("case_id", "")
        index = int.from_bytes(hashlib.sha256(case_key.encode()).digest()[:4], "big")
        appearance = APPEARANCE_PROFILES[index % len(APPEARANCE_PROFILES)]
    return {"age": age, "person": label, "appearance": appearance}


def build_prompt(case: dict) -> str:
    spec = portrait_spec(case)
    age_cue = " Clearly age-appropriate child facial proportions." if spec["age"] is not None and spec["age"] < 13 else ""
    return (
        f"Photorealistic head-and-shoulders portrait of a fictional {spec['person']} "
        f"with {spec['appearance']} in a clinical "
        "simulation. One person, centered, facing camera, neutral everyday clothing, "
        "plain softly lit background, natural skin texture, attentive expression."
        f"{age_cue} "
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
    spec = portrait_spec(case)
    prompt = build_prompt(case)
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    started = time.monotonic()
    asset_id = None
    target = None
    status = "error"
    error_type = None
    try:
        raw = client.generate(workflow_for(prompt, seed))
        encoded = _webp_bytes(raw)
        asset_id = uuid.uuid4().hex
        relative_path = f"{case_id}/v{case['version']}/{asset_id}.webp"
        target = portrait_dir() / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(encoded)
        temporary.replace(target)
        database.save_portrait_asset(
            asset_id, case_id, case["version"], relative_path, seed, MODEL_NAME,
            WORKFLOW_VERSION, prompt_hash, spec["age"], spec["appearance"], PROMPT_VERSION,
        )
        status = "success"
        return {"asset_id": asset_id, "file_path": relative_path, "case_version": case["version"]}
    except Exception as exc:
        error_type = type(exc).__name__
        if target is not None:
            target.unlink(missing_ok=True)
        raise
    finally:
        try:
            database.record_model_run(
                "portrait", "patient_portrait", PROMPT_VERSION, prompt_hash, MODEL_NAME,
                status, int((time.monotonic() - started) * 1000), case_id=case_id,
                asset_id=asset_id if status == "success" else None, error_type=error_type,
            )
        except Exception:
            logger.exception("Could not record portrait generation telemetry")
