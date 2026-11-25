import sqlite3
import sys
from pathlib import Path

import uvicorn

from scripts.dev_preview import main, seed_local_cases
from src.simulation import database
from src.simulation.simulator import SimulationEngine


def test_launcher_isolates_sessions_from_inherited_database_url(tmp_path, monkeypatch):
    external = tmp_path / "external.db"
    with sqlite3.connect(external) as conn:
        conn.execute("CREATE TABLE sentinel (value TEXT)")
        conn.execute("INSERT INTO sentinel VALUES ('keep')")
    original = external.read_bytes()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{external}")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "missing-case-bank.db"))
    monkeypatch.setattr(database, "_DB_PATH", str(external))
    monkeypatch.setattr(sys, "argv", ["dev_preview.py"])
    preview_files = []

    def exercise_preview(app, **kwargs):
        with database._connect() as conn:
            active_file = Path(conn.execute("PRAGMA database_list").fetchone()["file"])
        assert active_file == Path(database._DB_PATH) and active_file != external
        session = SimulationEngine().start_session("Preview", "SIM-001", "preview-learner")
        with sqlite3.connect(active_file) as conn:
            assert conn.execute(
                "SELECT status FROM sessions WHERE session_id=?", (session.session_id,)
            ).fetchone() == ("active",)
        preview_files.append(active_file)

    monkeypatch.setattr(uvicorn, "run", exercise_preview)
    main()

    assert len(preview_files) == 1 and not preview_files[0].exists()
    assert external.read_bytes() == original


def test_preview_copies_cases_without_changing_source(tmp_path):
    source = tmp_path / "existing.db"
    target = tmp_path / "preview.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE cases (case_id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO cases VALUES ('EXISTING-1', 'approved')")
    with sqlite3.connect(target) as conn:
        conn.execute("CREATE TABLE cases (case_id TEXT PRIMARY KEY, status TEXT)")

    assert seed_local_cases(source, target) == 1
    with sqlite3.connect(target) as conn:
        conn.execute("UPDATE cases SET status='pending' WHERE case_id='EXISTING-1'")
        assert conn.execute("SELECT status FROM cases").fetchone()[0] == "pending"
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT status FROM cases").fetchone()[0] == "approved"


def test_preview_without_existing_database(tmp_path):
    assert seed_local_cases(tmp_path / "missing.db", tmp_path / "preview.db") == 0


def test_preview_copies_portrait_assets(tmp_path):
    source = tmp_path / "existing.db"
    target = tmp_path / "preview.db"
    for path in (source, target):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE cases (case_id TEXT PRIMARY KEY)")
            conn.execute("CREATE TABLE portrait_assets (asset_id TEXT PRIMARY KEY, case_id TEXT)")
    with sqlite3.connect(source) as conn:
        conn.execute("INSERT INTO cases VALUES ('SIM-001')")
        conn.execute("INSERT INTO portrait_assets VALUES ('asset-1', 'SIM-001')")

    assert seed_local_cases(source, target) == 1
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT * FROM portrait_assets").fetchone() == ('asset-1', 'SIM-001')
