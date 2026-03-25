"""
Debrief generator for clinical simulation.

Produces structured narrative feedback after scoring, including:
  - Per-domain strengths and gaps
  - Missed diagnostic opportunities
  - Cost-benefit analysis
  - Evidence-based guideline deviations
  - Coaching recommendations for next case
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .scorer import ScoreResult

logger = logging.getLogger(__name__)


# ── Debrief prompt template ──────────────────────────────────────────────────

DEBRIEF_PROMPT = """\
You are an experienced clinical educator providing a debrief after a medical simulation.

## Case Details
- Case: {case_title}
- Correct Diagnosis: {correct_diagnosis}

## Resident's Performance Summary
- Overall Score: {percentage}% ({grade})
- History Score: {history_score}/{w_history}
- Examination Score: {exam_score}/{w_exam}
- Investigations Score: {inv_score}/{w_inv}
- Diagnosis Score: {diag_score}/{w_diag}
- Management Score: {mgmt_score}/{w_mgmt}

## Resident's Submissions
- Diagnosis: {diagnosis_submitted}
- Management Plan: {management_submitted}

## Correct Management Steps
{correct_management}

Please provide a structured debrief with the following sections:

1. **Summary**: 2-3 sentence overall assessment
2. **History Feedback**: What was done well and what was missed in history taking
3. **Exam Feedback**: Physical examination strengths and gaps
4. **Investigation Feedback**: Appropriateness of investigations ordered, cost considerations
5. **Diagnosis Feedback**: Assessment of diagnostic reasoning
6. **Management Feedback**: Treatment plan analysis
7. **Missed Opportunities**: Important things the resident did not consider
8. **Coaching Points**: 2-3 actionable learning points for next time

