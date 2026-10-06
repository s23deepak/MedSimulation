import json
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from src.simulation.exporter import build_export_dict, generate_pdf
from ..security import current_user, own_session

router = APIRouter(tags=["exports"], dependencies=[Depends(current_user)])


@router.get("/api/simulation/session/{session_id}/export/{format}")
def export(session_id: str, format: str, request: Request):
    data = own_session(request, session_id)
    if data["status"] != "scored":
        raise HTTPException(400, "Complete practice before exporting")
    if format not in {"json", "pdf"}:
        raise HTTPException(404, "Unknown export format")
    session = request.app.state.simulation_engine.get_session(session_id)
    result = build_export_dict(session)
    content = json.dumps(result, indent=2) if format == "json" else generate_pdf(result)
    return Response(
        content,
        media_type="application/json" if format == "json" else "application/pdf",
        headers={"Content-Disposition": f'attachment; filename="session_{session_id}.{format}"'},
    )
