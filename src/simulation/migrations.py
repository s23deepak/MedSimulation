"""Versioned, transactional migrations. Run: python -m src.simulation.migrations."""

from sqlalchemy import inspect, text


def migrate(engine, schema):
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(721040)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"))
        applied = set(conn.execute(text("SELECT version FROM schema_migrations")).scalars())
        if 1 not in applied:
            for statement in schema.split(";"):
                if statement.strip():
                    conn.execute(text(statement))
            columns = {c["name"] for c in inspect(conn).get_columns("sessions")}
            for name, kind in [("session_data", "TEXT"), ("updated_at", "TIMESTAMP")]:
                if name not in columns:
                    conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {name} {kind}"))
            conn.execute(text("INSERT INTO schema_migrations VALUES (1)"))
        if 2 not in applied:
            for name, kind in [("version", "INTEGER NOT NULL DEFAULT 1"), ("reviewer", "TEXT"),
                               ("approved_at", "TEXT"), ("review_notes", "TEXT")]:
                conn.execute(text(f"ALTER TABLE cases ADD COLUMN {name} {kind}"))
            # Historical auto-approvals are not evidence of a human review.
            conn.execute(text("UPDATE cases SET status = 'pending'"))
            for sql in [
                "CREATE TABLE session_owners (session_id TEXT PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE, user_id TEXT NOT NULL)",
                "CREATE TABLE rate_limits (bucket TEXT PRIMARY KEY, hits INTEGER NOT NULL, expires_at BIGINT NOT NULL)",
                "CREATE TABLE audit_events (event_id TEXT PRIMARY KEY, actor TEXT NOT NULL, event_type TEXT NOT NULL, subject_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
                "CREATE INDEX idx_audit_subject ON audit_events(subject_id)",
                "CREATE TABLE case_versions (case_id TEXT NOT NULL, version INTEGER NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(case_id, version))",
            ]:
                conn.execute(text(sql))
            conn.execute(text("INSERT INTO schema_migrations VALUES (2)"))
        if 3 not in applied:
            conn.execute(text("""CREATE TABLE portrait_assets (
                asset_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                case_version INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                seed BIGINT NOT NULL,
                model TEXT NOT NULL,
                workflow_version INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )"""))
            conn.execute(text("""CREATE UNIQUE INDEX portrait_active_case_version
                ON portrait_assets(case_id, case_version) WHERE active=1"""))
            conn.execute(text("INSERT INTO schema_migrations VALUES (3)"))


if __name__ == "__main__":
    from .database import _ensure_db
    _ensure_db()
