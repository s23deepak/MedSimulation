"""Tests for src.simulation.scorer — rule-based scoring engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from simulation.cases import get_case
from simulation.scorer import score_session, _rule_based_score, _grade, ScoreResult


# ── Mock session ──────────────────────────────────────────────────────────────

class _MockSession:
    """Minimal simulation session for testing."""

    def __init__(self, case_id, *, questions=None, exams=None, invs=None, img=None, diag="", mgmt=None):
        self.case = get_case(case_id)
        self.history_questions = questions or []
        self.exam_systems_viewed = exams or []
        self.investigations_ordered = invs or []
        self.imaging_studies_viewed = img or []
        self.diagnosis_submitted = diag
        self.management_submitted = mgmt or []


# ── Helper ────────────────────────────────────────────────────────────────────

def _stemi_session(**kw):
    return _MockSession("SIM-001", **kw)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_grade_thresholds():
    assert _grade(0.90) == "Excellent"
    assert _grade(0.85) == "Excellent"
    assert _grade(0.84) == "Satisfactory"
    assert _grade(0.70) == "Satisfactory"
    assert _grade(0.69) == "Borderline"
    assert _grade(0.50) == "Borderline"
    assert _grade(0.49) == "Unsatisfactory"
    assert _grade(0.0) == "Unsatisfactory"


def test_empty_submission_scores_zero():
    s = _stemi_session()
    result = score_session(s)
    assert isinstance(result, ScoreResult)
    assert result.total == 0
    assert result.percentage == 0
    assert result.grade == "Unsatisfactory"


def test_perfect_diagnosis_scores_full():
    s = _stemi_session(diag="Inferior STEMI with right ventricular involvement and cardiogenic shock")
    result = score_session(s)
    assert result.domain_scores["diagnosis"] == 25
    assert "Correct" in result.domain_feedback["diagnosis"]


def test_partial_diagnosis_gets_partial_credit():
    s = _stemi_session(diag="STEMI")
    result = score_session(s)
    assert result.domain_scores["diagnosis"] > 0
    assert result.domain_scores["diagnosis"] <= 25


def test_wrong_diagnosis_scores_zero():
    s = _stemi_session(diag="plantar fasciitis bilateral")
    result = score_session(s)
    assert result.domain_scores["diagnosis"] == 0
    assert "Incorrect" in result.domain_feedback["diagnosis"]


def test_history_questions_contribute():
    s = _stemi_session(
        questions=[
            {"question": "Tell me about the pain", "response": "..."},
            {"question": "Any cardiac history?", "response": "..."},
            {"question": "What medications do you take?", "response": "..."},
        ],
    )
    result = score_session(s)
    assert result.domain_scores["history"] > 0


def test_exam_systems_contribute():
    s = _stemi_session(exams=["Cardiovascular", "Respiratory"])
    result = score_session(s)
    assert result.domain_scores["exam"] > 0


def test_investigations_contribute():
    s = _stemi_session(invs=["12-lead ECG", "Troponin I", "CXR"])
    result = score_session(s)
    assert result.domain_scores["investigations"] > 0


def test_management_matching():
    s = _stemi_session(
        diag="STEMI",
        mgmt=[
            "Activate cath lab immediately",
            "Aspirin 300mg",
            "Ticagrelor 180mg",
            "IV heparin bolus",
        ],
    )
    result = score_session(s)
    assert result.domain_scores["management"] > 0


def test_score_result_to_dict():
    s = _stemi_session(diag="STEMI")
    result = score_session(s)
    d = result.to_dict()
    assert isinstance(d, dict)
    assert "total" in d
    assert "grade" in d
    assert "domain_scores" in d
    assert "correct_diagnosis" in d
    assert "key_learning_points" in d


def test_max_score_is_100():
    s = _stemi_session()
    result = score_session(s)
    assert result.max_score == 100


def test_all_five_domains_present():
    s = _stemi_session()
    result = score_session(s)
    expected = {"history", "exam", "investigations", "diagnosis", "management"}
    assert set(result.domain_scores.keys()) == expected
    assert set(result.domain_feedback.keys()) == expected
