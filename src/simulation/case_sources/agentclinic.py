"""
AgentClinic dataset importer.

Converts AgentClinic's OSCE_Examination JSONL format into
MedSimulation's ClinicalCase dataclass, giving us 335+ structured
clinical cases from USMLE (MedQA) and NEJM case challenges.

Data source: https://github.com/SamuelSchmidgall/AgentClinic
License: MIT

Datasets:
  - agentclinic_medqa.jsonl          →  107 USMLE-grounded cases
  - agentclinic_medqa_extended.jsonl →  215 USMLE-grounded cases
  - agentclinic_nejm.jsonl           →   15 NEJM image cases
  - agentclinic_nejm_extended.jsonl  →  120 NEJM image cases
"""

from __future__ import annotations

import json
import logging
import re
import os
from pathlib import Path
from typing import Any
from openai import AsyncOpenAI

from src.simulation.media import generate_patient_portrait

logger = logging.getLogger(__name__)

# Where downloaded datasets live
DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "agentclinic"

DATASET_URLS = {
    "medqa": "https://raw.githubusercontent.com/SamuelSchmidgall/AgentClinic/main/agentclinic_medqa.jsonl",
    "medqa_ext": "https://raw.githubusercontent.com/SamuelSchmidgall/AgentClinic/main/agentclinic_medqa_extended.jsonl",
    "nejm": "https://raw.githubusercontent.com/SamuelSchmidgall/AgentClinic/main/agentclinic_nejm.jsonl",
    "nejm_ext": "https://raw.githubusercontent.com/SamuelSchmidgall/AgentClinic/main/agentclinic_nejm_extended.jsonl",
}


# ── Download ──────────────────────────────────────────────────────────────────

async def download_dataset(dataset: str = "medqa_ext") -> Path:
    """
    Download an AgentClinic JSONL file if not already cached.

    Parameters
    ----------
    dataset : str
        One of: 'medqa', 'medqa_ext', 'nejm', 'nejm_ext'

    Returns
    -------
    Path to the local JSONL file.
    """
    import httpx

    if dataset not in DATASET_URLS:
        raise ValueError(f"Unknown dataset '{dataset}'. Choose from: {list(DATASET_URLS.keys())}")

    url = DATASET_URLS[dataset]
    filename = url.rsplit("/", 1)[-1]
    local_path = DATA_DIR / filename

    if local_path.exists():
        logger.info("AgentClinic dataset already cached: %s", local_path)
        return local_path

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading AgentClinic %s from %s ...", dataset, url)

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        local_path.write_bytes(r.content)

    logger.info("Saved %s (%d bytes)", local_path.name, local_path.stat().st_size)
    return local_path


# ── Parse ─────────────────────────────────────────────────────────────────────

def parse_agentclinic_jsonl(path: Path) -> list[dict]:
    """Parse an AgentClinic JSONL file into a list of raw dicts."""
    records = []
    text = path.read_text(encoding="utf-8")

    # The JSONL file has multiple JSON objects per line sometimes,
    # so we need a smart parser
    for raw_obj in _iter_json_objects(text):
        try:
            records.append(raw_obj)
        except Exception as e:
            logger.warning("Skipping malformed record: %s", e)

    logger.info("Parsed %d records from %s", len(records), path.name)
    return records


def _iter_json_objects(text: str):
    """
    Yield JSON objects from a file that may have multiple objects
    per line (AgentClinic's format puts ~2 objects per line).
    """
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)
    while idx < length:
        # Skip whitespace / newlines
        while idx < length and text[idx] in " \t\n\r":
            idx += 1
        if idx >= length:
            break
        try:
            obj, end_pos = decoder.raw_decode(text, idx)
            yield obj
            idx = end_pos  # end_pos is absolute position, NOT relative
        except json.JSONDecodeError:
            # Skip to next line
            next_newline = text.find("\n", idx)
            if next_newline == -1:
                break
            idx = next_newline + 1


# ── Convert to ClinicalCase ──────────────────────────────────────────────────

_CASE_COUNTER = 0


