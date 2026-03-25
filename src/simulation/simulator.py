"""
Clinical Simulation Engine

Manages resident simulation sessions end-to-end:
  1. Case presentation
  2. Interactive history taking (MedGemma plays the patient)
  3. Physical examination reveal
  4. Investigation ordering
  5. Diagnosis and management submission
  6. AI-powered scoring and feedback (MedGemma as tutor)
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .cases import ClinicalCase, get_case, list_cases
from .chat_chain import build_patient_chain, clear_session_history
from .scorer import score_session
from .debrief import generate_debrief
from .imaging import get_image_url

logger = logging.getLogger(__name__)


# ── Session model ──────────────────────────────────────────────────────────────

@dataclass
class SimulationSession:
    session_id: str
    resident_name: str
    case_id: str
    case: ClinicalCase

    # Interaction history
    history_questions: list[dict] = field(default_factory=list)   # [{q, a, ts}]
    exam_systems_viewed: list[str] = field(default_factory=list)
    investigations_ordered: list[str] = field(default_factory=list)
    imaging_studies_viewed: list[str] = field(default_factory=list)  # study_ids

    # Submissions
    diagnosis_submitted: str = ""
    management_submitted: list[str] = field(default_factory=list)

    # Scoring & debrief
    score: dict | None = None
    debrief: dict | None = None

    # State
    status: str = "active"   # active | submitted | scored
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "resident_name": self.resident_name,
            "case_id": self.case_id,
            "case_title": self.case.title,
            "specialty": self.case.specialty,
            "difficulty": self.case.difficulty,
            "presentation": self.case.presentation,
            "patient_image_url": getattr(self.case, "patient_image_url", ""),
            "initial_vitals": self.case.initial_vitals,
            "learning_objectives": self.case.learning_objectives,
            "history_questions": self.history_questions,
            "exam_systems_viewed": self.exam_systems_viewed,
            "investigations_ordered": self.investigations_ordered,
            "imaging_studies_viewed": self.imaging_studies_viewed,
            "imaging_studies": [
                {
                    "study_id": s["study_id"],
                    "modality": s["modality"],
                    "description": s["description"],
                    "image_url": get_image_url(s["file_path"]),
                    "thumbnail": get_image_url(s["thumbnail"]) if s.get("thumbnail") else "",
                }
                for s in (self.case.imaging_studies or [])
            ],
            "diagnosis_submitted": self.diagnosis_submitted,
            "management_submitted": self.management_submitted,
            "score": self.score,
            "debrief": self.debrief,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "turn_count": len(self.history_questions),
        }


# ── Simulator ─────────────────────────────────────────────────────────────────

class SimulationEngine:
    """Manages all active simulation sessions."""

    def __init__(self, agent=None):
        self.agent = agent
        self._sessions: dict[str, SimulationSession] = {}
        self._chain = build_patient_chain(agent) if agent is not None else None

    def set_agent(self, agent) -> None:
        self.agent = agent
        self._chain = build_patient_chain(agent)

    # ── Session lifecycle ──────────────────────────────────────────────────────

    def start_session(self, resident_name: str, case_id: str) -> SimulationSession:
        case = get_case(case_id)
        if case is None:
            raise ValueError(f"Case {case_id} not found")
        session = SimulationSession(
            session_id=f"SIM-{uuid.uuid4().hex[:8].upper()}",
            resident_name=resident_name,
            case_id=case_id,
            case=case,
        )
        self._sessions[session.session_id] = session
        logger.info(
            "Simulation session %s started for %s — case %s",
            session.session_id, resident_name, case_id,
        )
        return session

    def get_session(self, session_id: str) -> SimulationSession | None:
        return self._sessions.get(session_id)

    # ── Interactions ───────────────────────────────────────────────────────────

    def ask_history(self, session_id: str, question: str) -> dict:
        """
        Resident asks the patient a history question.
        MedGemma responds in character via the stateful LangChain chain.
        """
        session = self._get_active_session(session_id)
        case = session.case
        history_ctx = "\n".join(f"- {k}: {v}" for k, v in case.history_data.items())

        if self._chain is not None:
            try:
                result = self._chain.invoke(
                    {
                        "case_presentation": case.presentation,
                        "history_context": history_ctx,
                        "question": question,
                    },
                    config={"configurable": {"session_id": session_id}},
                )
                response = self._clean_response(result.content)
                ai = True
            except Exception as e:
                logger.warning("Chain history response failed: %s", e)
                response = self._keyword_patient_response(case, question)
                ai = False
        else:
            response = self._keyword_patient_response(case, question)
            ai = False

        entry = {
            "question": question,
            "response": response,
            "ai": ai,
            "ts": datetime.now().isoformat(),
        }
        session.history_questions.append(entry)
        return entry

    def view_exam(self, session_id: str, system: str) -> dict:
        """Resident requests physical examination findings for a body system."""
        session = self._get_active_session(session_id)
        system_key = next(
            (k for k in session.case.physical_exam if system.lower() in k.lower()),
            None,
        )
        if system_key is None:
            findings = "No specific findings documented for this system."
        else:
            findings = session.case.physical_exam[system_key]
            if system_key not in session.exam_systems_viewed:
                session.exam_systems_viewed.append(system_key)

        return {"system": system_key or system, "findings": findings}

    def order_investigation(self, session_id: str, investigation: str) -> dict:
        """Resident orders an investigation. Returns the result."""
        session = self._get_active_session(session_id)
        inv_key = next(
            (k for k in session.case.investigations if investigation.lower() in k.lower()),
            None,
        )
        if inv_key is None:
            result = "Investigation not available in this simulation or result pending."
            key = investigation
        else:
            result = session.case.investigations[inv_key]
            key = inv_key
            if key not in session.investigations_ordered:
                session.investigations_ordered.append(key)

        return {"investigation": key, "result": result}

    def view_imaging(self, session_id: str, study_id: str) -> dict:
        """
        Resident views a medical image / waveform.
        Returns metadata + findings (ground truth revealed).
        """
        session = self._get_active_session(session_id)
        study = next(
            (s for s in (session.case.imaging_studies or []) if s["study_id"] == study_id),
            None,
        )
        if study is None:
            return {"study_id": study_id, "error": "Imaging study not found for this case."}

        if study_id not in session.imaging_studies_viewed:
            session.imaging_studies_viewed.append(study_id)

        return {
            "study_id": study["study_id"],
            "modality": study["modality"],
            "description": study["description"],
            "image_url": get_image_url(study["file_path"]),
            "findings": study["findings"],
        }

    def submit_assessment(
        self,
        session_id: str,
        diagnosis: str,
        management: list[str],
    ) -> dict:
        """
        Resident submits their final diagnosis and management plan.
        Triggers scoring and debrief generation.
        """
        session = self._get_active_session(session_id)
        session.diagnosis_submitted = diagnosis
        session.management_submitted = management
        session.status = "submitted"

        # Score
        score_result = score_session(session, agent=self.agent)
        session.score = score_result.to_dict()

        # Debrief
        debrief_result = generate_debrief(session, score_result, agent=self.agent)
        session.debrief = debrief_result.to_dict()

        session.status = "scored"
        session.completed_at = datetime.now().isoformat()

        # Release LangChain message history — session is complete
        clear_session_history(session_id)

        return {
            "session_id": session_id,
            "submitted_at": session.completed_at,
            "scores": session.score,
            "debrief": session.debrief,
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_active_session(self, session_id: str) -> SimulationSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        if session.status == "scored":
            raise ValueError("Session already completed")
        return session

    @staticmethod
    def _clean_response(text: str) -> str:
        """Strip MedGemma internal thinking/planning preamble from responses."""
        text = re.sub(r"^<unused\d+>\s*", "", text.strip())

        if "\n\n" not in text:
            if text.lower().startswith("thought"):
                return re.sub(
                    r"^thought\b.*", "", text,
                    flags=re.IGNORECASE | re.DOTALL,
                ).strip()
            return text

        first_para, rest = text.split("\n\n", 1)
        first_lower = first_para.lower()

        preamble_signals = [
            first_lower.startswith("thought"),
            "i need to respond" in first_lower,
            "i should respond" in first_lower,
            "let me respond" in first_lower,
            "plan:" in first_lower,
            "my plan" in first_lower,
            "let me think" in first_lower,
            "i will respond" in first_lower,
            bool(re.search(r"^\s*1\.", first_para, re.MULTILINE)),
        ]

        if any(preamble_signals) and rest.strip():
            return rest.strip()

        return text

    @staticmethod
    def _keyword_patient_response(case: ClinicalCase, question: str) -> str:
        """Fallback: match question to history data by keyword."""
        question_lower = question.lower()
        for key, response in case.history_data.items():
            if key.lower() in question_lower or any(
                word in question_lower for word in key.lower().split()
            ):
                return response
        return (
            "I'm not sure what you mean. Could you ask me differently? "
            "I can tell you about my symptoms, medications, or medical history."
        )


# ── Singleton ──────────────────────────────────────────────────────────────────
_engine: SimulationEngine | None = None


def get_simulation_engine(agent=None) -> SimulationEngine:
    global _engine
    if _engine is None:
        _engine = SimulationEngine(agent=agent)
    elif agent is not None:
        _engine.set_agent(agent)
    return _engine
