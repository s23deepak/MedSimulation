from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from src.simulation import database
from src.simulation.portraits import portrait_dir
from src.simulation.chat_chain import clear_session_history
from ..models import (
    Start,
    SessionView,
    History,
    HistoryResult,
    Exam,
    ExamResult,
    Investigation,
    InvestigationResult,
    Imaging,
    ImagingResult,
    Submit,
    AssessmentResult,
    Status,
    CaseSummary,
    PollStatus,
    DebriefView,
    SessionListItem,
    Notes,
)
from ..security import current_user, own_session, rate_limit, rate_limit_actor
from ..services import session_action

router = APIRouter(
    prefix="/api/simulation", tags=["practice"], dependencies=[Depends(current_user)]
)


@router.get("/cases", response_model=list[CaseSummary])
def cases():
    return database.get_recommended_cases(100)


@router.get("/sessions", response_model=list[SessionListItem])
def my_sessions(request: Request):
    with database._connect() as conn:
        rows = conn.execute(
            """SELECT sessions.session_id, sessions.session_data, sessions.status, sessions.updated_at
            FROM sessions JOIN session_owners USING(session_id) WHERE user_id=? ORDER BY sessions.updated_at DESC LIMIT 20""",
            (request.state.user["user_id"],),
        ).fetchall()
    return [
        {
            "session_id": row["session_id"],
            "case_title": database.decode(row["session_data"]).get("case_title", "Case"),
            "status": row["status"],
            "updated_at": str(row["updated_at"]),
        }
        for row in rows
    ]


@router.post("/start", response_model=SessionView)
def start(payload: Start, request: Request):
    user = request.state.user
    rate_limit_actor(request, "start", 20)
    if not database.register_case_from_db(payload.case_id):
        raise HTTPException(404, "Case not found")
    case_record = database.case_record(payload.case_id)
    if (
        case_record["source"] == "dicom"
        and case_record["status"] != "approved"
        and user["role"] != "reviewer"
    ):
        raise HTTPException(404, "Case not found")
    return request.app.state.simulation_engine.start_session(
        payload.resident_name, payload.case_id, user["user_id"]
    ).to_dict()


@router.get("/session/{session_id}", response_model=SessionView)
def session(session_id: str, request: Request):
    own_session(request, session_id)
    return request.app.state.simulation_engine.get_session(session_id).to_dict()


@router.get("/session/{session_id}/portrait", include_in_schema=False)
def session_portrait(session_id: str, request: Request):
    session_data = own_session(request, session_id)
    asset_id = session_data.get("portrait_asset_id")
    asset = database.portrait_asset(asset_id) if asset_id else None
    if (
        not asset
        or asset["case_id"] != session_data["case_id"]
        or asset["case_version"] != session_data["case_version"]
    ):
        raise HTTPException(404, "Portrait not found")
    base = portrait_dir().resolve()
    target = (base / asset["file_path"]).resolve()
    if not target.is_relative_to(base):
        raise HTTPException(404, "Portrait not found")
    if not target.is_file() and request.app.state.reload_portrait_volume:
        try:
            request.app.state.reload_portrait_volume()
        except RuntimeError as exc:
            raise HTTPException(503, "Portrait temporarily unavailable") from exc
    if not target.is_file():
        raise HTTPException(404, "Portrait not found")
    return FileResponse(target, media_type="image/webp")


@router.post("/history", response_model=HistoryResult)
def history(payload: History, request: Request):
    rate_limit_actor(request, "chat", 30)
    with session_action(request, payload.session_id) as engine:
        return engine.ask_history(payload.session_id, payload.question)


@router.post("/exam", response_model=ExamResult)
def exam(payload: Exam, request: Request):
    with session_action(request, payload.session_id) as engine:
        return engine.view_exam(payload.session_id, payload.system)


@router.post("/investigate", response_model=InvestigationResult)
def investigate(payload: Investigation, request: Request):
    with session_action(request, payload.session_id) as engine:
        return engine.order_investigation(payload.session_id, payload.investigation)


@router.post("/imaging", response_model=ImagingResult)
def imaging(payload: Imaging, request: Request):
    with session_action(request, payload.session_id) as engine:
        return engine.view_imaging(payload.session_id, payload.study_id)


@router.post("/submit", response_model=AssessmentResult)
def submit(payload: Submit, request: Request):
    rate_limit_actor(request, "score", 10)
    if any(len(step) > 2000 for step in payload.management):
        raise HTTPException(422, "Management steps must be at most 2000 characters")
    with session_action(request, payload.session_id) as engine:
        return engine.submit_assessment(payload.session_id, payload.diagnosis, payload.management)


@router.post("/notes", response_model=Status)
def notes(payload: Notes, request: Request):
    rate_limit(f"notes:{request.state.user['user_id']}", 120)
    with session_action(request, payload.session_id) as engine:
        session = engine._get_active_session(payload.session_id)
        session.clinical_notes = payload.clinical_notes
        engine._persist_session(session)
    return {"status": "saved"}


@router.get("/session/{session_id}/status", response_model=PollStatus)
def status(session_id: str, request: Request):
    data = own_session(request, session_id)
    return {
        "session_id": session_id,
        "status": data["status"],
        "ai_ready": True,
        "scores": data.get("score"),
        "debrief": data.get("debrief"),
        "ai_feedback": (data.get("score") or {}).get("ai_feedback", ""),
    }


@router.get("/session/{session_id}/debrief", response_model=DebriefView)
def debrief(session_id: str, request: Request):
    data = own_session(request, session_id)
    if data["status"] != "scored":
        raise HTTPException(400, "Complete practice first")
    return {"session_id": session_id, "scores": data["score"], "debrief": data["debrief"]}


@router.delete("/session/{session_id}", response_model=Status)
def reset(session_id: str, request: Request):
    with session_action(request, session_id) as engine:
        with database._connect() as conn:
            conn.execute(
                "DELETE FROM audit_events WHERE subject_id=? AND actor=?",
                (session_id, request.state.user["user_id"]),
            )
            conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
        engine._sessions.pop(session_id, None)
        clear_session_history(session_id)
    return {"status": "deleted"}
