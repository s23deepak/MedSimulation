"""Isolated app fixtures. No test touches the developer's case database."""
import copy
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.simulation import database
from src.simulation.cases import CASES
from src.web.application import create_app
from src.web.config import Settings

class FakeAgent:
    def chat(self, prompt):
        return "The discomfort started this morning. It has been persistent since then."

@pytest.fixture
def webapp(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(database, "_DB_PATH", str(tmp_path / "test.db"))
    original = copy.deepcopy(CASES)
    settings = Settings(environment="local")
    app = create_app(settings=settings, agent=FakeAgent(), initialize_agent=False)
    with TestClient(app) as client:
        yield app, client
    CASES.clear()
    CASES.update(original)

def login(client, username="learner"):
    return {"user_id": "pilot-learner", "role": "learner", "guest": True}

def complete(client, case_id="SIM-001"):
    response = client.post("/api/simulation/start", json={"case_id": case_id})
    assert response.status_code == 200, response.text
    session = response.json()
    for question in ["Tell me about your pain", "When did it start?", "Any medications?"]:
        response = client.post("/api/simulation/history", json={"session_id": session["session_id"], "question": question})
        assert response.status_code == 200, response.text
    for route, key, value in [("exam", "system", "Cardiovascular"), ("investigate", "investigation", "ECG")]:
        assert client.post(f"/api/simulation/{route}", json={"session_id": session["session_id"], key: value}).status_code == 200
    response = client.post("/api/simulation/submit", json={"session_id": session["session_id"], "diagnosis": "STEMI", "management": ["Activate cath lab"]})
    assert response.status_code == 200, response.text
    return session, response.json()