def convert_to_clinical_case(record: dict, dataset_name: str = "medqa") -> dict:
    """
    Convert an AgentClinic OSCE_Examination record into a
    ClinicalCase-compatible dictionary.

    AgentClinic schema:
    {
      "OSCE_Examination": {
        "Objective_for_Doctor": str,
        "Patient_Actor": {
          "Demographics": str,
          "History": str,
          "Symptoms": {
            "Primary_Symptom": str,
            "Secondary_Symptoms": [str]
          },
          "Past_Medical_History": str,
          "Social_History": str,
          "Review_of_Systems": str
        },
        "Physical_Examination_Findings": {
          "Vital_Signs": dict,
          "...system_exams...": dict
        },
        "Test_Results": dict,
        "Correct_Diagnosis": str
      }
    }
    """
    global _CASE_COUNTER
    _CASE_COUNTER += 1

    osce = record.get("OSCE_Examination", record)
    patient = osce.get("Patient_Actor", {})
    symptoms = patient.get("Symptoms", {})
    phys_exam = osce.get("Physical_Examination_Findings", {})
    vitals_raw = phys_exam.get("Vital_Signs", {})
    test_results = osce.get("Test_Results", {})
    diagnosis = osce.get("Correct_Diagnosis", "Unknown")
    objective = osce.get("Objective_for_Doctor", "")

    demographics = patient.get("Demographics", "Patient")
    prefix = "AC-MQ" if "medqa" in dataset_name else "AC-NJ"
    case_id = f"{prefix}-{_CASE_COUNTER:04d}"

    # ── Build presentation ────────────────────────────────────────────────
    primary = symptoms.get("Primary_Symptom", "")
    secondary = symptoms.get("Secondary_Symptoms", [])
    symptom_text = primary
    if secondary:
        symptom_text += ". Also reports: " + ", ".join(secondary[:3])

    presentation = (
        f"A {demographics} presents with {symptom_text.lower() if symptom_text else 'symptoms for evaluation'}. "
        f"{patient.get('History', '')}"
    )

    # ── Vitals ────────────────────────────────────────────────────────────
    initial_vitals = _normalize_vitals(vitals_raw)

    # ── History data (keyword → patient response) ─────────────────────────
    history_data = {}

    if patient.get("History"):
        history_data["history"] = patient["History"]
    if symptoms.get("Primary_Symptom"):
        history_data["main complaint"] = f"My main problem is {symptoms['Primary_Symptom'].lower()}."
    for i, sec in enumerate(secondary):
        history_data[sec.lower()[:30]] = f"Yes, I've also been experiencing {sec.lower()}."
    if patient.get("Past_Medical_History"):
        history_data["past medical history"] = patient["Past_Medical_History"]
    if patient.get("Social_History"):
        history_data["social history"] = patient["Social_History"]
    if patient.get("Review_of_Systems"):
        history_data["review of systems"] = patient["Review_of_Systems"]

    # Add medications, allergies if mentioned in history
    history_text = patient.get("History", "") + " " + patient.get("Past_Medical_History", "")
    if "medication" in history_text.lower() or "taking" in history_text.lower():
        history_data["medications"] = _extract_or_default(history_text, "medication", "I'm not on any medications.")
    if "allerg" in history_text.lower():
        history_data["allergies"] = _extract_or_default(history_text, "allerg", "No known allergies.")

    # Ensure minimum entries
    if len(history_data) < 5:
        history_data.setdefault("medications", "I'm not currently taking any medications.")
        history_data.setdefault("allergies", "No known allergies.")
        history_data.setdefault("family history", "No significant family history that I know of.")

    # ── Physical exam ─────────────────────────────────────────────────────
    physical_exam = {}
    for key, value in phys_exam.items():
        if key == "Vital_Signs":
            continue
        clean_key = key.replace("_", " ").replace("Examination", "").strip()
        if isinstance(value, dict):
            parts = []
            for sub_key, sub_val in value.items():
                sub_label = sub_key.replace("_", " ")
                if isinstance(sub_val, dict):
                    sub_parts = [f"{k.replace('_', ' ')}: {v}" for k, v in sub_val.items()]
                    parts.append(f"{sub_label}: {'; '.join(sub_parts)}")
                else:
                    parts.append(f"{sub_label}: {sub_val}")
            physical_exam[clean_key] = ". ".join(parts)
        else:
            physical_exam[clean_key] = str(value)

    if not physical_exam:
        physical_exam["General"] = "No specific examination findings provided."

    # ── Investigations ────────────────────────────────────────────────────
    investigations = _flatten_test_results(test_results)

    # ── Learning objectives ───────────────────────────────────────────────
    learning_objectives = [
        objective if objective else f"Diagnose and manage {diagnosis}",
        f"Recognize key clinical features of {diagnosis}",
        "Appropriate use of diagnostic investigations",
        "Evidence-based management planning",
    ]

    # ── Build the case ────────────────────────────────────────────────────
    return {
        "case_id": case_id,
        "title": _generate_title(demographics, primary, diagnosis),
        "specialty": _infer_specialty(diagnosis, objective),
        "difficulty": _infer_difficulty(diagnosis, len(investigations)),
        "learning_objectives": learning_objectives,
        "presentation": presentation,
        "initial_vitals": initial_vitals,
        "history_data": history_data,
        "physical_exam": physical_exam,
        "investigations": investigations,
        "correct_diagnosis": diagnosis,
        "acceptable_diagnoses": _generate_acceptable(diagnosis),
        "correct_management": [
            f"Confirm diagnosis of {diagnosis} with appropriate investigations",
            "Stabilize patient and address immediate concerns",
            "Develop comprehensive management plan",
            "Consult relevant specialist teams as needed",
            "Arrange appropriate follow-up and monitoring",
        ],
        "key_learning_points": [
            f"Key diagnostic features of {diagnosis}",
            "Importance of systematic clinical assessment",
            "Correlation between history, examination, and investigations",
            f"Evidence-based management of {diagnosis}",
            "Recognition of red flag features requiring urgent intervention",
        ],
        "score_weights": {
            "history": 20,
            "exam": 20,
            "investigations": 20,
            "diagnosis": 25,
            "management": 15,
        },
    }


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def import_agentclinic(
    dataset: str = "medqa_ext",
    max_cases: int | None = None,
) -> list[dict]:
    """
    Download, parse, and convert AgentClinic cases.

    Parameters
    ----------
    dataset : str
        'medqa', 'medqa_ext', 'nejm', 'nejm_ext'
    max_cases : int, optional
        Limit number of cases to import

    Returns
    -------
    list[dict]
        ClinicalCase-compatible dictionaries
    """
    global _CASE_COUNTER
    _CASE_COUNTER = 0  # Reset counter for clean IDs

    path = await download_dataset(dataset)
    records = parse_agentclinic_jsonl(path)

    if max_cases:
        records = records[:max_cases]

    cases = []
    
    # Phase E: Prepare Image Gen Client
    openai_key = os.getenv("OPENAI_API_KEY")
    client = None
    if openai_key:
        client = AsyncOpenAI(api_key=openai_key)
        
    for record in records:
        try:
            case = convert_to_clinical_case(record, dataset)
            
            # Generate portrait
            if client:
                portrait_url = await generate_patient_portrait(client, case.get("presentation", ""))
                case["patient_image_url"] = portrait_url
            else:
                case["patient_image_url"] = ""
                
            cases.append(case)
        except Exception as e:
            logger.warning("Failed to convert AgentClinic record: %s", e)

    logger.info(
        "Converted %d/%d AgentClinic cases from %s",
        len(cases), len(records), dataset,
    )
    return cases


