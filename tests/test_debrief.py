"""Tests for src.simulation.debrief — narrative feedback generator."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from simulation.cases import get_case
from simulation.scorer import score_session, ScoreResult
from simulation.debrief import generate_debrief, DebriefResult


# ── Mock session ──────────────────────────────────────────────────────────────

class _MockSession:
    def __init__(self, case_id, *, questions=None, exams=None, invs=None, img=None, diag="", mgmt=None):
        self.case = get_case(case_id)
        self.history_questions = questions or []
        self.exam_systems_viewed = exams or []
        self.investigations_ordered = invs or []
        self.imaging_studies_viewed = img or []
        self.diagnosis_submitted = diag
        self.management_submitted = mgmt or []


def _make_scored_session(case_id="SIM-001", **kw):
    session = _MockSession(case_id, **kw)
    score = score_session(session)
    return session, score


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_debrief_returns_result():
    session, score = _make_scored_session()
    debrief = generate_debrief(session, score)
    assert isinstance(debrief, DebriefResult)


def test_debrief_has_summary():
    session, score = _make_scored_session()
    debrief = generate_debrief(session, score)
    assert len(debrief.summary) > 20


def test_debrief_excellent_summary():
    session, score = _make_scored_session(
        diag="Inferior STEMI with right ventricular involvement and cardiogenic shock",
        questions=[{"question": q, "response": "..."} for q in ["pain", "onset", "cardiac history", "medications", "allergies", "smoking", "diabetes", "vomiting"]],
        exams=["General", "Cardiovascular", "Respiratory", "Abdomen", "Neurological", "Extremities"],
        invs=["12-lead ECG", "Troponin I", "FBC", "U&E", "CXR", "ABG"],
        mgmt=["Activate cath lab", "Aspirin 300mg", "Ticagrelor", "IV heparin", "Avoid nitrates", "IV fluid", "Oxygen", "Morphine", "Metoclopramide", "Senior cardiology"],
    )
    debrief = generate_debrief(session, score)
    assert "Excellent" in debrief.summary or "excellent" in debrief.summary.lower()


def test_debrief_has_coaching_points():
    session, score = _make_scored_session()
    debrief = generate_debrief(session, score)
    assert len(debrief.coaching_points) > 0


def test_debrief_has_cost_analysis():
    session, score = _make_scored_session(invs=["ECG", "CXR"])
    debrief = generate_debrief(session, score)
    assert len(debrief.cost_analysis) > 0


def test_debrief_missed_opportunities_when_incomplete():
    session, score = _make_scored_session()
    debrief = generate_debrief(session, score)
    assert len(debrief.missed_opportunities) > 0


def test_debrief_to_dict():
    session, score = _make_scored_session()
    debrief = generate_debrief(session, score)
    d = debrief.to_dict()
    assert isinstance(d, dict)
    assert "summary" in d
    assert "coaching_points" in d
    assert "missed_opportunities" in d
    assert "cost_analysis" in d


def test_debrief_domain_feedback_populated():
    session, score = _make_scored_session()
    debrief = generate_debrief(session, score)
    assert debrief.history_feedback != "" or debrief.exam_feedback != ""
