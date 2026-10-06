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
from dataclasses import asdict, dataclass, field
from copy import deepcopy
from datetime import datetime
from typing import Any

from .cases import ClinicalCase, get_case, list_cases
from .chat_chain import build_patient_chain, clear_session_history
from .scorer import score_session
from .debrief import generate_debrief
from .imaging import get_image_url
from .database import save_session, load_session

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

    # Chronological action log — tracks doctor actions in order for feedback
    action_log: list[dict] = field(default_factory=list)  # [{type, detail, ts}]

    # Submissions
    diagnosis_submitted: str = ""
    management_submitted: list[str] = field(default_factory=list)
    clinical_notes: str = ""

    # Scoring & debrief
    score: dict | None = None
    debrief: dict | None = None

    # State
    status: str = "active"   # active | submitted | scored
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str = ""
    owner_id: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "owner_id": self.owner_id,
            "case_snapshot": asdict(self.case),
            "case_version": self.case.version,
            "review_status": self.case.status,
            "reviewer": self.case.reviewer,
            "approved_at": self.case.approved_at,
            "scoring_available": self.case.status == "approved" and bool(self.case.rubric),
            "available_exams": list(self.case.physical_exam),
            "available_investigations": list(self.case.investigations),
            "resident_name": self.resident_name,
            "case_id": self.case_id,
            "case_title": self.case.title,
            "specialty": self.case.specialty,
            "difficulty": self.case.difficulty,
            "presentation": self.case.presentation,
            "patient_image_url": getattr(self.case, "patient_image_url", ""),
            "initial_vitals": self.case.initial_vitals,
            "abnormal_vitals": self.case.abnormal_vitals,
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
                    "image_url": get_image_url(s["file_path"]) if s.get("file_path") else "",
                    "thumbnail": get_image_url(s["thumbnail"]) if s.get("thumbnail") else "",
                }
                for s in (self.case.imaging_studies or [])
            ],
            "diagnosis_submitted": self.diagnosis_submitted,
            "management_submitted": self.management_submitted,
            "clinical_notes": self.clinical_notes,
            "ordered_results": [
                {"investigation": name, "result": next((value for key, value in self.case.investigations.items() if name.lower() in key.lower() or key.lower() in name.lower()), "Result not recorded.")}
                for name in self.investigations_ordered
            ],
            "score": self.score,
            "debrief": self.debrief,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "turn_count": len(self.history_questions),
            "action_log": self.action_log,
            "source": getattr(self.case, "source", ""),
            "source_ref": getattr(self.case, "source_ref", ""),
        }


# ── Simulator ─────────────────────────────────────────────────────────────────

