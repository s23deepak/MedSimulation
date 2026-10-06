"""Session application service with cross-worker PostgreSQL serialization."""

import hashlib
import threading
from contextlib import contextmanager
from fastapi import HTTPException
from src.simulation import database
from src.simulation.storage import engine_for
from .security import own_session

_locks = [threading.Lock() for _ in range(64)]


@contextmanager
def session_action(request, session_id):
    own_session(request, session_id)
    key = int.from_bytes(hashlib.sha256(session_id.encode()).digest()[:8], "big", signed=True)
    engine = engine_for(database._DB_PATH)
    with _locks[key % len(_locks)]:
        with database._connect() as conn:
            if engine.dialect.name == "postgresql":
                conn.execute("SELECT pg_advisory_xact_lock(?)", (key,))
            try:
                yield request.app.state.simulation_engine
            except ValueError as exc:
                raise HTTPException(
                    400, "Action unavailable. Check the session state and submitted fields."
                ) from exc
            except RuntimeError as exc:
                raise HTTPException(
                    503, "Patient response service unavailable. Please retry."
                ) from exc
