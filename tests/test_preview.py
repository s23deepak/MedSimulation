import sqlite3

from scripts.dev_preview import seed_local_cases


def test_preview_copies_cases_without_changing_source(tmp_path):
    source = tmp_path / "existing.db"
    target = tmp_path / "preview.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE cases (case_id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO cases VALUES ('EXISTING-1', 'approved')")

    assert seed_local_cases(source, target) == 1
    with sqlite3.connect(target) as conn:
        conn.execute("UPDATE cases SET status='pending' WHERE case_id='EXISTING-1'")
        assert conn.execute("SELECT status FROM cases").fetchone()[0] == "pending"
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT status FROM cases").fetchone()[0] == "approved"


def test_preview_without_existing_database(tmp_path):
    assert seed_local_cases(tmp_path / "missing.db", tmp_path / "preview.db") == 0
