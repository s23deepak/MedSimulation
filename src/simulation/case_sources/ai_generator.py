"""
AI case structuring prompt and generator.

This module contains the prompt template used by all case sources
(PubMed, EndlessMedical, Wiley) to transform raw medical text
into structured ClinicalCase JSON.
"""

from __future__ import annotations

import json
import logging
import uuid
import os
from typing import Any
from openai import AsyncOpenAI

from src.simulation.media import generate_patient_portrait

logger = logging.getLogger(__name__)


# ── Case structuring prompt ───────────────────────────────────────────────────

CASE_STRUCTURING_PROMPT = """\
You are a medical education expert creating a structured clinical simulation case.

## Source Material
{source_text}

## Instructions
Generate a COMPLETE clinical simulation case as a JSON object with ALL of these fields:

{{
  "case_id": "DYN-{unique_id}",
  "title": "<descriptive title e.g. 'Crushing Chest Pain in a 58-Year-Old Male'>",
  "specialty": "<specialty e.g. 'Emergency Medicine'>",
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
    "<symptom keyword>": "<natural first-person patient response>",
    // Include at least 12 keyword→response pairs covering:
    // pain/symptoms, onset, duration, associated symptoms, medications,
    // allergies, smoking, alcohol, family history, past medical history,
    // social history, review of systems
  }},

  "physical_exam": {{
    "General": "<findings>",
    "Cardiovascular": "<findings>",
    "Respiratory": "<findings>",
    "Abdomen": "<findings>",
    "Neurological": "<findings>",
    "Extremities": "<findings>"
    // Add more systems as relevant
  }},

  "investigations": {{
    "<test name>": "<realistic result with interpretation>",
    // Include at least 8 investigations with realistic values
  }},

  "correct_diagnosis": "<precise diagnosis>",
  "acceptable_diagnoses": ["<alternative 1>", "<alternative 2>"],
  "correct_management": [
    "<step 1 with drug names and doses>",
    "<step 2>",
    // At least 8 management steps
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
  }}
}}

IMPORTANT:
- All vitals should be realistic and may be abnormal based on the condition
- History responses should be natural, first-person, layperson language
- Investigation results should include reference ranges where relevant
- Management steps should be specific (drug names, doses, timing)
- Return ONLY the JSON object, no explanation

JSON:"""


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
                prompt, temperature=0.3, max_tokens=3000
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

        case_data = json.loads(raw.strip())

        # Ensure required metadata
        case_data.setdefault("case_id", f"DYN-{unique_id}")
        case_data["_source_type"] = source_type
        case_data["_source_ref"] = source_ref
        
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
