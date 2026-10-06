import logging
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from src.simulation import database
from src.simulation.cases import ClinicalCase
from src.simulation.rubrics import draft_rubric
from .. import case_generation
from ..models import (
    CaseSummary,
    CaseResult,
    CaseEdit,
    Engagement,
    Generate,
    ImportRequest,
    ImportResult,
    Review,
    Reject,
    Status,
    CaseContent,
    AuditView,
)
from ..security import current_user, rate_limit, rate_limit_actor, reviewer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cases", tags=["cases"], dependencies=[Depends(current_user)])


@router.get("/recommended", response_model=list[CaseSummary])
def recommended(limit: int = Query(default=12, ge=1, le=100)):
    return database.get_recommended_cases(limit)


@router.get("/db", response_model=list[CaseSummary])
def listing(source: str | None = None, status: str | None = None):
    return database.list_db_cases(source, status)


@router.get("/pending", response_model=list[CaseContent], dependencies=[Depends(reviewer)])
def pending():
    cases = database.get_pending_cases()
    for case in cases:
        obj = ClinicalCase(
            **{k: v for k, v in case.items() if k in ClinicalCase.__dataclass_fields__}
        )
        case["rubric"] = case.get("rubric") or draft_rubric(obj)
    return cases


@router.post("/generate", response_model=CaseResult)
async def generate(payload: Generate, request: Request):
    rate_limit_actor(request, "generate", 5, 300)
    try:
        return await case_generation.generate(payload, request.app.state.agent)
    except Exception as exc:
        logger.exception("Case generation failed")
        raise HTTPException(503, "Case generation unavailable. Retry shortly.") from exc


@router.post("/import/dicom", response_model=CaseResult, dependencies=[Depends(reviewer)])
async def dicom(request: Request, file: UploadFile = File(...)):
    from src.simulation.case_sources.dicom_import import process_dicom_zip

    if not (file.filename or "").lower().endswith(".zip") or file.content_type not in {
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
    }:
        raise HTTPException(415, "Upload a DICOM ZIP archive")
    rate_limit(f"import:{request.state.user['user_id']}", 5, 300)
    try:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "upload.zip"
            size = 0
            with path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > request.app.state.settings.max_upload_bytes:
                        raise HTTPException(413, "Upload exceeds 20 MiB")
                    output.write(chunk)
            case = process_dicom_zip(path)
            database.save_case(asdict(case), source="dicom")
        return {"case_id": case.case_id, "title": case.title, "status": "pending"}
    except ValueError as exc:
        raise HTTPException(400, "Invalid or unsafe DICOM archive") from exc
    finally:
        await file.close()


@router.post("/import/{source}", response_model=ImportResult, dependencies=[Depends(reviewer)])
async def import_cases(
    source: Literal["pubmed", "wiley", "endless", "agentclinic"],
    payload: ImportRequest,
    request: Request,
):
    rate_limit(f"import:{request.state.user['user_id']}", 5, 300)
    try:
        return await case_generation.import_cases(
            payload, "endless_medical" if source == "endless" else source, request.app.state.agent
        )
    except Exception as exc:
        logger.exception("Import failed")
        raise HTTPException(503, "Import unavailable. Check the source and retry.") from exc


@router.post("/track_engagement", response_model=Status)
def engagement(payload: Engagement, request: Request):
    rate_limit(f"engagement:{request.state.user['user_id']}", 60)
    database.update_bandit_state(payload.arm_id, payload.success)
    return {"status": "success"}


@router.get("/{case_id}", response_model=CaseContent, dependencies=[Depends(reviewer)])
def case(case_id: str):
    result = database.case_record(case_id)
    if not result:
        raise HTTPException(404, "Case not found")
    return result


@router.put("/{case_id}", response_model=CaseResult, dependencies=[Depends(reviewer)])
def edit(case_id: str, payload: CaseEdit, request: Request):
    existing = database.case_record(case_id)
    if not existing or existing["version"] != payload.version:
        raise HTTPException(409, "Case changed. Reload before editing.")
    data = payload.case.model_dump()
    if data.get("case_id") != case_id:
        raise HTTPException(422, "Case identifier cannot change")
    try:
        database.save_case(
            data, existing["source"], existing["source_ref"], expected_version=payload.version
        )
        database.audit(
            request.state.user["user_id"],
            "case_edited",
            case_id,
            {"previous_version": payload.version},
        )
    except ValueError as exc:
        if "Case changed" in str(exc):
            raise HTTPException(409, "Case changed. Reload before editing.") from exc
        raise HTTPException(422, "Invalid case content") from exc
    except TypeError as exc:
        raise HTTPException(422, "Invalid case content") from exc
    return {"case_id": case_id, "title": data["title"], "status": "pending"}


@router.post("/{case_id}/approve", response_model=CaseResult, dependencies=[Depends(reviewer)])
def approve(case_id: str, payload: Review, request: Request):
    if not payload.clinical_review_confirmed:
        raise HTTPException(422, "Clinical review confirmation is required")
    try:
        approved = database.approve_case(
            case_id,
            request.state.user["user_id"],
            payload.version,
            payload.notes,
            payload.rubric.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(
            422, "Complete case content and a valid five-domain rubric are required"
        ) from exc
    if not approved:
        raise HTTPException(409, "Case changed or is no longer pending. Reload the review queue.")
    return {"case_id": case_id, "status": "approved"}


@router.post("/{case_id}/reject", response_model=CaseResult, dependencies=[Depends(reviewer)])
def reject(case_id: str, payload: Reject, request: Request):
    if not database.reject_case(
        case_id, request.state.user["user_id"], payload.version, payload.notes
    ):
        raise HTTPException(409, "Case changed or is no longer pending")
    return {"case_id": case_id, "status": "rejected"}


@router.get("/{case_id}/audit", response_model=list[AuditView], dependencies=[Depends(reviewer)])
def audit(case_id: str):
    with database._connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM audit_events WHERE subject_id=? ORDER BY created_at", (case_id,)
            ).fetchall()
        ]