class SimulationEngine:
    """
    Manages all active simulation sessions.

    Uses hybrid storage:
    - In-memory cache (_sessions) for active sessions
    - Database persistence for cross-container state sharing

    On Modal (serverless), containers are stateless, so we load from DB
    on each request if the session isn't in memory.
    """

    def __init__(self, agent=None):
        self.agent = agent
        self._sessions: dict[str, SimulationSession] = {}  # In-memory cache
        self._chain = build_patient_chain(agent) if agent is not None else None

    def set_agent(self, agent) -> None:
        self.agent = agent
        self._chain = build_patient_chain(agent)

    # ── Session lifecycle ──────────────────────────────────────────────────────

    def start_session(self, resident_name: str, case_id: str, owner_id: str = "") -> SimulationSession:
        case = get_case(case_id)
        if case is None:
            raise ValueError(f"Case {case_id} not found")
        session = SimulationSession(
            session_id=f"SIM-{uuid.uuid4().hex.upper()}",
            resident_name=resident_name,
            case_id=case_id,
            case=deepcopy(case),
            owner_id=owner_id,
        )
        self._sessions[session.session_id] = session
        self._persist_session(session)
        logger.info(
            "Simulation session %s started for %s — case %s",
            session.session_id, resident_name, case_id,
        )
        return session

    def get_session(self, session_id: str) -> SimulationSession | None:
        # Fall back to database (for cross-container requests)
        session_data = load_session(session_id)
        if session_data is None:
            return None

        # Reconstruct session from DB
        snapshot = session_data.get("case_snapshot")
        case = ClinicalCase(**snapshot) if snapshot else get_case(session_data.get("case_id"))
        if case is None:
            logger.warning("Session %s references non-existent case %s", session_id, session_data.get("case_id"))
            return None

        session = SimulationSession(
            session_id=session_data.get("session_id"),
            resident_name=session_data.get("resident_name"),
            case_id=session_data.get("case_id"),
            case=case,
            owner_id=session_data.get("owner_id", ""),
        )
        # Restore state
        session.history_questions = session_data.get("history_questions", [])
        session.exam_systems_viewed = session_data.get("exam_systems_viewed", [])
        session.investigations_ordered = session_data.get("investigations_ordered", [])
        session.imaging_studies_viewed = session_data.get("imaging_studies_viewed", [])
        session.action_log = session_data.get("action_log", [])
        session.diagnosis_submitted = session_data.get("diagnosis_submitted", "")
        session.management_submitted = session_data.get("management_submitted", [])
        session.clinical_notes = session_data.get("clinical_notes", "")
        session.score = session_data.get("score")
        session.debrief = session_data.get("debrief")
        session.status = session_data.get("status", "active")
        session.started_at = session_data.get("started_at")
        session.completed_at = session_data.get("completed_at")

        # Cache in memory
        self._sessions[session_id] = session
        return session

    def _persist_session(self, session: SimulationSession) -> None:
        """Persist session state to database."""
        save_session(session.to_dict())

    # ── Interactions ───────────────────────────────────────────────────────────

    def ask_history(self, session_id: str, question: str) -> dict:
        """
        Resident asks the patient a history question.
        MedGemma responds in character via the stateful LangChain chain.
        """
        session = self._get_active_session(session_id)
        case = session.case
        history_ctx = "\n".join(f"- {k}: {v}" for k, v in case.history_data.items())

        if self._chain is None:
            raise RuntimeError("Patient response model is not configured")

        # Rebuild the prompt history after a worker restart or request on a
        # different worker; the durable transcript remains the source of truth.
        from langchain_core.messages import AIMessage, HumanMessage
        from .chat_chain import get_session_history
        prompt_history = get_session_history(session_id)
        if not prompt_history.messages and session.history_questions:
            for turn in session.history_questions:
                prompt_history.add_messages([HumanMessage(content=turn["question"]), AIMessage(content=turn["response"])])

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
            raise RuntimeError("Patient response model is unavailable") from e

        entry = {
            "question": question,
            "response": response,
            "ai": ai,
            "ts": datetime.now().isoformat(),
        }
        session.history_questions.append(entry)
        session.action_log.append({"type": "history", "detail": question, "ts": entry["ts"]})
        self._persist_session(session)
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

        ts = datetime.now().isoformat()
        session.action_log.append({"type": "exam", "detail": system_key or system, "ts": ts})
        self._persist_session(session)
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

        ts = datetime.now().isoformat()
        session.action_log.append({"type": "investigation", "detail": key, "ts": ts})
        self._persist_session(session)
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

        ts = datetime.now().isoformat()
        session.action_log.append({"type": "imaging", "detail": study_id, "ts": ts})
        self._persist_session(session)
        return {
            "study_id": study["study_id"],
            "modality": study["modality"],
            "description": study["description"],
            "image_url": get_image_url(study["file_path"]) if study.get("file_path") else "",
            "findings": study["findings"],
        }

    def submit_assessment(
        self,
        session_id: str,
        diagnosis: str,
        management: list[str],
        wait_for_ai: bool = True,
    ) -> dict:
        """
        Resident submits their final diagnosis and management plan.
        Triggers scoring and debrief generation.

        Parameters
        ----------
        wait_for_ai : bool
            If True (default), waits for AI scoring to complete before returning.
            If False, returns rule-based scores immediately and runs AI scoring in background.

        Note: In Modal serverless mode, async scoring is unreliable because background
        tasks may be terminated when the request completes. Synchronous scoring is recommended.
        """
        session = self._get_active_session(session_id)

        # Validate minimum clinical workup before allowing submission
        min_history_questions = 3
        min_exam_systems = 0

        if len(session.history_questions) < min_history_questions:
            raise ValueError(
                f"Insufficient history taking. Please ask at least {min_history_questions} questions "
                f"before submitting (you asked {len(session.history_questions)}). "
                "Good clinical practice requires adequate history before diagnosis."
            )

        if len(session.exam_systems_viewed) < min_exam_systems:
            raise ValueError(
                f"Please perform physical examination before submitting. "
                f"Examine at least {min_exam_systems} system(s) relevant to the case."
            )

        session.diagnosis_submitted = diagnosis
        session.management_submitted = management
        session.status = "submitted"
        session.action_log.append({"type": "assessment", "detail": diagnosis, "ts": datetime.now().isoformat()})

        # Always use synchronous scoring for Modal serverless compatibility
        # Async background tasks are unreliable in serverless containers
        return self._complete_scoring(session, session_id)

    def _complete_scoring(self, session, session_id: str) -> dict:
        """Complete full scoring (rule-based + AI) synchronously."""
        from .safety import DISCLAIMER
        if session.case.status == "approved" and session.case.rubric:
            score_result = score_session(session, agent=self.agent)
            session.score = score_result.to_dict()
            session.debrief = generate_debrief(session, score_result, agent=self.agent).to_dict()
        else:
            session.score = None
            session.debrief = {
                "summary": "Practice completed. This case has not been clinically reviewed; numeric scoring is unavailable.",
                "coaching_points": ["Reflect on your differential diagnosis and the evidence for your management plan.", "Revisit this case with a clinical instructor when one is available."],
                "ai_narrative": "", "disclaimer": DISCLAIMER,
            }

        session.status = "scored"
        session.completed_at = datetime.now().isoformat()

        # Persist final session state
        save_session(session.to_dict(), actor=session.owner_id or "local")

        # Release LangChain message history — session is complete
        clear_session_history(session_id)

        return {
            "session_id": session_id,
            "submitted_at": session.completed_at,
            "scores": session.score,
            "debrief": session.debrief,
            "ai_ready": True,
            "disclaimer": DISCLAIMER,
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_active_session(self, session_id: str) -> SimulationSession:
        """Get session from cache or DB, checking it's active."""
        session = self.get_session(session_id)  # Use DB-backed method
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        if session.status == "scored":
            raise ValueError("Session already completed")
        return session

    @staticmethod
    def _strip_outer_quotes(text: str) -> str:
        """Remove a single matching quote pair around a whole response."""
        text = text.strip()
        quote_pairs = [
            ('"', '"'),
            ("'", "'"),
            ("\u201c", "\u201d"),
            ("\u2018", "\u2019"),
        ]
        for opening, closing in quote_pairs:
            if text.startswith(opening) and text.endswith(closing) and len(text) >= 2:
                return text[1:-1].strip()
        return text

    @staticmethod
    def _strip_echoed_turn_prefix(text: str) -> str:
        """Remove same-line echoed resident turns before the patient answer."""
        text = re.sub(
            r"^\s*(resident|doctor|user)\s*:\s*.*?\b(patient|assistant)\s*:\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"^\s*[\"'\u201c\u2018][^\n]{1,300}?[\"'\u201d\u2019]\s*(patient|assistant)\s*:\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r"^\s*[^:\n]{1,300}?\?\s*(patient|assistant)\s*:\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _clean_response(text: str) -> str:
        """Strip MedGemma internal thinking/planning preamble from responses."""
        text = re.sub(r"^<unused\d+>\s*", "", text.strip())
        # Strip role prefixes the model sometimes echoes ("Patient: ", "Resident: ")
        text = re.sub(r"^(patient|resident|doctor)\s*:\s*", "", text, flags=re.IGNORECASE)
        text = SimulationEngine._strip_echoed_turn_prefix(text)

        # Strip multi-turn conversation output (model continuing the dialogue)
        # Keep only the first patient response, before any "USER:" or "RESIDENT:" marker
        multi_turn_match = re.search(r"\n\s*(user|resident|doctor)\s*:", text, flags=re.IGNORECASE)
        if multi_turn_match:
            text = text[:multi_turn_match.start()].strip()

        if "\n\n" not in text:
            if text.lower().startswith("thought"):
                return SimulationEngine._strip_outer_quotes(re.sub(
                    r"^thought\b.*", "", text,
                    flags=re.IGNORECASE | re.DOTALL,
                ).strip())
            return SimulationEngine._strip_outer_quotes(text)

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
            return SimulationEngine._strip_outer_quotes(rest)

        return SimulationEngine._strip_outer_quotes(text)

    @staticmethod
    def _keyword_patient_response(case: ClinicalCase, question: str) -> str:
        """Fallback: match question to history data by keyword."""
        question_lower = question.lower()
        for key, response in case.history_data.items():
            if key.lower() in question_lower or any(
                word in question_lower for word in key.lower().split()
            ):
                return SimulationEngine._strip_outer_quotes(response)
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
