"""
Session export module.

Provides two public functions:
  build_export_dict(session)  — assemble a complete, enriched dict from a live session
  generate_pdf(data)          — render that dict as a downloadable PDF (fpdf2)
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .simulator import SimulationSession


# ── Text helpers ───────────────────────────────────────────────────────────────

def _safe(text: str) -> str:
    """Sanitize text to Latin-1 for fpdf2 built-in fonts."""
    if not text:
        return ""
    replacements = {
        "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "--", "\u00b0": " deg", "\u03bc": "u",
        "\u2265": ">=", "\u2264": "<=", "\u00b1": "+/-", "\u2022": "-",
    }
    for orig, repl in replacements.items():
        text = text.replace(orig, repl)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _strip_md(text: str) -> str:
    """Strip Markdown bold/italic/heading markers from AI-generated text."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"#+\s*", "", text)
    return text.strip()


# ── Data builder ───────────────────────────────────────────────────────────────

def build_export_dict(session: "SimulationSession") -> dict:
    """
    Build a complete, enriched export dictionary from a live SimulationSession.

    Resolves exam findings, investigation results, and imaging findings from
    the in-memory case object — these are omitted from to_dict() during active
    sessions to avoid leaking ground truth to the frontend.
    """
    case = session.case

    # Exam: system name → findings text
    exam = []
    for system in session.exam_systems_viewed:
        findings = ""
        for key, val in (case.physical_exam or {}).items():
            if system.lower() in key.lower() or key.lower() in system.lower():
                findings = val
                break
        exam.append({"system": system, "findings": findings or "Findings not recorded."})

    # Investigations: test name → result text
    investigations = []
    for name in session.investigations_ordered:
        result = ""
        for key, val in (case.investigations or {}).items():
            if name.lower() in key.lower() or key.lower() in name.lower():
                result = val
                break
        investigations.append({"investigation": name, "result": result or "Result not recorded."})

    # Imaging: only studies the resident actually viewed
    viewed_ids = set(session.imaging_studies_viewed)
    imaging = [
        {
            "study_id": s.get("study_id", ""),
            "modality": s.get("modality", ""),
            "description": s.get("description", ""),
            "findings": s.get("findings", ""),
        }
        for s in (case.imaging_studies or [])
        if s.get("study_id") in viewed_ids
    ]

    score = session.score or {}
    debrief = session.debrief or {}

    return {
        "meta": {
            "session_id": session.session_id,
            "resident_name": session.resident_name,
            "started_at": session.started_at,
            "completed_at": session.completed_at,
            "exported_at": datetime.now().isoformat(),
        },
        "case": {
            "case_id": session.case_id,
            "title": case.title,
            "specialty": case.specialty,
            "difficulty": case.difficulty,
            "source": getattr(case, "source", ""),
            "source_ref": getattr(case, "source_ref", ""),
            "presentation": case.presentation,
            "initial_vitals": case.initial_vitals,
            "learning_objectives": case.learning_objectives,
            "score_weights": getattr(case, "score_weights", {}),
        },
        "history": session.history_questions,
        "exam": exam,
        "investigations": investigations,
        "imaging": imaging,
        "assessment": {
            "diagnosis_submitted": session.diagnosis_submitted,
            "management_submitted": session.management_submitted,
            "correct_diagnosis": score.get("correct_diagnosis", ""),
            "correct_management": score.get("correct_management", []),
        },
        "scores": {
            "total": score.get("total", 0),
            "max_score": score.get("max_score", 100),
            "percentage": score.get("percentage", 0),
            "grade": score.get("grade", ""),
            "domain_scores": score.get("domain_scores", {}),
            "domain_feedback": score.get("domain_feedback", {}),
            "key_learning_points": score.get("key_learning_points", []),
            "ai_feedback": score.get("ai_feedback", ""),
        },
        "debrief": {
            "summary": debrief.get("summary", ""),
            "history_feedback": debrief.get("history_feedback", ""),
            "exam_feedback": debrief.get("exam_feedback", ""),
            "investigation_feedback": debrief.get("investigation_feedback", ""),
            "diagnosis_feedback": debrief.get("diagnosis_feedback", ""),
            "management_feedback": debrief.get("management_feedback", ""),
            "missed_opportunities": debrief.get("missed_opportunities", []),
            "cost_analysis": debrief.get("cost_analysis", ""),
            "coaching_points": debrief.get("coaching_points", []),
            "ai_narrative": debrief.get("ai_narrative", ""),
        },
    }


