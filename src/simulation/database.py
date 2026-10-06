"""Versioned case review, session persistence and audit repository."""
import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from .bandits import BanditState, ThompsonSamplingBandit
from .cases import CASES, ClinicalCase, _register
from .migrations import migrate
from .safety import check_case
from .storage import Connection, engine_for

_DB_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parents[2] / "data/medsim.db"))
_initialized = set()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
 case_id TEXT PRIMARY KEY, title TEXT NOT NULL, specialty TEXT DEFAULT '',
 difficulty TEXT DEFAULT 'intermediate', source TEXT DEFAULT 'static', source_ref TEXT DEFAULT '',
 case_data TEXT NOT NULL, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sessions (
 session_id TEXT PRIMARY KEY, resident_name TEXT DEFAULT '', case_id TEXT REFERENCES cases(case_id),
 session_data TEXT, score_data TEXT, debrief_data TEXT, status TEXT DEFAULT 'active',
 started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS bandit_state (arm_id TEXT PRIMARY KEY, alpha INTEGER DEFAULT 1, beta INTEGER DEFAULT 1);
CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
"""

def now():
    return datetime.now(timezone.utc).isoformat()

def decode(value):
    return json.loads(value) if isinstance(value, str) else value

def _ensure_db():
    key = (os.getenv("DATABASE_URL"), _DB_PATH)
    if key in _initialized:
        return
    if not os.getenv("DATABASE_URL"):
        Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    migrate(engine_for(_DB_PATH), _SCHEMA)
    _initialized.add(key)

@contextmanager
def _connect():
    with engine_for(_DB_PATH).begin() as conn:
        yield Connection(conn)

def audit(actor, event_type, subject_id, payload, conn=None):
    args = (uuid.uuid4().hex, actor, event_type, subject_id, json.dumps(payload), now())
    sql = "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)"
    if conn is not None:
        conn.execute(sql, args)
    else:
        with _connect() as connection:
            connection.execute(sql, args)

def case_record(case_id):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    if not row:
        return None
    data = decode(row["case_data"])
    for key in ("source", "source_ref", "status", "version", "reviewer", "approved_at", "review_notes"):
        data[key] = row[key]
    return data

def save_case(case_data, source="ai_generated", source_ref="", status="pending", expected_version=None):
    # Approval only happens in the review transaction, never through import metadata.
    check_case(case_data)
    _ensure_db()
    case_id = case_data["case_id"]
    clean = {k: v for k, v in case_data.items() if k in ClinicalCase.__dataclass_fields__}
    ClinicalCase(**clean)
    with _connect() as conn:
        sql = """INSERT INTO cases (case_id,title,specialty,difficulty,source,source_ref,case_data,status)
            VALUES (?,?,?,?,?,?,?,'pending') ON CONFLICT(case_id) DO UPDATE SET
            title=excluded.title,specialty=excluded.specialty,difficulty=excluded.difficulty,
            source=excluded.source,source_ref=excluded.source_ref,case_data=excluded.case_data,
            status='pending',version=cases.version+1,reviewer=NULL,approved_at=NULL,review_notes=NULL"""
        if expected_version is not None:
            sql += " WHERE cases.version=?"
        args = (case_id, clean["title"], clean["specialty"], clean["difficulty"], source, source_ref, json.dumps(clean))
        result = conn.execute(sql, args + ((expected_version,) if expected_version is not None else ()))
        if not result.rowcount:
            raise ValueError("Case changed during edit")
        audit("import", "case_saved", case_id, {"source": source}, conn)
    register_case_from_db(case_id)
    return case_id

def load_dynamic_cases(status="approved"):
    with _connect() as conn:
        ids = conn.execute("SELECT case_id FROM cases WHERE status=?", (status,)).fetchall()
    return [case_record(row["case_id"]) for row in ids]

def get_pending_cases():
    return load_dynamic_cases("pending")

def approve_case(case_id, reviewer, version, notes, rubric):
    from .rubrics import validate_rubric
    validate_rubric(rubric)
    data = case_record(case_id)
    if not data or data["status"] != "pending" or data["version"] != version:
        return False
    check_case(data)
    weights = data.get("score_weights", {})
    if set(weights) != {"history", "exam", "investigations", "diagnosis", "management"} or any(not isinstance(value, int) or value <= 0 for value in weights.values()) or sum(weights.values()) != 100:
        raise ValueError("Scoring weights must provide five positive domains totaling 100")
    if "pending" in data["correct_diagnosis"].lower() or "TBD" in data.get("acceptable_diagnoses", []):
        raise ValueError("Complete the diagnosis and rubric before approval")
    data.update(status="approved", reviewer=reviewer, approved_at=now(), review_notes=notes, rubric=rubric)
    with _connect() as conn:
        result = conn.execute("""UPDATE cases SET status='approved', reviewer=?, approved_at=?, review_notes=?, case_data=?
            WHERE case_id=? AND version=? AND status='pending'""",
            (reviewer, data["approved_at"], notes, json.dumps(data), case_id, version))
        if not result.rowcount:
            return False
        conn.execute("INSERT INTO case_versions VALUES (?,?,?)", (case_id, version, json.dumps(data)))
        audit(reviewer, "case_approved", case_id, {"version": version, "rubric": rubric, "notes": notes}, conn)
    _register_case_from_dict(data)
    return True

def reject_case(case_id, reviewer, version, notes):
    with _connect() as conn:
        result = conn.execute("UPDATE cases SET status='rejected',reviewer=?,review_notes=? WHERE case_id=? AND version=? AND status='pending'", (reviewer, notes, case_id, version))
        if result.rowcount:
            audit(reviewer, "case_rejected", case_id, {"version": version, "notes": notes}, conn)
    CASES.pop(case_id, None)
    return bool(result.rowcount)

def register_case_from_db(case_id):
    data = case_record(case_id)
    if not data or data["status"] == "rejected":
        return False
    _register_case_from_dict(data)
    return True

def list_db_cases(source=None, status=None):
    with _connect() as conn:
        rows = conn.execute("SELECT case_id,title,specialty,difficulty,source,source_ref,status,version,reviewer,approved_at FROM cases").fetchall()
    return [dict(row) for row in rows if (not source or row["source"] == source) and (not status or row["status"] == status)]

def save_session(session_data, actor=None):
    session_id = session_data["session_id"]
    with _connect() as conn:
        conn.execute("""INSERT INTO sessions (session_id,resident_name,case_id,session_data,score_data,debrief_data,status,started_at,completed_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET
            session_data=excluded.session_data,score_data=excluded.score_data,debrief_data=excluded.debrief_data,
            status=excluded.status,completed_at=excluded.completed_at,updated_at=excluded.updated_at""",
            (session_id, session_data["resident_name"], session_data["case_id"], json.dumps(session_data),
             json.dumps(session_data.get("score")), json.dumps(session_data.get("debrief")), session_data["status"],
             session_data["started_at"], session_data.get("completed_at") or None, now()))
        if session_data.get("owner_id"):
            conn.execute("INSERT INTO session_owners VALUES (?,?) ON CONFLICT(session_id) DO NOTHING", (session_id, session_data["owner_id"]))
        if actor:
            audit(actor, "assessment_submitted", session_id, {
                key: session_data.get(key) for key in ("diagnosis_submitted", "management_submitted", "score", "debrief", "case_snapshot")
            }, conn)

def load_session(session_id):
    with _connect() as conn:
        row = conn.execute("SELECT session_data FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return decode(row["session_data"]) if row else None

def get_session_status(session_id):
    return load_session(session_id)

def load_bandit_state():
    with _connect() as conn:
        rows = conn.execute("SELECT arm_id,alpha,beta FROM bandit_state").fetchall()
    return {r["arm_id"]: BanditState(**dict(r)) for r in rows}

def update_bandit_state(arm_id, success):
    with _connect() as conn:
        conn.execute("INSERT INTO bandit_state VALUES (?,1,1) ON CONFLICT(arm_id) DO NOTHING", (arm_id,))
        field = "alpha" if success else "beta"
        conn.execute(f"UPDATE bandit_state SET {field}={field}+1 WHERE arm_id=?", (arm_id,))

def get_recommended_cases(limit=12):
    # Return presentation metadata only. Answer keys belong to the review API.
    records = [r for r in list_db_cases() if r["status"] != "rejected" and not (r["source"] == "dicom" and r["status"] != "approved")]
    groups = {}
    for record in records:
        arm = f"{record['specialty']}_{record['difficulty']}".lower().replace(" ", "_")
        groups.setdefault(arm, []).append(record)
    ranked = ThompsonSamplingBandit(states=load_bandit_state()).sample_arms(list(groups), k=len(groups))
    ranked_records = []
    while any(groups.values()) and len(ranked_records) < limit:
        for arm in ranked:
            if groups[arm] and len(ranked_records) < limit:
                ranked_records.append(groups[arm].pop(0))
    result = []
    for record in ranked_records:
        data = case_record(record["case_id"])
        record["learning_objectives"] = data.get("learning_objectives", [])
        record["_arm_id"] = f"{record['specialty']}_{record['difficulty']}".lower().replace(" ", "_")
        result.append(record)
    return result

def _register_case_from_dict(data):
    _register(ClinicalCase(**{k: v for k, v in data.items() if k in ClinicalCase.__dataclass_fields__}))

def init_db():
    _ensure_db()
    with _connect() as conn:
        for case in list(CASES.values()):
            conn.execute("""INSERT INTO cases (case_id,title,specialty,difficulty,source,source_ref,case_data,status)
                VALUES (?,?,?,?,?,?,?,'pending') ON CONFLICT(case_id) DO NOTHING""",
                (case.case_id, case.title, case.specialty, case.difficulty, case.source or "static", case.source_ref, json.dumps(asdict(case))))
    for record in list_db_cases():
        register_case_from_db(record["case_id"])
