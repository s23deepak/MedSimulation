"""
AI case structuring prompt and generator.

This module contains the prompt template used by all case sources
(PubMed, EndlessMedical, Wiley) to transform raw medical text
into structured ClinicalCase JSON.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
import os
from typing import Any
from openai import AsyncOpenAI

from src.simulation.media import generate_patient_portrait

logger = logging.getLogger(__name__)


def _repair_json(raw: str) -> str:
    """
    Best-effort repair of LLM-generated JSON that may contain:
    - // line comments (from the prompt template examples)
    - /* block comments */
    - Trailing commas before } or ]
    - Colon embedded inside key string: "key:" "value" → "key": "value"
    """
    # Strip // line comments (but not inside strings — approximate, handles 99% of cases)
    raw = re.sub(r"//[^\n\"]*", "", raw)
    # Strip /* block comments */
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
    # Remove trailing commas before } or ]
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    # Fix "key:" "value" → "key": "value" (model puts colon inside key string)
    raw = re.sub(r'"([^"]+):"\s*"', r'"\1": "', raw)
    # Extract first {...} block if model prefixed with prose
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        raw = m.group(0)
    return raw.strip()


# ── Case structuring prompt ───────────────────────────────────────────────────

CASE_STRUCTURING_PROMPT = """\
You are a medical education expert creating a structured clinical simulation case.

## Source Material
{source_text}

## Field requirements
- history_data: at least 12 keyword-response pairs covering pain, onset, duration, associated symptoms, medications, allergies, smoking, alcohol, family history, past medical history, social history, review of systems. Responses must be natural first-person layperson language (no medical jargon).
- physical_exam: include General AND all systems relevant to the chief complaint. For neck/spine complaints include "Neck" or "Cervical Spine". For musculoskeletal include "Musculoskeletal". For cardiac include "Cardiovascular". For respiratory include "Respiratory". For abdominal include "Abdomen". For neurological include "Neurological".
- investigations: at least 8 tests with realistic values and reference ranges.
- correct_management: at least 8 specific steps with drug names and doses.
- imaging_studies: include relevant imaging if the case warrants it. Each entry needs study_id, modality (ECG/XR/CT/MRI/US), description, and findings. Leave as empty array if not applicable.

## Output format
Return ONLY a valid JSON object with no comments, no explanation, no markdown fences.

