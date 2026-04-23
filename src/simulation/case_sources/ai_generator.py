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
from typing import Any

logger = logging.getLogger(__name__)


def _repair_json(raw: str) -> str:
    """
    Best-effort repair of LLM-generated JSON that may contain:
    - // line comments (from the prompt template examples)
    - /* block comments */
    - Trailing commas before } or ]
    - Colon embedded inside key string: "key:" "value" → "key": "value"
    - Truncated output (hit max_tokens mid-object)
    - Extra content after the JSON object (model continues generating)
    """
    # Strip // line comments (but not inside strings — approximate, handles 99% of cases)
    raw = re.sub(r"//[^\n\"]*", "", raw)
    # Strip /* block comments */
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
    # Remove trailing commas before } or ]
    raw = re.sub(r",\s*([}\]])", r"\1", raw)

    # Extract first complete {...} block, handling nested braces
    # This finds the FIRST complete JSON object and ignores any extra content after it
    raw = _extract_first_json_object(raw)

    # Fix "key:" "value" → "key": "value" (model puts colon inside key string)
    # Must run AFTER extraction so we only fix content inside the JSON object
    raw = _fix_colon_in_key(raw)

    # Attempt to close truncated JSON (hit max_tokens mid-output)
    raw = _close_truncated_json(raw)
    return raw.strip()


def _fix_colon_in_key(raw: str) -> str:
    """
    Fix cases where the model puts the colon inside the key string.
    E.g., "C-Reactive Protein (CRP): 2 mg/L" → "C-Reactive Protein (CRP)": "2 mg/L"

    Strategy: Look for the pattern "Key Name: value" inside JSON objects and
    split it into "Key Name": "value". We use heuristics to find the right colon
    when there are multiple colons (e.g., reference ranges).
    """
    result = []
    i = 0
    n = len(raw)

    while i < n:
        # Look for opening quote of a potential key
        if raw[i] == '"':
            # Scan forward to find all colons inside this string
            j = i + 1
            colon_positions = []
            paren_depth = 0

            while j < n:
                if raw[j] == '\\' and j + 1 < n:
                    j += 2
                    continue
                if raw[j] in '([':
                    paren_depth += 1
                elif raw[j] in ')]':
                    paren_depth -= 1
                elif raw[j] == ':' and paren_depth == 0:
                    # Found a colon at top level (not inside parentheses)
                    colon_positions.append(j)
                elif raw[j] == '"':
                    # Found closing quote
                    break
                j += 1

            # If we found colons and a closing quote, this is a complete string
            # If we found colons but NO closing quote, this might be "key: value"
            if colon_positions and j < n and raw[j] == '"':
                # String closed properly - check if it looks like "key: value"
                # (i.e., the content has a colon followed by space and more content)
                first_colon = colon_positions[0]
                key_content = raw[i + 1:first_colon]

                # Check if this looks like a medical test name (has parentheses or specific patterns)
                # and the content after colon looks like a value
                if first_colon + 2 < j:
                    after_colon = raw[first_colon + 1:j].strip()
                    # If after_colon looks like a value (starts with number or common units)
                    if (after_colon and
                        (after_colon[0].isdigit() or
                         after_colon.startswith('<') or
                         after_colon.startswith('>') or
                         after_colon.lower().startswith('normal') or
                         after_colon.lower().startswith('negative') or
                         after_colon.lower().startswith('positive'))):
                        # This looks like "key: value" - split it
                        value_content = after_colon
                        result.append(f'"{key_content}": "{value_content}"')
                        i = j + 1
                        continue

            result.append(raw[i])
            i += 1
        else:
            result.append(raw[i])
            i += 1

    return ''.join(result)


def _extract_first_json_object(raw: str) -> str:
    """
    Extract the first complete JSON object from text that may contain:
    - Prose before/after the JSON
    - Multiple JSON objects concatenated
    - Extra content after the closing brace

    Returns only the first complete {...} block.
    """
    start = raw.find("{")
    if start == -1:
        return raw

    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(raw)):
        ch = raw[i]

        if escape_next:
            escape_next = False
            continue

        if ch == "\\" and in_string:
            escape_next = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                # Found the complete first object
                return raw[start:i+1]

    # Didn't find complete object - return what we have for _close_truncated_json to fix
    return raw[start:]


