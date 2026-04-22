"""
Five-domain scoring engine for clinical simulation.

Evaluates resident performance across:
  1. History Taking   (completeness of HPI, ROS, PMH capture)
  2. Physical Exam    (appropriate exam findings documented)
  3. Investigations   (logical test ordering, cost-effectiveness)
  4. Diagnosis        (differential generation + final accuracy)
  5. Management       (treatment plan alignment with guidelines)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Tutor scoring prompt ─────────────────────────────────────────────────────

TUTOR_SCORING_PROMPT = """\
You are a senior clinician and medical educator assessing a resident's performance in a clinical simulation.

CASE: {case_title}
CORRECT DIAGNOSIS: {correct_diagnosis}
CORRECT MANAGEMENT STEPS: {correct_management}

RESIDENT PERFORMANCE:
History questions asked: {history_questions}
Examination systems reviewed: {exam_systems}
Investigations ordered: {investigations}
Imaging viewed: {imaging}
Diagnosis submitted: {diagnosis_submitted}
Management plan submitted: {management_submitted}

CHRONOLOGICAL ACTION SEQUENCE (important for assessing clinical reasoning order):
{action_sequence}

Score each domain on the given maximum points and provide specific feedback:

1. HISTORY TAKING (max {w_history} pts): Did the resident ask the key discriminating questions?
2. PHYSICAL EXAMINATION (max {w_exam} pts): Did they examine the relevant systems? Was exam performed AFTER adequate history taking?
3. INVESTIGATIONS (max {w_inv} pts): Were the key investigations ordered? Any unnecessary ones?
4. DIAGNOSIS (max {w_diag} pts): Is the diagnosis correct or partially correct?
5. MANAGEMENT (max {w_mgmt} pts): Are the management steps appropriate and complete?

Note: Penalise if physical examination was performed before the patient had a chance to explain their symptoms (fewer than 3 history questions asked first). Good clinical practice requires history before examination.

Also provide:
- OVERALL FEEDBACK: 2-3 sentences of constructive summary
- MISSED DIAGNOSES: Any important differentials they should have considered
- CRITICAL ERRORS: Any dangerous or harmful decisions made
- KEY LEARNING POINTS: 2-3 most important teaching points for this case

