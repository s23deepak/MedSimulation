"""Transactional SQLite/Postgres adapter for the existing repository queries."""

import os
from functools import lru_cache

from sqlalchemy import create_engine, event, text


@lru_cache(maxsize=4)
def engine_for(path):
    url = os.getenv("DATABASE_URL") or f"sqlite:///{path}"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def configure(dbapi, _):
            dbapi.execute("PRAGMA foreign_keys=ON")
            dbapi.execute("PRAGMA journal_mode=WAL")
            dbapi.execute("PRAGMA busy_timeout=10000")
    return engine


class Result:
    def __init__(self, result):
        self.result = result
        self.rowcount = result.rowcount

    def fetchone(self):
        return self.result.mappings().fetchone()

    def fetchall(self):
        return self.result.mappings().fetchall()

    def __iter__(self):
        return iter(self.result.mappings())


class Connection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        for index in range(len(params)):
            sql = sql.replace("?", f":p{index}", 1)
        return Result(self.connection.execute(text(sql), {f"p{i}": p for i, p in enumerate(params)}))