def _close_truncated_json(raw: str) -> str:
    """Close any unclosed strings, objects, and arrays caused by token truncation."""
    stack = []
    in_string = False
    escape_next = False
    for ch in raw:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ("{", "["):
            stack.append("}" if ch == "{" else "]")
        elif ch in ("}", "]"):
            if stack and stack[-1] == ch:
                stack.pop()

    # Truncated mid-string: strip the dangling partial value up to last comma or {
    if in_string:
        cut = max(raw.rfind(","), raw.rfind("{"), raw.rfind("["))
        if cut != -1:
            raw = raw[:cut]
        raw = re.sub(r",\s*$", "", raw)  # remove trailing comma after cut

    # Close any still-open containers in reverse order
    while stack:
        raw += stack.pop()

    return raw


# ── Case structuring prompt ───────────────────────────────────────────────────

CASE_STRUCTURING_PROMPT = """\
You are a medical education expert creating a structured clinical simulation case.

## Source Material
{source_text}

## Field requirements
- history_data: at least 12 keyword-response pairs covering pain, onset, duration, associated symptoms, medications, allergies, smoking, alcohol, family history, past medical history, social history, review of systems. Responses must be natural first-person layperson language (no medical jargon).
- physical_exam: **CRITICAL: Generate SPECIFIC findings for the chief complaint.** For knee pain: "Inspection: moderate swelling over anterior knee, no visible bruising. Palpation: tenderness over medial joint line and MCL. ROM: flexion limited to 90° due to pain, extension full. Special tests: positive valgus stress test, stable Lachman." For cardiac: "Regular rate and rhythm, S1/S2 normal, no murmurs/rubs." Be specific - NO generic text like "no specific findings".
- investigations: at least 8 tests with realistic values and reference ranges.
- correct_management: at least 8 specific steps with drug names and doses (e.g., "Ibuprofen 400mg PO TID PRN pain", "RICE protocol: Rest, Ice 20min Q2H, Compression wrap, Elevation above heart").
- key_learning_points: **MUST include 5 specific teaching points** relevant to this case (e.g., "1. MCL injuries present with medial knee tenderness and positive valgus stress test. 2. Ottawa Knee Rules help determine need for radiography...").
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
            # Reduced max_tokens to fit within model's 4096 context limit
            raw = await vllm_client.generate_async(
                prompt, temperature=0.3, max_tokens=1500, timeout=120.0
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
        
        # Patient portrait generation disabled - not clinically useful
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

    # Ensure minimum physical exam systems with clinically appropriate defaults
    pe = case_data["physical_exam"]

    # Check if this is a musculoskeletal/extremity case based on presentation
    presentation = case_data.get("presentation", "").lower()
    is_msks_case = any(word in presentation for word in
                       ["knee", "leg", "ankle", "hip", "shoulder", "arm", "wrist", "hand",
                        "foot", "elbow", "fracture", "ligament", "tendon", "joint", "pain"])

    # Add system-specific defaults instead of generic text
    pe.setdefault("General", "Patient is alert and oriented, appears comfortable.")
    pe.setdefault("Cardiovascular", "Regular rate and rhythm, no murmurs.")
    pe.setdefault("Respiratory", "Clear to auscultation bilaterally, no wheezes or rales.")
    pe.setdefault("Abdomen", "Soft, non-tender, no rebound or guarding.")
    pe.setdefault("Neurological", "Alert and oriented, cranial nerves intact, no focal deficits.")

    if is_msks_case:
        # For musculoskeletal cases, add specific extremity findings
        if any(word in presentation for word in ["knee", "leg"]):
            pe.setdefault("Extremities", "See Musculoskeletal exam for affected joint.")
            pe.setdefault("Musculoskeletal", "Inspect for swelling, bruising, or deformity. Palpate for tenderness. Assess range of motion and stability.")
        else:
            pe.setdefault("Extremities", "Warm and well-perfused, no edema.")
            pe.setdefault("Musculoskeletal", "No acute deformity or tenderness noted.")
    else:
        pe.setdefault("Extremities", "Warm and well-perfused, no edema or cyanosis.")
        pe.setdefault("Musculoskeletal", "No acute deformity or tenderness noted.")