Format your response as structured text with clear section headers.
"""


# ── Score result dataclass ────────────────────────────────────────────────────

@dataclass
class ScoreResult:
    """Structured scoring output for a simulation session."""

    total: int
    max_score: int
    percentage: int
    grade: str                            # Excellent | Satisfactory | Borderline | Unsatisfactory
    domain_scores: dict[str, int] = field(default_factory=dict)
    domain_feedback: dict[str, str] = field(default_factory=dict)
    correct_diagnosis: str = ""
    correct_management: list[str] = field(default_factory=list)
    key_learning_points: list[str] = field(default_factory=list)
    ai_feedback: str = ""

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "max_score": self.max_score,
            "percentage": self.percentage,
            "grade": self.grade,
            "domain_scores": self.domain_scores,
            "domain_feedback": self.domain_feedback,
            "correct_diagnosis": self.correct_diagnosis,
            "correct_management": self.correct_management,
            "key_learning_points": self.key_learning_points,
            "ai_feedback": self.ai_feedback,
        }


# ── Grade helper ──────────────────────────────────────────────────────────────

def _grade(ratio: float) -> str:
    if ratio >= 0.85:
        return "Excellent"
    if ratio >= 0.70:
        return "Satisfactory"
    if ratio >= 0.50:
        return "Borderline"
    return "Unsatisfactory"


# ── Public entry point ────────────────────────────────────────────────────────

def score_session(session: Any, agent: Any = None) -> ScoreResult:
    """
    Score a simulation session.

    Uses AI scoring (MedGemma) when *agent* is provided,
    falling back to deterministic rule-based scoring otherwise.
    """
    if agent is not None:
        return _ai_score(session, agent)
    return _rule_based_score(session)


# ── AI scoring ────────────────────────────────────────────────────────────────

def _ai_score(session: Any, agent: Any) -> ScoreResult:
    case = session.case
    w = case.score_weights
    action_log = getattr(session, "action_log", [])
    action_sequence = "\n".join(
        f"[{e['ts'][11:19]}] {e['type'].upper()}: {e['detail']}" for e in action_log
    ) or "No actions recorded."
    prompt = TUTOR_SCORING_PROMPT.format(
        case_title=case.title,
        correct_diagnosis=case.correct_diagnosis,
        correct_management="\n".join(f"- {m}" for m in case.correct_management),
        history_questions="\n".join(
            f"- Q: {h['question']} | A: {h['response']}" for h in session.history_questions
        ),
        exam_systems=", ".join(session.exam_systems_viewed) or "None",
        investigations=", ".join(session.investigations_ordered) or "None",
        imaging=", ".join(session.imaging_studies_viewed) or "None",
        diagnosis_submitted=session.diagnosis_submitted or "Not submitted",
        management_submitted="\n".join(
            f"- {m}" for m in session.management_submitted
        ) or "Not submitted",
        action_sequence=action_sequence,
        w_history=w["history"],
        w_exam=w["exam"],
        w_inv=w["investigations"],
        w_diag=w["diagnosis"],
        w_mgmt=w["management"],
    )
    try:
        if hasattr(agent, "process_query"):
            result = agent.process_query(query=prompt, patient_context={})
            feedback_text = result.get("response", "")
        elif hasattr(agent, "chat"):
            feedback_text = _clean_ai_text(agent.chat(prompt))
        else:
            feedback_text = ""

        # Validate AI output - reject code snippets or garbage
        if feedback_text:
            # Check for code-like output (Python keywords, def statements, etc.)
            code_indicators = ["def ", "import ", "print(", "```python"]
            is_code = any(indicator in feedback_text for indicator in code_indicators)

            # Check for minimum meaningful content (at least 50 chars and contains sentences)
            is_too_short = len(feedback_text.strip()) < 50
            has_sentences = "." in feedback_text or "!" in feedback_text

            if is_code or is_too_short or not has_sentences:
                logger.warning("AI feedback rejected (garbage output): %s...", feedback_text[:100])
                feedback_text = ""  # Fall back to rule-based only

        if feedback_text:
            numeric = _rule_based_score(session)
            return ScoreResult(
                total=numeric.total,
                max_score=numeric.max_score,
                percentage=numeric.percentage,
                grade=numeric.grade,
                domain_scores=numeric.domain_scores,
                domain_feedback=numeric.domain_feedback,
                correct_diagnosis=numeric.correct_diagnosis,
                correct_management=numeric.correct_management,
                key_learning_points=case.key_learning_points,
                ai_feedback=feedback_text,
            )
    except Exception as e:
        logger.warning("AI scoring failed, falling back to rule-based: %s", e)

    return _rule_based_score(session)


# ── Rule-based scoring ────────────────────────────────────────────────────────

def _rule_based_score(session: Any) -> ScoreResult:
    """Deterministic scoring based on keyword matching."""
    case = session.case
    w = case.score_weights
    scores: dict[str, int] = {}
    feedback: dict[str, str] = {}

    # History (did they ask about key topics?)
    key_history_keywords = list(case.history_data.keys())[:8]
    questions_text = " ".join(h["question"].lower() for h in session.history_questions)
    history_hits = sum(1 for kw in key_history_keywords if kw.lower() in questions_text)
    scores["history"] = round(
        min(w["history"], (history_hits / max(len(key_history_keywords), 1)) * w["history"])
    )
    feedback["history"] = (
        f"Asked {len(session.history_questions)} questions, "
        f"covering {history_hits}/{len(key_history_keywords)} key areas."
    )

    # Physical Exam
    key_systems = list(case.physical_exam.keys())
    exam_hits = sum(1 for s in key_systems if s in session.exam_systems_viewed)
    scores["exam"] = round(
        min(w["exam"], (exam_hits / max(len(key_systems), 1)) * w["exam"])
    )
    exam_feedback = f"Examined {len(session.exam_systems_viewed)}/{len(key_systems)} relevant systems."

    # Check ordering: how many history questions before first physical exam?
    action_log = getattr(session, "action_log", [])
    if session.exam_systems_viewed and action_log:
        history_before_exam = 0
        for entry in action_log:
            if entry["type"] == "history":
                history_before_exam += 1
            elif entry["type"] == "exam":
                break
        if history_before_exam < 3:
            exam_feedback += (
                f" Warning: Physical exam performed after only {history_before_exam} "
                "history question(s) — adequate history should precede examination."
            )
            scores["exam"] = round(scores["exam"] * 0.7)
    feedback["exam"] = exam_feedback

    # Investigations (& Imaging)
    key_invs = list(case.investigations.keys())[:6]
    key_img = [s["study_id"] for s in (case.imaging_studies or [])]
    
    total_expected = len(key_invs) + len(key_img)
    inv_hits = sum(1 for i in key_invs if i in session.investigations_ordered)
    img_hits = sum(1 for i in key_img if i in session.imaging_studies_viewed)
    
    total_hits = inv_hits + img_hits
    
    scores["investigations"] = round(
        min(w["investigations"], (total_hits / max(total_expected, 1)) * w["investigations"])
    )
    feedback["investigations"] = (
        f"Ordered {len(session.investigations_ordered)} investigations and viewed "
        f"{len(session.imaging_studies_viewed)} imaging studies. "
        f"Covered {total_hits}/{total_expected} key diagnostics."
    )

    # Diagnosis
    diag_lower = session.diagnosis_submitted.lower()
    correct_lower = case.correct_diagnosis.lower()
    acceptable_lower = [d.lower() for d in case.acceptable_diagnoses]
    if any(term in diag_lower for term in correct_lower.split()):
        diag_score = w["diagnosis"]
        feedback["diagnosis"] = "Correct diagnosis identified."
    elif any(
        any(term in diag_lower for term in acc.split()) for acc in acceptable_lower
    ):
        diag_score = round(w["diagnosis"] * 0.6)
        feedback["diagnosis"] = "Partially correct — core diagnosis captured but incomplete detail."
    else:
        diag_score = 0
        feedback["diagnosis"] = f"Incorrect. Correct diagnosis: {case.correct_diagnosis}"
    scores["diagnosis"] = diag_score

    # Management
    mgmt_text = " ".join(session.management_submitted).lower()
    mgmt_hits = sum(
        1
        for step in case.correct_management
        if any(word in mgmt_text for word in step.lower().split()[:4])
    )
    scores["management"] = round(
        min(w["management"], (mgmt_hits / max(len(case.correct_management), 1)) * w["management"])
    )
    feedback["management"] = (
        f"Covered {mgmt_hits}/{len(case.correct_management)} management steps."
    )

    total = sum(scores.values())
    max_score = sum(w.values())

    return ScoreResult(
        total=total,
        max_score=max_score,
        percentage=round((total / max_score) * 100),
        grade=_grade(total / max_score),
        domain_scores=scores,
        domain_feedback=feedback,
        correct_diagnosis=case.correct_diagnosis,
        correct_management=case.correct_management,
        key_learning_points=case.key_learning_points,
        ai_feedback="",
    )


# ── Text cleaning ─────────────────────────────────────────────────────────────

def _clean_ai_text(text: str) -> str:
    """Strip MedGemma internal preamble from AI responses."""
    text = re.sub(r"^<unused\d+>\s*", "", text.strip())
    if "\n\n" not in text:
        return text
    first_para, rest = text.split("\n\n", 1)
    first_lower = first_para.lower()
    preamble_signals = [
        first_lower.startswith("thought"),
        "i need to respond" in first_lower,
        "let me think" in first_lower,
        bool(re.search(r"^\s*1\.", first_para, re.MULTILINE)),
    ]
    if any(preamble_signals) and rest.strip():
        return rest.strip()
    return text
