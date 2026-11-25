import multiprocessing
from types import SimpleNamespace

from starlette.requests import Request
from src.simulation import database
from src.simulation.simulator import SimulationEngine
from src.web.config import Settings
from src.web.services import session_action


def _write_in_worker(db_path, cookie, session_id, ready, release, notes):
    database._DB_PATH = db_path
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=Settings(environment="local"), simulation_engine=SimulationEngine()
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "scheme": "http",
            "app": app,
            "headers": [(b"cookie", f"medsim_learner={cookie}".encode())],
        }
    )
    if not notes:
        ready.set()
    with session_action(request, session_id) as engine:
        if notes:
            session = engine._get_active_session(session_id)
            ready.set()
            assert release.wait(timeout=15)
            session.clinical_notes = "Persistent note"
            engine._persist_session(session)
        else:
            engine.order_investigation(session_id, "ECG")


def test_simultaneous_actions_persist_across_processes(webapp):
    _, client = webapp
    sid = client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()["session_id"]
    ctx = multiprocessing.get_context("spawn")
    ready, attempting, release = (ctx.Event() for _ in range(3))
    args = (database._DB_PATH, client.cookies["medsim_learner"], sid)
    first = ctx.Process(target=_write_in_worker, args=(*args, ready, release, True))
    second = ctx.Process(target=_write_in_worker, args=(*args, attempting, release, False))
    first.start()
    try:
        assert ready.wait(timeout=15)
        second.start()
        assert attempting.wait(timeout=15)
        second.join(timeout=0.3)
        assert second.is_alive()
    finally:
        release.set()
        first.join(15)
        if second.pid:
            second.join(15)
    assert first.exitcode == second.exitcode == 0
    state = database.load_session(sid)
    assert state["clinical_notes"] == "Persistent note"
    assert state["investigations_ordered"] == ["12-lead ECG"]