# ── PDF renderer ───────────────────────────────────────────────────────────────

_TEAL = (0, 150, 136)
_DARK = (33, 33, 33)
_GREY = (240, 240, 240)
_MUTED = (110, 110, 110)
_BRAND = "MedSimulation"


def generate_pdf(data: dict) -> bytes:
    """Render a session export dict as a PDF and return raw bytes."""
    from fpdf import FPDF

    class _PDF(FPDF):
        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(150, 150, 150)
            sid = data["meta"]["session_id"]
            self.cell(0, 5, _safe(f"{_BRAND}  |  Session {sid}  |  Page {self.page_no()}"), align="C")

    pdf = _PDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_margins(15, 15, 15)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def section_header(title: str):
        pdf.ln(4)
        pdf.set_fill_color(*_TEAL)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, _safe(title.upper()), fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_DARK)
        pdf.ln(2)

    def kv(label: str, value: str, label_w: int = 42):
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_MUTED)
        pdf.cell(label_w, 5, _safe(label.upper()))
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*_DARK)
        pdf.multi_cell(pdf.epw - label_w, 5, _safe(str(value)), new_x="LMARGIN", new_y="NEXT")

    def body(text: str):
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*_DARK)
        pdf.multi_cell(0, 5, _safe(_strip_md(text or "")))
        pdf.ln(1)

    # ── Cover header ───────────────────────────────────────────────────────────
    pdf.set_fill_color(*_TEAL)
    pdf.rect(0, 0, 210, 26, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_y(6)
    pdf.set_font("Helvetica", "B", 17)
    pdf.cell(0, 9, f"{_BRAND} - Clinical Training Report", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    meta = data["meta"]
    pdf.cell(0, 5,
        _safe(f"Session {meta['session_id']}  |  {meta['resident_name']}  |  "
              f"Completed {meta['completed_at'][:10] if meta['completed_at'] else 'N/A'}"),
        align="C", new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(*_DARK)
    pdf.set_y(33)

    # ── Score hero ─────────────────────────────────────────────────────────────
    sc = data["scores"]
    pct = sc.get("percentage", 0)
    pdf.set_font("Helvetica", "B", 34)
    pdf.set_text_color(*_TEAL)
    pdf.cell(0, 14, f"{pct:.0f}%", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*_MUTED)
    pdf.cell(0, 5,
        _safe(f"{sc.get('grade', '')}  ({sc.get('total', 0)}/{sc.get('max_score', 100)})"),
        align="C", new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(*_DARK)
    pdf.ln(3)

    # ── Case overview ──────────────────────────────────────────────────────────
    section_header("Case Overview")
    case = data["case"]
    kv("Title", case["title"])
    kv("Specialty", f"{case['specialty']}   Difficulty: {case['difficulty']}")
    if case.get("source_ref"):
        kv("Source", f"{case.get('source', '').upper()} — {case['source_ref']}")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_MUTED)
    pdf.cell(0, 5, "PRESENTATION", new_x="LMARGIN", new_y="NEXT")
    body(case["presentation"])

    vitals = case.get("initial_vitals") or {}
    if vitals:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_MUTED)
        pdf.cell(0, 5, "VITALS", new_x="LMARGIN", new_y="NEXT")
        vline = "   ".join(f"{k}: {v}" for k, v in vitals.items())
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*_DARK)
        pdf.multi_cell(0, 5, _safe(vline))
        pdf.ln(1)

    # ── History taking ─────────────────────────────────────────────────────────
    section_header(f"History Taking  ({len(data['history'])} questions)")
    for i, qa in enumerate(data["history"], 1):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_TEAL)
        pdf.cell(8, 5, f"{i}.")
        pdf.set_text_color(*_DARK)
        pdf.multi_cell(0, 5, _safe(qa.get("question", "")))
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*_MUTED)
        pdf.set_x(23)
        pdf.multi_cell(0, 5, _safe(qa.get("response", "")))
        pdf.set_text_color(*_DARK)
        pdf.ln(1)

    # ── Physical exam ──────────────────────────────────────────────────────────
    section_header(f"Physical Examination  ({len(data['exam'])} systems)")
    for entry in data["exam"]:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, _safe(entry["system"]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*_MUTED)
        pdf.set_x(20)
        pdf.multi_cell(0, 5, _safe(entry["findings"]))
        pdf.set_text_color(*_DARK)
        pdf.ln(1)

    # ── Investigations ─────────────────────────────────────────────────────────
    section_header(f"Investigations  ({len(data['investigations'])} ordered)")
    for entry in data["investigations"]:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(65, 5, _safe(entry["investigation"]))
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*_MUTED)
        pdf.multi_cell(pdf.epw - 65, 5, _safe(entry["result"]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_DARK)

    if data["imaging"]:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_MUTED)
        pdf.cell(0, 5, "IMAGING STUDIES VIEWED", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_DARK)
        for img in data["imaging"]:
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(0, 5, _safe(f"[{img['modality']}] {img['description']}"), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*_MUTED)
            pdf.set_x(20)
            pdf.multi_cell(0, 5, _safe(img["findings"]))
            pdf.set_text_color(*_DARK)
            pdf.ln(1)

    # ── Assessment ─────────────────────────────────────────────────────────────
    section_header("Assessment Submitted")
    asmt = data["assessment"]
    kv("Diagnosis", asmt.get("diagnosis_submitted", "—"))
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_MUTED)
    pdf.cell(0, 5, "MANAGEMENT PLAN", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_DARK)
    for step in (asmt.get("management_submitted") or []):
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(6, 5, "-")
        pdf.multi_cell(pdf.epw - 6, 5, _safe(step), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    kv("Correct Diagnosis", asmt.get("correct_diagnosis", ""))

    # ── Domain score table ─────────────────────────────────────────────────────
    section_header("Score Breakdown")
    domain_scores = sc.get("domain_scores", {})
    domain_feedback = sc.get("domain_feedback", {})
    weight = data["case"].get("score_weights") or {
        "history": 20, "exam": 20, "investigations": 20, "diagnosis": 25, "management": 15,
    }
    domains = [
        ("History", "history"), ("Physical Exam", "exam"),
        ("Investigations", "investigations"), ("Diagnosis", "diagnosis"),
        ("Management", "management"),
    ]
    pdf.set_fill_color(*_GREY)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(42, 7, "Domain", border=1, fill=True)
    pdf.cell(18, 7, "Score", border=1, fill=True, align="C")
    pdf.cell(14, 7, "Max", border=1, fill=True, align="C")
    pdf.cell(0, 7, "Feedback", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")
    for label, key in domains:
        got = domain_scores.get(key, 0)
        max_w = weight.get(key, 20)
        fb = (domain_feedback.get(key) or "")[:110]
        if len(domain_feedback.get(key, "")) > 110:
            fb += "..."
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(42, 6, _safe(label), border=1)
        pdf.cell(18, 6, str(got), border=1, align="C")
        pdf.cell(14, 6, str(max_w), border=1, align="C")
        pdf.multi_cell(pdf.epw - 74, 6, _safe(fb), border=1, new_x="LMARGIN", new_y="NEXT")

    # ── Debrief ────────────────────────────────────────────────────────────────
    section_header("Debrief")
    deb = data["debrief"]
    if deb.get("summary"):
        body(deb["summary"])

    for label, key in [
        ("History", "history_feedback"), ("Exam", "exam_feedback"),
        ("Investigations", "investigation_feedback"),
        ("Diagnosis", "diagnosis_feedback"), ("Management", "management_feedback"),
    ]:
        val = deb.get(key, "")
        if val:
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(0, 5, _safe(label + ":"), new_x="LMARGIN", new_y="NEXT")
            body(val)

    if deb.get("missed_opportunities"):
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, "Missed Opportunities:", new_x="LMARGIN", new_y="NEXT")
        for opp in deb["missed_opportunities"]:
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(6, 5, "-")
            pdf.multi_cell(pdf.epw - 6, 5, _safe(opp), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    if deb.get("coaching_points"):
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 5, "Coaching Points:", new_x="LMARGIN", new_y="NEXT")
        for pt in deb["coaching_points"]:
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(6, 5, "*")
            pdf.multi_cell(pdf.epw - 6, 5, _safe(pt), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    if deb.get("ai_narrative"):
        pdf.ln(2)
        pdf.set_fill_color(230, 245, 242)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 6, "AI Senior Clinician Narrative", fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*_MUTED)
        pdf.multi_cell(0, 5, _safe(_strip_md(deb["ai_narrative"])))
        pdf.set_text_color(*_DARK)

    # ── Key learning points ────────────────────────────────────────────────────
    klp = sc.get("key_learning_points") or []
    if klp:
        section_header("Key Learning Points")
        for pt in klp:
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(6, 5, "-")
            pdf.multi_cell(pdf.epw - 6, 5, _safe(pt), new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())
