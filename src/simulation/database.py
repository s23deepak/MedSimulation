"""
Database layer for persisting cases and sessions.

Uses SQLite for development / local mode.
Can be swapped to PostgreSQL for production via DATABASE_URL env var.

Tables
------
- cases: all simulation cases (static + dynamic)
- sessions: simulation session history + scores
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .cases import ClinicalCase, CASES, _register
from .bandits import BanditState, ThompsonSamplingBandit

logger = logging.getLogger(__name__)

# ── Database path ─────────────────────────────────────────────────────────────

_DB_PATH = os.getenv(
    "DATABASE_PATH",
    str(Path(__file__).resolve().parent.parent.parent / "data" / "medsim.db"),
)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    specialty TEXT DEFAULT '',
    difficulty TEXT DEFAULT 'intermediate',
    source TEXT DEFAULT 'static',
    source_ref TEXT DEFAULT '',
    case_data JSON NOT NULL,
    status TEXT DEFAULT 'approved',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    resident_name TEXT DEFAULT '',
    case_id TEXT,
    score_data JSON,
    debrief_data JSON,
    status TEXT DEFAULT 'active',
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (case_id) REFERENCES cases(case_id)
);

CREATE INDEX IF NOT EXISTS idx_cases_source ON cases(source);
CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_specialty ON cases(specialty);
CREATE INDEX IF NOT EXISTS idx_sessions_case ON sessions(case_id);

CREATE TABLE IF NOT EXISTS bandit_state (
    arm_id TEXT PRIMARY KEY,
    alpha INTEGER DEFAULT 1,
    beta INTEGER DEFAULT 1
);
"""


# ── Connection management ─────────────────────────────────────────────────────

def _ensure_db() -> None:
    """Create database directory and tables if they don't exist."""
    db_path = Path(_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA)
    logger.info("Database initialized at %s", _DB_PATH)


@contextmanager
def _connect():
    """Context manager for SQLite connections."""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Case operations ───────────────────────────────────────────────────────────

def save_case(
    case_data: dict,
    source: str = "ai_generated",
    source_ref: str = "",
    status: str = "approved",
) -> str:
    """
    Save a case to the database.

    Parameters
    ----------
    case_data : dict
        Full ClinicalCase-compatible dictionary
    source : str
        Origin: 'static', 'pubmed', 'wiley', 'endless_medical', 'ai_generated'
    source_ref : str
        Reference identifier (PMID, DOI, disease name)
    status : str
        'approved' (auto for local), 'pending' (needs review for cloud)

    Returns
    -------
    str
        The case_id of the saved case.
    """
    _ensure_db()
    case_id = case_data.get("case_id", "")
    if not case_id:
        raise ValueError("case_data must contain case_id")

    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO cases
                (case_id, title, specialty, difficulty, source, source_ref, case_data, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case_id,
                case_data.get("title", ""),
                case_data.get("specialty", ""),
                case_data.get("difficulty", "intermediate"),
                source,
                source_ref,
                json.dumps(case_data),
                status,
            ),
        )

    logger.info("Saved case %s (source=%s, status=%s)", case_id, source, status)

    # If approved, also register in the in-memory case registry
    if status == "approved":
        _register_case_from_dict(case_data)

    return case_id


def load_dynamic_cases(status: str = "approved") -> list[dict]:
    """Load all cases from the database with the given status."""
    _ensure_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT case_data, source, source_ref, status FROM cases WHERE status = ?",
            (status,),
        ).fetchall()

    cases = []
    for row in rows:
        data = json.loads(row["case_data"])
        data["_source"] = row["source"]
        data["_source_ref"] = row["source_ref"]
        data["_status"] = row["status"]
        cases.append(data)

    return cases


def get_pending_cases() -> list[dict]:
    """Get cases pending admin review."""
    return load_dynamic_cases(status="pending")


def approve_case(case_id: str) -> bool:
    """Approve a pending case for use in simulations."""
    _ensure_db()
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE cases SET status = 'approved' WHERE case_id = ? AND status = 'pending'",
            (case_id,),
        )
        if cursor.rowcount == 0:
            return False

        # Load into memory
        row = conn.execute(
            "SELECT case_data FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        if row:
            _register_case_from_dict(json.loads(row["case_data"]))

    logger.info("Approved case %s", case_id)
    return True


def reject_case(case_id: str) -> bool:
    """Reject a pending case."""
    _ensure_db()
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE cases SET status = 'rejected' WHERE case_id = ? AND status = 'pending'",
            (case_id,),
        )
    return cursor.rowcount > 0