# ── Helper functions ──────────────────────────────────────────────────────────

def _normalize_vitals(vitals_raw: dict) -> dict:
    """Normalize AgentClinic vital signs to our format."""
    mapping = {
        "Temperature": "Temp",
        "Blood_Pressure": "BP",
        "Heart_Rate": "HR",
        "Respiratory_Rate": "RR",
        "Oxygen_Saturation": "SpO2",
        "SpO2": "SpO2",
    }
    result = {}
    for key, value in vitals_raw.items():
        mapped = mapping.get(key, key.replace("_", " "))
        result[mapped] = str(value)

    # Ensure standard vitals are present
    result.setdefault("HR", "Not recorded")
    result.setdefault("BP", "Not recorded")
    result.setdefault("RR", "Not recorded")
    result.setdefault("SpO2", "Not recorded")
    result.setdefault("Temp", "Not recorded")
    result.setdefault("GCS", "Not recorded")
    return result


def _flatten_test_results(test_results: dict, prefix: str = "") -> dict:
    """Recursively flatten nested test results dict."""
    flat = {}
    for key, value in test_results.items():
        clean_key = key.replace("_", " ")
        if prefix:
            clean_key = f"{prefix} — {clean_key}"

        if isinstance(value, dict):
            # Check if it's a leaf with "Findings"
            if "Findings" in value:
                findings = value["Findings"]
                comments = value.get("Comments", "")
                flat[clean_key] = f"{findings}. {comments}".strip().rstrip(".")
            else:
                # Recurse
                flat.update(_flatten_test_results(value, clean_key))
        elif isinstance(value, str):
            flat[clean_key] = value
        elif isinstance(value, list):
            flat[clean_key] = ", ".join(str(v) for v in value)

    return flat


