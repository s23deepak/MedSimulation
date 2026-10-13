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
        assert conn.execute('SELECT COUNT(*) AS total FROM schema_migrations').fetchone()['total'] == 5
        assert 'portrait_assets' in {
            row['name'] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def test_existing_portrait_is_quarantined_on_upgrade(tmp_path, monkeypatch):
    path = tmp_path / 'portrait-v3.db'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY);
            INSERT INTO schema_migrations VALUES (1),(2),(3);
            CREATE TABLE cases (case_id TEXT PRIMARY KEY, version INTEGER);
            INSERT INTO cases VALUES ('SIM-001',1);
            CREATE TABLE portrait_assets (
                asset_id TEXT PRIMARY KEY, case_id TEXT, case_version INTEGER,
                file_path TEXT, seed BIGINT, model TEXT, workflow_version INTEGER,
                active INTEGER, created_at TEXT
            );
            INSERT INTO portrait_assets VALUES
                ('old-asset','SIM-001',1,'old.webp',42,'model',1,1,'2026-01-01');
        ''')
    monkeypatch.setattr(database, '_DB_PATH', str(path))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    database._ensure_db()
    assert database.active_portrait('SIM-001', 1) is None
    asset = database.portrait_asset('old-asset')
    assert asset['active'] == 0 and asset['review_status'] == 'quarantined'


def test_current_unreviewed_portrait_is_published_on_upgrade(tmp_path, monkeypatch):
    path = tmp_path / 'portrait-v4.db'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY);
            INSERT INTO schema_migrations VALUES (1),(2),(3),(4);
            CREATE TABLE cases (case_id TEXT PRIMARY KEY, version INTEGER);
            INSERT INTO cases VALUES ('SIM-001',1);
            CREATE TABLE portrait_assets (
                asset_id TEXT PRIMARY KEY, case_id TEXT, case_version INTEGER,
                file_path TEXT, seed BIGINT, model TEXT, workflow_version INTEGER,
                active INTEGER, created_at TEXT, review_status TEXT, reviewed_by TEXT,
                reviewed_at TEXT, review_notes TEXT, prompt_version INTEGER,
                prompt_hash TEXT, target_age INTEGER, appearance_profile TEXT
            );
            CREATE UNIQUE INDEX portrait_active_case_version
                ON portrait_assets(case_id, case_version) WHERE active=1;
            INSERT INTO portrait_assets VALUES
                ('old','SIM-001',1,'old.webp',1,'model',1,0,'2026-01-01','quarantined',NULL,NULL,NULL,1,NULL,NULL,NULL),
                ('new','SIM-001',1,'new.webp',2,'model',1,0,'2026-02-01','unreviewed',NULL,NULL,NULL,2,NULL,5,NULL);
        ''')
    monkeypatch.setattr(database, '_DB_PATH', str(path))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    database._ensure_db()
    assert database.active_portrait('SIM-001', 1)['asset_id'] == 'new'
    assert database.portrait_asset('old')['active'] == 0
