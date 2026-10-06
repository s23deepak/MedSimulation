import os
import uuid
import pytest
from dataclasses import asdict
from fastapi.testclient import TestClient
from src.simulation import database
from src.simulation.cases import CASES
from src.simulation.storage import engine_for
from src.simulation.rubrics import draft_rubric
from src.web.application import create_app
from src.web.config import Settings

@pytest.mark.skipif(not os.getenv('TEST_POSTGRES_URL'), reason='Postgres CI service not configured')
def test_postgres_migration_review_session_audit(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', os.environ['TEST_POSTGRES_URL'])
    monkeypatch.setattr(database, '_DB_PATH', f'test-{uuid.uuid4()}')
    engine_for.cache_clear()
    database.init_db()
    data = asdict(CASES['SIM-001'])
    data['case_id'] = 'PG-' + uuid.uuid4().hex
    database.save_case(data, source='test')
    assert database.case_record(data['case_id'])['status'] == 'pending'
    assert database.approve_case(data['case_id'], 'reviewer', 1, 'Reviewed in a database integration test', draft_rubric(CASES['SIM-001']))
    assert database.case_record(data['case_id'])['reviewer'] == 'reviewer'
    sid = 'PG-' + uuid.uuid4().hex
    database.save_session({'session_id': sid, 'resident_name':'Test', 'case_id':data['case_id'],
                           'owner_id':'learner', 'status':'active', 'started_at':database.now()})
    assert database.load_session(sid)['owner_id'] == 'learner'
    with database._connect() as conn:
        events = conn.execute('SELECT event_type FROM audit_events WHERE subject_id=?', (data['case_id'],)).fetchall()
    assert {e['event_type'] for e in events} == {'case_saved', 'case_approved'}
    settings = Settings(environment='demo', allowed_origins=['https://pilot.example'])
    app = create_app(settings=settings, initialize_agent=False)
    with TestClient(app, base_url='https://pilot.example') as first, \
         TestClient(app, base_url='https://pilot.example') as second:
        session = first.post('/api/simulation/start', json={'case_id': data['case_id']})
        assert session.status_code == 200
        session_id = session.json()['session_id']
        assert second.get(f'/api/simulation/session/{session_id}').status_code == 200
