import json
import sqlite3
from dataclasses import asdict
from src.simulation import database
from src.simulation.cases import CASES

def test_legacy_auto_approvals_become_pending(tmp_path, monkeypatch):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE cases (case_id TEXT PRIMARY KEY, title TEXT, specialty TEXT, difficulty TEXT,
                source TEXT, source_ref TEXT, case_data TEXT, status TEXT, created_at TIMESTAMP);
            CREATE TABLE sessions (session_id TEXT PRIMARY KEY, resident_name TEXT, case_id TEXT,
                score_data TEXT, debrief_data TEXT, status TEXT, started_at TIMESTAMP, completed_at TIMESTAMP);
        ''')
        data = asdict(CASES['SIM-001'])
        conn.execute('INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)',
                     ('SIM-001', data['title'], data['specialty'], data['difficulty'], 'static', '', json.dumps(data), 'approved'))
    monkeypatch.setattr(database, '_DB_PATH', str(path))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    database.init_db()
    record = database.case_record('SIM-001')
    assert record['status'] == 'pending'
    assert record['reviewer'] is None
    with database._connect() as conn:
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(sessions)').fetchall()}
        assert 'session_data' in columns and 'updated_at' in columns
        assert conn.execute('SELECT COUNT(*) AS total FROM schema_migrations').fetchone()['total'] == 2