def _generate_title(demographics: str, primary_symptom: str, diagnosis: str) -> str:
    """Generate a descriptive case title."""
    if primary_symptom:
        symptom_clean = primary_symptom.rstrip(".")
        return f"{symptom_clean} in a {demographics}"
    return f"{diagnosis} — {demographics}"


def _generate_acceptable(diagnosis: str) -> list[str]:
    """Generate acceptable diagnosis alternatives."""
    alternatives = [diagnosis]
    # Add common abbreviations / variations
    words = diagnosis.split()
    if len(words) > 2:
        # Add acronym
        acronym = "".join(w[0].upper() for w in words if w[0].isupper() or len(w) > 3)
        if len(acronym) >= 2:
            alternatives.append(acronym)
    # Add without parenthetical
    if "(" in diagnosis:
        alternatives.append(re.sub(r"\s*\([^)]+\)", "", diagnosis).strip())
    return alternatives


def _infer_specialty(diagnosis: str, objective: str) -> str:
    """Infer medical specialty from diagnosis / objective text."""
    text = (diagnosis + " " + objective).lower()
    specialty_map = {
        "Emergency Medicine": ["emergenc", "trauma", "acute", "shock", "resuscit"],
        "Internal Medicine": ["diabetes", "hypertension", "chronic", "metabol", "autoimmune", "thyroid", "liver", "renal", "kidney"],
        "Cardiology": ["cardiac", "heart", "myocard", "arrhythm", "coronary", "valve", "cardiomyopath"],
        "Neurology": ["stroke", "seizure", "neurolog", "brain", "dementia", "neuropath", "multiple sclerosis", "meningit", "ataxia"],
        "Surgery": ["appendic", "hernia", "fracture", "surgical", "obstruct", "cholecyst", "abscess"],
        "Pulmonology": ["pulmon", "pneumon", "lung", "asthma", "copd", "respirat", "bronch"],
        "Oncology": ["cancer", "tumor", "lymphoma", "leukemia", "carcinoma", "malignan", "neoplasm", "metasta"],
        "Pediatrics": ["infant", "child", "neonat", "pediatr", "month-old", "year-old boy", "year-old girl"],
        "Obstetrics": ["pregnan", "obstetric", "fetal", "placent", "eclampsia", "gestation"],
        "Psychiatry": ["depress", "anxiety", "psycho", "schizo", "bipolar", "psychiatr"],
        "Infectious Disease": ["infecti", "hiv", "aids", "tuberculosis", "malaria", "septi", "hepatitis"],
        "Dermatology": ["rash", "skin", "dermat", "eczema", "psoriasis", "lesion"],
        "Rheumatology": ["arthritis", "lupus", "rheumat", "gout", "fibromyalgia"],
        "Endocrinology": ["endocrin", "cushing", "addison", "pituitary", "adrenal"],
        "Gastroenterology": ["gastro", "crohn", "colitis", "bowel", "hepat", "pancreat", "celiac", "hirschsprung"],
        "Hematology": ["anemia", "hemophilia", "thrombocyt", "coagulat", "sickle cell"],
        "Ophthalmology": ["eye", "vision", "optic", "retina", "glaucoma", "cataract"],
        "Nephrology": ["nephro", "glomerulo", "dialysis", "uremia"],
    }
    for specialty, keywords in specialty_map.items():
        if any(kw in text for kw in keywords):
            return specialty
    return "General Medicine"


def _infer_difficulty(diagnosis: str, num_investigations: int) -> str:
    """Infer difficulty from diagnosis complexity and investigation count."""
    # Rare / complex conditions
    rare = ["pml", "progressive multifocal", "erdheim", "langerhans", "amyloid",
            "pheochromocytoma", "addison", "cushing", "acromegaly", "hemochromatosis"]
    if any(r in diagnosis.lower() for r in rare):
        return "advanced"
    if num_investigations >= 6:
        return "intermediate"
    if num_investigations >= 3:
        return "intermediate"
    return "beginner"


def _extract_or_default(text: str, keyword: str, default: str) -> str:
    """Try to extract a sentence containing keyword from text."""
    sentences = text.split(".")
    for s in sentences:
        if keyword.lower() in s.lower() and len(s.strip()) > 10:
            return s.strip() + "."
    return default