Be constructive, specific, and educational in your feedback.
"""


# ── Debrief result dataclass ─────────────────────────────────────────────────

@dataclass
class DebriefResult:
    """Structured debrief output for a simulation session."""

    summary: str = ""
    history_feedback: str = ""
    exam_feedback: str = ""
    investigation_feedback: str = ""
    diagnosis_feedback: str = ""
    management_feedback: str = ""
    missed_opportunities: list[str] = field(default_factory=list)
    cost_analysis: str = ""
    coaching_points: list[str] = field(default_factory=list)
    ai_narrative: str = ""   # Full AI-generated narrative (if available)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "history_feedback": self.history_feedback,
            "exam_feedback": self.exam_feedback,
            "investigation_feedback": self.investigation_feedback,
            "diagnosis_feedback": self.diagnosis_feedback,
            "management_feedback": self.management_feedback,
            "missed_opportunities": self.missed_opportunities,
            "cost_analysis": self.cost_analysis,
            "coaching_points": self.coaching_points,
            "ai_narrative": self.ai_narrative,
        }


# ── Public entry point ────────────────────────────────────────────────────────

def generate_debrief(
    session: Any,
    score_result: ScoreResult,
    agent: Any = None,
) -> DebriefResult:
    """
    Generate a structured debrief from a scored simulation session.

    Uses AI (MedGemma) when *agent* is available, otherwise produces
    deterministic rule-based feedback based on the score result and case data.
    """
    # Always start from rule-based debrief
    debrief = _rule_based_debrief(session, score_result)

    # Enhance with AI if available
    if agent is not None:
        try:
            ai_text = _generate_ai_debrief(session, score_result, agent)
            if ai_text:
                debrief.ai_narrative = ai_text
        except Exception as e:
            logger.warning("AI debrief generation failed (rule-based used): %s", e)

    return debrief


# ── Rule-based debrief ───────────────────────────────────────────────────────

def _rule_based_debrief(session: Any, score: ScoreResult) -> DebriefResult:
    """Generate deterministic feedback from scores and case data."""
    case = session.case

    # Summary
    if score.percentage >= 85:
        summary = (
            f"Excellent performance on '{case.title}'. "
            "Strong clinical reasoning demonstrated across all domains. "
            "Minor opportunities for improvement identified below."
        )
    elif score.percentage >= 70:
        summary = (
            f"Satisfactory performance on '{case.title}'. "
            "Core competencies demonstrated with some areas for improvement. "
            "Review the feedback below for targeted development."
        )
    elif score.percentage >= 50:
        summary = (
            f"Borderline performance on '{case.title}'. "
            "Some key clinical steps were missed. "
            "Focused review of the areas below is recommended before reattempting."
        )
    else:
        summary = (
            f"This case '{case.title}' needs further review. "
            "Several critical clinical steps were not completed. "
            "Consider reviewing the learning points and attempting again."
        )

    # Per-domain feedback (pulled from scorer output)
    history_fb = score.domain_feedback.get("history", "")
    exam_fb = score.domain_feedback.get("exam", "")
    inv_fb = score.domain_feedback.get("investigations", "")
    diag_fb = score.domain_feedback.get("diagnosis", "")
    mgmt_fb = score.domain_feedback.get("management", "")

    # Missed opportunities — derive from what wasn't done
    missed: list[str] = []
    key_systems = list(case.physical_exam.keys())
    missed_systems = [s for s in key_systems if s not in session.exam_systems_viewed]
    if missed_systems:
        missed.append(f"Exam systems not checked: {', '.join(missed_systems[:3])}")

    key_invs = list(case.investigations.keys())[:6]
    missed_invs = [i for i in key_invs if i not in session.investigations_ordered]
    if missed_invs:
        missed.append(f"Key investigations not ordered: {', '.join(missed_invs[:3])}")

    if score.domain_scores.get("diagnosis", 0) == 0:
        missed.append(f"Correct diagnosis was: {case.correct_diagnosis}")

    # Cost analysis
    inv_count = len(session.investigations_ordered)
    if inv_count <= 3:
        cost_msg = f"Total investigations ordered: {inv_count}. Conservative approach — ensure key tests are not omitted."
    elif inv_count <= 6:
        cost_msg = f"Total investigations ordered: {inv_count}. Appropriate and cost-effective workup."
    else:
        cost_msg = f"Total investigations ordered: {inv_count}. Consider whether all tests were necessary."

    # Coaching points — always include key learning points from the case
    coaching = case.key_learning_points[:3]

    return DebriefResult(
        summary=summary,
        history_feedback=history_fb,
        exam_feedback=exam_fb,
        investigation_feedback=inv_fb,
        diagnosis_feedback=diag_fb,
        management_feedback=mgmt_fb,
        missed_opportunities=missed,
        cost_analysis=cost_msg,
        coaching_points=coaching,
    )


# ── AI debrief ────────────────────────────────────────────────────────────────

def _generate_ai_debrief(
    session: Any,
    score: ScoreResult,
    agent: Any,
) -> str:
    """Generate rich narrative debrief via MedGemma."""
    case = session.case
    w = case.score_weights
    prompt = DEBRIEF_PROMPT.format(
        case_title=case.title,
        correct_diagnosis=case.correct_diagnosis,
        percentage=score.percentage,
        grade=score.grade,
        history_score=score.domain_scores.get("history", 0),
        w_history=w["history"],
        exam_score=score.domain_scores.get("exam", 0),
        w_exam=w["exam"],
        inv_score=score.domain_scores.get("investigations", 0),
        w_inv=w["investigations"],
        diag_score=score.domain_scores.get("diagnosis", 0),
        w_diag=w["diagnosis"],
        mgmt_score=score.domain_scores.get("management", 0),
        w_mgmt=w["management"],
        diagnosis_submitted=session.diagnosis_submitted or "Not submitted",
        management_submitted="\n".join(
            f"- {m}" for m in session.management_submitted
        ) or "Not submitted",
        correct_management="\n".join(f"- {m}" for m in case.correct_management),
    )

    if hasattr(agent, "process_query"):
        result = agent.process_query(query=prompt, patient_context={})
        return result.get("response", "")
    elif hasattr(agent, "chat"):
        text = agent.chat(prompt)
        return _clean_text(text)
    return ""


def _clean_text(text: str) -> str:
    """Strip MedGemma preamble noise."""
    text = re.sub(r"^<unused\d+>\s*", "", text.strip())
    if "\n\n" not in text:
        return text
    first_para, rest = text.split("\n\n", 1)
    if first_para.lower().startswith("thought") or "let me" in first_para.lower():
        return rest.strip()
    return text