def list_db_cases(
    source: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """List cases with optional filters."""
    _ensure_db()
    query = "SELECT case_id, title, specialty, difficulty, source, source_ref, status, created_at FROM cases WHERE 1=1"
    params: list = []

    if source:
        query += " AND source = ?"
        params.append(source)
    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY created_at DESC"

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(row) for row in rows]


# ── Session persistence ──────────────────────────────────────────────────────

def save_session(session_data: dict) -> None:
    """Save or update a simulation session in the database."""
    _ensure_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO sessions
                (session_id, resident_name, case_id, score_data, debrief_data,
                 status, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_data.get("session_id", ""),
                session_data.get("resident_name", ""),
                session_data.get("case_id", ""),
                json.dumps(session_data.get("score")) if session_data.get("score") else None,
                json.dumps(session_data.get("debrief")) if session_data.get("debrief") else None,
                session_data.get("status", "active"),
                session_data.get("started_at", ""),
                session_data.get("completed_at", ""),
            ),
        )

# ── Bandit persistence ────────────────────────────────────────────────────────

def load_bandit_state() -> dict[str, BanditState]:
    """Load the current Thompson Sampling alpha/beta counts for all arms."""
    _ensure_db()
    states = {}
    with _connect() as conn:
        rows = conn.execute("SELECT arm_id, alpha, beta FROM bandit_state").fetchall()
        for row in rows:
            states[row["arm_id"]] = BanditState(
                arm_id=row["arm_id"], alpha=row["alpha"], beta=row["beta"]
            )
    return states

def update_bandit_state(arm_id: str, success: bool) -> None:
    """Update an arm's alpha (success) or beta (failure) count."""
    _ensure_db()
    with _connect() as conn:
        # First ensure the row exists
        conn.execute(
            "INSERT OR IGNORE INTO bandit_state (arm_id, alpha, beta) VALUES (?, 1, 1)",
            (arm_id,)
        )
        if success:
            conn.execute("UPDATE bandit_state SET alpha = alpha + 1 WHERE arm_id = ?", (arm_id,))
        else:
            conn.execute("UPDATE bandit_state SET beta = beta + 1 WHERE arm_id = ?", (arm_id,))

def get_recommended_cases(limit: int = 6) -> list[dict]:
    """
    Use the ThompsonSamplingBandit to rank cases based on historical
    engagement clicks, balancing exploration and exploitation.
    """
    bandit_state_dict = load_bandit_state()
    bandit = ThompsonSamplingBandit(states=bandit_state_dict)
    
    # 1. Fetch all available cases
    all_cases = []
    _ensure_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT case_id, title, specialty, difficulty, source, case_data FROM cases WHERE status = 'approved'"
        ).fetchall()
        
    for row in rows:
        data = json.loads(row["case_data"])
        data["_arm_id"] = f"{row['specialty']}_{row['difficulty']}".lower().replace(" ", "_").strip()
        data["_source"] = row["source"]
        all_cases.append(data)
        
    # Group cases by arm_id
    cases_by_arm: dict[str, list[dict]] = {}
    for case in all_cases:
        arm = case.get("_arm_id", "unknown_intermediate")
        cases_by_arm.setdefault(arm, []).append(case)
        
    available_arms = list(cases_by_arm.keys())
    
    # 2. Sample from the bandit to get the ranked arms
    ranked_arms = bandit.sample_arms(available_arms, k=len(available_arms))
    
    # 3. Build the final recommended list
    recommended = []
    # Interleave cases from the top sampled arms to provide variety
    # but strictly preferring the top bandit choices.
    while len(recommended) < limit and any(cases_by_arm.values()):
        for arm in ranked_arms:
            if cases_by_arm[arm] and len(recommended) < limit:
                # Pop a random case from this arm so we don't always show the exact same case
                import random
                idx = random.randrange(len(cases_by_arm[arm]))
                recommended.append(cases_by_arm[arm].pop(idx))
                
    return recommended

# ── Helpers ───────────────────────────────────────────────────────────────────

def _register_case_from_dict(data: dict) -> None:
    """Register a case dict into the in-memory CASES registry."""
    try:
        # Remove internal metadata keys
        clean = {k: v for k, v in data.items() if not k.startswith("_")}
        case = ClinicalCase(**clean)
        _register(case)
        logger.debug("Registered dynamic case %s in memory", case.case_id)
    except Exception as e:
        logger.warning("Could not register case %s from DB: %s", data.get("case_id"), e)


def init_db() -> None:
    """Initialize the database (called during app startup)."""
    _ensure_db()

    # Load all approved dynamic cases into memory
    approved = load_dynamic_cases(status="approved")
    for case_data in approved:
        _register_case_from_dict(case_data)

    if approved:
        logger.info("Loaded %d dynamic cases from database", len(approved))
