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
        if 4 not in applied:
            for name, kind in [
                ("review_status", "TEXT NOT NULL DEFAULT 'unreviewed'"),
                ("reviewed_by", "TEXT"),
                ("reviewed_at", "TEXT"),
                ("review_notes", "TEXT"),
                ("prompt_version", "INTEGER NOT NULL DEFAULT 1"),
                ("prompt_hash", "TEXT"),
                ("target_age", "INTEGER"),
                ("appearance_profile", "TEXT"),
            ]:
                conn.execute(text(f"ALTER TABLE portrait_assets ADD COLUMN {name} {kind}"))
            # Legacy portraits were published without visual review.
            conn.execute(text("""UPDATE portrait_assets SET active=0,
                review_status='quarantined', review_notes='Legacy prompt was not visually reviewed'"""))
            conn.execute(text("""CREATE TABLE model_runs (
                run_id TEXT PRIMARY KEY,
                component TEXT NOT NULL,
                case_id TEXT,
                session_id TEXT,
                asset_id TEXT,
                prompt_name TEXT NOT NULL,
                prompt_version INTEGER NOT NULL,
                prompt_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                error_type TEXT,
                created_at TEXT NOT NULL
            )"""))
            conn.execute(text("CREATE INDEX idx_model_runs_component_time ON model_runs(component, created_at)"))
            conn.execute(text("""CREATE TABLE model_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                run_id TEXT REFERENCES model_runs(run_id),
                asset_id TEXT REFERENCES portrait_assets(asset_id),
                component TEXT NOT NULL,
                metric TEXT NOT NULL,
                value TEXT NOT NULL,
                evaluator TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL
            )"""))
            conn.execute(text("INSERT INTO schema_migrations VALUES (4)"))
        if 5 not in applied:
            # Publish only current-generation candidates; v1 portraits stay quarantined.
            case_versions = conn.execute(text("""SELECT DISTINCT p.case_id, p.case_version
                FROM portrait_assets p JOIN cases c
                ON c.case_id=p.case_id AND c.version=p.case_version
                WHERE p.review_status='unreviewed' AND p.prompt_version>=2""")).fetchall()
            for case_id, case_version in case_versions:
                candidate = conn.execute(text("""SELECT asset_id FROM portrait_assets
                    WHERE case_id=:case_id AND case_version=:case_version
                    AND review_status='unreviewed' AND prompt_version>=2
                    ORDER BY created_at DESC, asset_id DESC LIMIT 1"""),
                    {"case_id": case_id, "case_version": case_version}).scalar_one()
                conn.execute(text("""UPDATE portrait_assets SET active=0
                    WHERE case_id=:case_id AND case_version=:case_version AND active=1"""),
                    {"case_id": case_id, "case_version": case_version})
                conn.execute(text("UPDATE portrait_assets SET active=1 WHERE asset_id=:asset_id"),
                             {"asset_id": candidate})
            conn.execute(text("INSERT INTO schema_migrations VALUES (5)"))


if __name__ == "__main__":
    from .database import _ensure_db
    _ensure_db()
