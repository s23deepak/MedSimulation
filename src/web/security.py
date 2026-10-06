"""Temporary pilot identity and shared database rate limits."""

import time
from fastapi import HTTPException, Request
from src.simulation.database import _connect, load_session

PILOT_USER = {"user_id": "pilot-learner", "role": "learner", "guest": True}


def rate_limit(key, limit, seconds=60):
    current = int(time.time())
    bucket = f"{key}:{current // seconds}"
    with _connect() as conn:
        conn.execute("DELETE FROM rate_limits WHERE expires_at < ?", (current,))
        row = conn.execute(
            """INSERT INTO rate_limits VALUES (?,1,?) ON CONFLICT(bucket)
            DO UPDATE SET hits=rate_limits.hits+1 RETURNING hits""",
            (bucket, current + seconds),
        ).fetchone()
    if row["hits"] > limit:
        raise HTTPException(
            429, "Too many requests. Try again shortly.", headers={"Retry-After": str(seconds)}
        )


def rate_limit_actor(request: Request, action: str, limit: int, seconds: int = 60):
    rate_limit(f"{action}:{request.state.user['user_id']}", limit, seconds)
    host = request.client.host if request.client else "unknown"
    rate_limit(f"{action}:ip:{host}", limit * 3, seconds)


def current_user(request: Request):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        allowed = request.app.state.settings.allowed_origins
        same_origin = f"{request.url.scheme}://{request.url.netloc}"
        if request.headers.get("sec-fetch-site") == "cross-site" or (
            origin and origin not in allowed and origin != same_origin
        ):
            raise HTTPException(403, "Request origin not allowed")
    user = dict(PILOT_USER)
    request.state.user = user
    return user


def reviewer(request: Request):
    current_user(request)
    raise HTTPException(403, "Reviewer access is disabled until third-party auth is configured")


def own_session(request, session_id):
    user = current_user(request)
    data = load_session(session_id)
    if not data or data.get("owner_id") != user["user_id"]:
        raise HTTPException(404, "Session not found")
    return data