{{
  "case_id": "DYN-{unique_id}",
  "title": "<descriptive title>",
  "specialty": "<specialty>",
  "difficulty": "<beginner|intermediate|advanced>",
  "learning_objectives": ["<objective 1>", "<objective 2>", "<objective 3>", "<objective 4>"],
  "presentation": "<2-3 sentence chief complaint with age, sex, and initial context>",
  "initial_vitals": {{
    "HR": "<value bpm>",
    "BP": "<value mmHg>",
    "RR": "<value /min>",
    "SpO2": "<value %>",
    "Temp": "<value °C>",
    "GCS": "<value>"
  }},
  "history_data": {{
    "pain": "<first-person response>",
    "onset": "<first-person response>",
    "duration": "<first-person response>",
    "associated": "<first-person response>",
    "medications": "<first-person response>",
    "allergies": "<first-person response>",
    "smoking": "<first-person response>",
    "alcohol": "<first-person response>",
    "family history": "<first-person response>",
    "past medical history": "<first-person response>",
    "social history": "<first-person response>",
    "review of systems": "<first-person response>"
  }},
  "physical_exam": {{
    "General": "<findings>"
  }},
  "investigations": {{
    "<test name>": "<realistic result with reference range>"
  }},
  "correct_diagnosis": "<precise diagnosis>",
  "acceptable_diagnoses": ["<alternative 1>", "<alternative 2>"],
  "correct_management": [
    "<step 1 with drug names and doses>",
    "<step 2>",
    "<step 3>"
  ],
  "key_learning_points": [
    "<point 1>",
    "<point 2>",
    "<point 3>",
    "<point 4>",
    "<point 5>"
  ],
  "score_weights": {{
    "history": 20,
    "exam": 20,
    "investigations": 20,
    "diagnosis": 25,
    "management": 15
  }},
  "imaging_studies": []
}}"""


async def generate_case(
    vllm_client: Any,
    source_text: str,
    source_type: str = "custom",
    source_ref: str = "",
) -> dict:
    """
    Use vLLM to transform source text into a structured ClinicalCase dict.

    Parameters
    ----------
    vllm_client : VLLMClient
        The vLLM inference client.
    source_text : str
        Raw medical text (abstract, case report, etc.)
    source_type : str
        Origin: 'pubmed', 'wiley', 'endless_medical', 'custom'
    source_ref : str
        Reference ID (PMID, DOI, etc.)

    Returns
    -------
    dict
        ClinicalCase-compatible dictionary.
    """
    unique_id = uuid.uuid4().hex[:8].upper()
    prompt = CASE_STRUCTURING_PROMPT.format(
        source_text=source_text,
        unique_id=unique_id,
    )

    try:
        if hasattr(vllm_client, "generate_async"):
            raw = await vllm_client.generate_async(
                prompt, temperature=0.3, max_tokens=2000
            )
        elif hasattr(vllm_client, "chat"):
            raw = vllm_client.chat(prompt)
        else:
            raise ValueError("vllm_client has no generate method")

        # Clean up: extract JSON from response
        raw = raw.strip()
        if raw.startswith("```json"):
            raw = raw[7:]
        if raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]

        raw = _repair_json(raw)
        try:
            case_data = json.loads(raw)
        except json.JSONDecodeError as first_err:
            # Last resort: log the bad section and reraise with context
            bad_pos = getattr(first_err, "pos", 0)
            snippet = raw[max(0, bad_pos - 60):bad_pos + 60]
            logger.error("JSON repair failed near: ...%s...", snippet)
            raise

        # Ensure required metadata
        case_data.setdefault("case_id", f"DYN-{unique_id}")
        case_data["_source_type"] = source_type
        case_data["_source_ref"] = source_ref

        # Validate and fill defaults for missing fields
        _ensure_case_quality(case_data)
        
        # Phase E: Generate a DALL-E portrait
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            client = AsyncOpenAI(api_key=openai_key)
            portrait_url = await generate_patient_portrait(client, case_data.get("presentation", ""))
            case_data["patient_image_url"] = portrait_url
        else:
            case_data["patient_image_url"] = ""

        logger.info(
            "Generated case %s from %s source (ref=%s)",
            case_data["case_id"], source_type, source_ref,
        )
        return case_data

    except json.JSONDecodeError as e:
        logger.error("Failed to parse AI-generated case JSON: %s", e)
        raise ValueError(f"AI returned invalid JSON: {e}") from e
    except Exception as e:
        logger.error("Case generation failed: %s", e)
        raise


def _ensure_case_quality(case_data: dict) -> None:
    """Fill in defaults for missing or incomplete fields in generated cases."""
    # Ensure all standard vitals are present
    vitals = case_data.setdefault("initial_vitals", {})
    vitals.setdefault("HR", "Not recorded")
    vitals.setdefault("BP", "Not recorded")
    vitals.setdefault("RR", "Not recorded")
    vitals.setdefault("SpO2", "Not recorded")
    vitals.setdefault("Temp", "Not recorded")
    vitals.setdefault("GCS", "Not recorded")

    # Ensure required list/dict fields exist
    case_data.setdefault("learning_objectives", [])
    case_data.setdefault("presentation", "")
    case_data.setdefault("history_data", {})
    case_data.setdefault("physical_exam", {})
    case_data.setdefault("investigations", {})
    case_data.setdefault("correct_diagnosis", "Unknown")
    case_data.setdefault("acceptable_diagnoses", [])
    case_data.setdefault("correct_management", [])
    case_data.setdefault("key_learning_points", [])
    case_data.setdefault("imaging_studies", [])
    case_data.setdefault("score_weights", {
        "history": 20, "exam": 20, "investigations": 20,
        "diagnosis": 25, "management": 15,
    })

    # Ensure minimum physical exam systems
    pe = case_data["physical_exam"]
    for system in ["General", "Cardiovascular", "Respiratory", "Abdomen",
                    "Neurological", "Musculoskeletal", "Extremities"]:
        pe.setdefault(system, "No specific findings documented for this system.")
