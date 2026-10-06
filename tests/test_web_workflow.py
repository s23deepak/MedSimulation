import io
import json
import zipfile
from dataclasses import asdict
import pytest
from src.simulation import database
from src.simulation.cases import CASES
from src.simulation.rubrics import draft_rubric
from src.web.security import rate_limit
from fastapi import HTTPException
from fastapi.testclient import TestClient
from src.web.application import create_app
from src.web.config import Settings
from conftest import login, complete


def test_pilot_mode_without_password(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr(database, '_DB_PATH', str(tmp_path / 'guest.db'))
    settings = Settings(environment='local')
    with TestClient(create_app(settings=settings, initialize_agent=False)) as client:
        assert client.get('/api/cases/recommended').status_code == 200
        assert client.get('/api/cases/pending').status_code == 403
        assert client.post('/api/simulation/start', json={'case_id': 'SIM-001'},
                           headers={'Origin': 'https://evil.test'}).status_code == 403
        started = client.post('/api/simulation/start', json={'case_id': 'SIM-001'})
        assert started.status_code == 200
        assert client.get(f"/api/simulation/session/{started.json()['session_id']}").status_code == 200


def test_no_builtin_auth_routes(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr(database, '_DB_PATH', str(tmp_path / 'pilot.db'))
    settings = Settings(environment='local')
    app = create_app(settings=settings, initialize_agent=False)
    with TestClient(app) as alice:
        assert 'id="loginDialog"' not in alice.get('/simulation').text
        assert alice.get('/api/cases/recommended').status_code == 200
        assert alice.get('/api/cases/pending').status_code == 403
        assert alice.get('/review').status_code == 404
        assert alice.get('/api/auth/me').status_code == 404
        assert alice.post('/api/auth/login', json={'username':'x','password':'x'}).status_code == 404
        assert alice.post('/api/auth/logout').status_code == 404
        assert alice.post('/api/simulation/start', json={'case_id':'SIM-001'},
                          headers={'Origin':'https://evil.test'}).status_code == 403
        started = alice.post('/api/simulation/start', json={'case_id':'SIM-001'})
        assert started.status_code == 200
        sid = started.json()['session_id']
        assert alice.get(f'/api/simulation/session/{sid}').status_code == 200


def test_hosted_pilot_configuration(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://example.invalid/medsim')
    Settings(environment='demo', allowed_origins=['https://pilot.example']).validate()
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ALLOW_HOSTED_SQLITE', '1')
    Settings(environment='demo', allowed_origins=['https://pilot.example']).validate()
    monkeypatch.delenv('ALLOW_HOSTED_SQLITE', raising=False)
    monkeypatch.setenv('ALLOW_HOSTED_SQLITE', '1')
    with pytest.raises(ValueError, match='HTTPS'):
        Settings(environment='demo', allowed_origins=['http://pilot.example']).validate()

def test_auth_and_unscored_practice_export(webapp):
    app, client = webapp
    login(client)
    cases = client.get('/api/cases/recommended').json()
    assert cases and all(c['status'] == 'pending' for c in cases)
    assert all('correct_diagnosis' not in c for c in cases)
    session, result = complete(client)
    assert 'case_snapshot' not in session and 'owner_id' not in session
    assert result['scores'] is None
    base = f"/api/simulation/session/{session['session_id']}"
    exported = client.get(base + '/export/json').json()
    assert exported['scores']['total'] is None
    assert exported['case']['reviewer'] is None
    assert 'not an official' in exported['disclaimer'].lower()
    pdf = client.get(base + '/export/pdf')
    assert pdf.status_code == 200, pdf.text[:200] if pdf.status_code != 200 else ''
    assert pdf.content.startswith(b'%PDF')
    app.state.simulation_engine._sessions.clear()
    assert client.get(base).json()['history_questions']
    with database._connect() as conn:
        event = conn.execute('SELECT payload FROM audit_events WHERE subject_id=?', (session['session_id'],)).fetchone()
    assert json.loads(event['payload'])['diagnosis_submitted'] == 'STEMI'

def test_ownership_every_session_surface_and_reset(webapp):
    _, client = webapp
    login(client)
    session, _ = complete(client)
    sid = session['session_id']
    assert client.delete(f'/api/simulation/session/{sid}').status_code == 200
    assert database.load_session(sid) is None


def test_resume_notes_results_and_delete(webapp):
    app, client = webapp
    login(client)
    started = client.post('/api/simulation/start', json={'case_id':'SIM-001'}).json()
    sid = started['session_id']
    assert any(row['session_id'] == sid for row in client.get('/api/simulation/sessions').json())
    assert client.post('/api/simulation/notes', json={'session_id':sid, 'clinical_notes':'Synthetic draft note'}).status_code == 200
    assert client.post('/api/simulation/investigate', json={'session_id':sid, 'investigation':'ECG'}).status_code == 200
    app.state.simulation_engine._sessions.clear()
    restored = client.get(f'/api/simulation/session/{sid}').json()
    assert restored['clinical_notes'] == 'Synthetic draft note'
    assert 'ECG' in restored['ordered_results'][0]['investigation']
    assert client.delete(f'/api/simulation/session/{sid}').status_code == 200
    assert client.get('/api/simulation/sessions').json() == []

def test_review_routes_disabled_until_third_party_auth(webapp):
    _, client = webapp
    login(client)
    assert client.get('/api/cases/pending').status_code == 403
    assert client.get('/api/cases/SIM-001').status_code == 403

def test_validation_rate_limit_csrf_and_error_sanitizing(webapp):
    app, client = webapp
    login(client)
    assert client.post('/api/simulation/start', json={'case_id':'SIM-001', 'owner_id':'other'}).status_code == 422
    assert client.post('/api/simulation/start', json={'case_id':'SIM-001'}, headers={'Origin':'https://evil.test'}).status_code == 403
    for _ in range(2): rate_limit('unit-test', 2)
    with pytest.raises(HTTPException) as error: rate_limit('unit-test', 2)
    assert error.value.status_code == 429
    def broken(*args, **kwargs): raise RuntimeError('PRIVATE DATABASE PASSWORD')
    app.state.simulation_engine.start_session = broken
    response = client.post('/api/simulation/start', json={'case_id':'SIM-001'})
    assert response.status_code == 500
    assert 'PRIVATE' not in response.text

def test_dicom_validation(webapp):
    _, client = webapp
    login(client)
    assert client.post('/api/cases/import/dicom', files={'file':('test.zip', b'bad', 'application/zip')}).status_code == 403
    assert client.get('/api/simulation/imaging/dicom_list?case_id=../../').status_code == 422

def test_generation_pending_and_content_checks(webapp, monkeypatch):
    _, client = webapp
    from src.simulation.case_sources import ai_generator
    async def generated(*args, **kwargs):
        data = asdict(CASES['SIM-001'])
        data.update(case_id='GENERATED-1', status='approved', reviewer='fabricated')
        return data
    monkeypatch.setattr(ai_generator, 'generate_case', generated)
    login(client)
    response = client.post('/api/cases/generate', json={'topic':'fictional scenario'})
    assert response.status_code == 200, response.text
    record = database.case_record('GENERATED-1')
    assert record['status'] == 'pending' and record['reviewer'] is None
    data = asdict(CASES['SIM-001'])
    data['presentation'] = '<script>alert(1)</script>'
    with pytest.raises(ValueError): database.save_case(data)
