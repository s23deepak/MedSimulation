"""Session application service with cross-worker session serialization."""

import hashlib
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from fastapi import HTTPException
from filelock import FileLock, Timeout
from src.simulation import database
from src.simulation.storage import engine_for
from .security import own_session

_locks = [threading.Lock() for _ in range(64)]


@contextmanager
def _session_lock(engine, key):
    if engine.dialect.name == "sqlite":
        path = engine.url.database
        if not path or path == ":memory:":
            yield  # The enclosing thread lock covers an in-memory database.
            return
        db_file = Path(path).resolve()
        directory = Path(os.getenv("MEDSIM_SESSION_LOCK_DIR", str(db_file) + ".locks"))
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # A bounded set of OS locks. The kernel releases the lock if a worker exits.
        lock = FileLock(directory / f"{key % len(_locks)}.lock")
        try:
            lock.acquire(timeout=30)
        except Timeout as exc:
            raise HTTPException(
                503, "Session is busy. Please retry.", headers={"Retry-After": "1"}
            ) from exc
        try:
            yield
        finally:
            lock.release()
    else:
        with database._connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(?)", (key,))
            yield


@contextmanager
def session_action(request, session_id):
    key = int.from_bytes(hashlib.sha256(session_id.encode()).digest()[:8], "big", signed=True)
    engine = engine_for(database._DB_PATH)
    with _locks[key % len(_locks)]:
        with _session_lock(engine, key):
            own_session(request, session_id)
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
