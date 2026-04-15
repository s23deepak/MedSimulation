"""
EndlessMedical API client for diagnostic case generation.

API docs: https://endlessmedical.com/api
Base URL: https://api.endlessmedical.com/v1/dx/

The API provides:
  - 830+ clinical features (symptoms, signs, labs)
  - 180+ diseases with diagnostic probabilities
  - Suggested next tests and features
  - Session-based interaction

We use it to:
  1. Query available diseases/features
  2. Build realistic symptom profiles
  3. Get differential diagnoses
  4. Feed all of this to the AI case generator
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .ai_generator import generate_case

logger = logging.getLogger(__name__)

BASE_URL = "https://api.endlessmedical.com/v1/dx"
TOS_PASSPHRASE = "I have read, understood and I accept and agree to comply with the Terms of Use of EndlessMedical API and target Applications. The Terms of Use are available on endlessmedical.com"


# ── Session management ────────────────────────────────────────────────────────

async def _init_session() -> str:
    """Initialize an EndlessMedical API session and accept ToS."""
    # Use verify=False to work around SSL certificate issues
    async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
        # Init session
        r = await client.get(f"{BASE_URL}/InitSession")
        r.raise_for_status()
        session_id = r.json().get("SessionID", "")
        if not session_id:
            raise ValueError("EndlessMedical: failed to get SessionID")

        # Accept Terms of Service (required)
        r = await client.post(
            f"{BASE_URL}/AcceptTermsOfUse",
            params={"SessionID": session_id, "passphrase": TOS_PASSPHRASE},
        )
        r.raise_for_status()

    logger.info("EndlessMedical session initialized: %s", session_id)
    return session_id


# ── Feature / disease queries ─────────────────────────────────────────────────

async def get_available_features() -> list[str]:
    """Get all available clinical features from the API."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{BASE_URL}/GetFeatures")
        r.raise_for_status()
        return r.json().get("data", [])


async def get_available_diseases() -> list[str]:
    """Get all available diseases/diagnoses from the API."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{BASE_URL}/GetOutcomes")
        r.raise_for_status()
        return r.json().get("data", [])


async def add_feature(session_id: str, feature_name: str, value: str) -> dict:
    """Add a clinical feature to the session."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{BASE_URL}/UpdateFeature",
            params={
                "SessionID": session_id,
                "name": feature_name,
                "value": value,
            },
        )
        r.raise_for_status()
        return r.json()


async def get_diagnoses(session_id: str) -> list[dict]:
    """
    Get differential diagnoses based on current features.

    Returns list of {"Disease": str, "Probability": float}
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{BASE_URL}/GetDiagnoses",
            params={"SessionID": session_id},
        )
        r.raise_for_status()
        data = r.json()
        # API returns {Diseases: [{Disease, Probability}]}
        return data.get("Diseases", [])


async def get_suggested_tests(session_id: str) -> list[str]:
    """Get suggested diagnostic tests from the API."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{BASE_URL}/GetSuggestedTests",
            params={"SessionID": session_id},
        )
        r.raise_for_status()
        return r.json().get("SuggestedTests", [])


async def get_suggested_features(session_id: str) -> list[str]:
    """Get suggested features to ask the patient about."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{BASE_URL}/GetSuggestedFeatures_PatientProvided",
            params={"SessionID": session_id},
        )
        r.raise_for_status()
        return r.json().get("SuggestedFeatures", [])


# ── Case generation pipeline ─────────────────────────────────────────────────

async def build_case_from_disease(
    disease_name: str,
    vllm_client: Any,
    features_to_add: dict[str, str] | None = None,
) -> dict:
    """
    Build a full clinical simulation case starting from a disease name.

    Pipeline:
      1. Init EndlessMedical session
      2. Add features if provided (or let the AI fill them)
      3. Get suggested tests and differential diagnoses
      4. Feed all data to AI case generator

    Parameters
    ----------
    disease_name : str
        Target disease (e.g. "Myocardial Infarction", "Pneumonia")
    vllm_client : VLLMClient
        vLLM inference client
    features_to_add : dict, optional
        Pre-set features {feature_name: value}

    Returns
    -------
    dict
        ClinicalCase-compatible dictionary
    """
    session_id = await _init_session()

    # Add any pre-set features
    if features_to_add:
        for name, value in features_to_add.items():
            try:
                await add_feature(session_id, name, value)
            except Exception as e:
                logger.warning("Failed to add feature %s: %s", name, e)

    # Get diagnostic context from the API
    try:
        diagnoses = await get_diagnoses(session_id)
        suggested_tests = await get_suggested_tests(session_id)
        suggested_features = await get_suggested_features(session_id)
    except Exception as e:
        logger.warning("EndlessMedical API query failed: %s — generating from disease name only", e)
        diagnoses = []
        suggested_tests = []
        suggested_features = []

    # Build source text for AI case generation
    source_text = (
        f"Disease: {disease_name}\n"
        f"Source: EndlessMedical Diagnostic API\n\n"
    )

    if diagnoses:
        source_text += "Differential Diagnoses (by probability):\n"
        for dx in diagnoses[:10]:
            source_text += f"  - {dx.get('Disease', 'Unknown')}: {dx.get('Probability', 0):.1%}\n"
        source_text += "\n"

    if suggested_tests:
        source_text += f"Suggested Diagnostic Tests:\n"
        for t in suggested_tests[:15]:
            source_text += f"  - {t}\n"
        source_text += "\n"

    if suggested_features:
        source_text += f"Relevant Clinical Features to Explore:\n"
        for f in suggested_features[:15]:
            source_text += f"  - {f}\n"
        source_text += "\n"

    if features_to_add:
        source_text += f"Known Patient Features:\n"
        for k, v in features_to_add.items():
            source_text += f"  - {k}: {v}\n"

    source_text += (
        f"\nGenerate a realistic clinical simulation case for {disease_name}. "
        "Include accurate vitals, history, exam findings, investigations, "
        "diagnosis, and management plan with specific drug names and doses."
    )

    return await generate_case(
        vllm_client,
        source_text=source_text,
        source_type="endless_medical",
        source_ref=disease_name,
    )
