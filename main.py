"""
MedSimulation — Clinical Simulation Engine
FastAPI server for AI-powered resident training and competency assessment.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pydantic import BaseModel
from openai import AsyncOpenAI

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.simulation import (
    get_simulation_engine,
    list_cases,
    get_case,
)
from src.simulation.cases import load_cases_from_json
from src.simulation.database import (
    init_db, save_case, get_pending_cases, approve_case, reject_case, 
    list_db_cases, get_recommended_cases, update_bandit_state
)
from src.simulation.case_sources.dicom_import import process_dicom_zip


load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ── Global state ──────────────────────────────────────────────────────────────
simulation_engine = None
vllm_client = None
vllm_ready = False  # Track vLLM readiness
vllm_warming_up = False  # Track if warmup is in progress


# ── Context-aware logging ─────────────────────────────────────────────────────

def log_with_context(message: str, session_id: str = None, case_id: str = None, **extra):
    """Log a message with optional session/case context for traceability."""
    context_parts = []
    if session_id:
        context_parts.append(f"session={session_id}")
    if case_id:
        context_parts.append(f"case={case_id}")
    for k, v in extra.items():
        if v is not None:
            context_parts.append(f"{k}={v}")

    if context_parts:
        logger.info(f"{message} [{', '.join(context_parts)}]")
    else:
        logger.info(message)


# ── Lifespan ──────────────────────────────────────────────────────────────────

from contextlib import asynccontextmanager


async def warmup_vllm():
    """Send a warmup prompt to vLLM to wake it from sleep.

    In Modal mode, vLLM may take 30-60 seconds to start, so we retry
    the warmup until it succeeds.
    """
    global vllm_ready, vllm_warming_up

    if vllm_client is None:
        vllm_ready = True  # Simulated mode is always ready
        return

    max_retries = 30  # Retry for up to 60 seconds (2s intervals)
    retry_count = 0

    while retry_count < max_retries:
        try:
            logger.info("Sending warmup prompt to vLLM (attempt %d/%d)...", retry_count + 1, max_retries)

            # First check health
            healthy = await vllm_client.health_async()
            if not healthy:
                raise Exception("vLLM health check failed")

            # Send a simple warmup prompt to wake the model
            warmup_messages = [
                {"role": "system", "content": "You are a medical AI assistant. Respond briefly."},
                {"role": "user", "content": "Say 'ready' in one word."}
            ]
            response = await vllm_client.chat_async(warmup_messages, max_tokens=10, timeout=60.0)
            logger.info("vLLM warmup response: %s", response.strip()[:50] if response else "empty")
            vllm_ready = True
            vllm_warming_up = False
            logger.info("✓ vLLM is ready after %d attempts", retry_count + 1)
            return

        except Exception as e:
            retry_count += 1
            if retry_count >= max_retries:
                logger.warning("vLLM warmup failed after %d attempts: %s", retry_count, e)
                vllm_warming_up = False
                vllm_ready = True  # Allow requests anyway
                return
            logger.info("vLLM not ready yet (attempt %d/%d): %s", retry_count, max_retries, e)
            await asyncio.sleep(2)  # Wait 2 seconds before retry


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    global simulation_engine, vllm_client, vllm_ready, vllm_warming_up

    # Load any JSON case files
    load_cases_from_json()

    # ── Connect to vLLM inference backend ─────────────────────────────────
    from src.simulation.vllm_client import VLLMClient

    # Support legacy SIMULATED_MODE env var
    legacy_sim = os.getenv("SIMULATED_MODE", "").lower() in ("true", "1", "yes")
    if legacy_sim and not os.getenv("VLLM_MODE"):
        os.environ["VLLM_MODE"] = "simulated"

    vllm_client = VLLMClient.from_env()

    if vllm_client is not None:
        # In Modal mode, vLLM may not be ready yet — start warmup anyway
        # The warmup will retry until vLLM responds
        logger.info("vLLM client initialized: %s", vllm_client)
        logger.info("Starting vLLM warmup in background...")
        vllm_warming_up = True
        asyncio.create_task(warmup_vllm())
    else:
        logger.info("Running in SIMULATED_MODE — keyword-based patient responses")
        vllm_ready = True  # Simulated mode is ready


    # ── Initialize database and load dynamic cases ─────────────────────────
    init_db()

    simulation_engine = get_simulation_engine(agent=vllm_client)
    logger.info(
        "Simulation engine initialized with %d cases (agent=%s)",
        len(list_cases()),
        "vLLM" if vllm_client else "keyword-fallback",
    )

    # ── Generate sample imaging for built-in cases ─────────────────────────
    try:
        from src.simulation.imaging import generate_sample_ecgs
        from src.simulation.cases import CASES
        ecg_samples = generate_sample_ecgs()
        for cid, studies in ecg_samples.items():
            if cid in CASES:
                CASES[cid].imaging_studies = studies
        logger.info("Generated imaging for %d cases", len(ecg_samples))
    except Exception as e:
        logger.warning("Failed to generate sample ECGs: %s", e)

    yield

    # Shutdown
    logger.info("Shutting down MedSimulation")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="MedSimulation — Clinical Simulation Engine",
    description="AI-powered resident training with standardized patient simulations",
    version="0.1.0",
    lifespan=lifespan,
)

# Enable CORS for web app (React Native Web runs on different port)
# In development, allow all origins. In production, restrict to specific domains.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Dev mode - restrict in production
    allow_credentials=False,  # Must be False when using wildcard origins
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_path = Path(__file__).parent / "static"
static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Mount imaging files
imaging_path = Path(__file__).parent / "data" / "imaging"
imaging_path.mkdir(parents=True, exist_ok=True)
app.mount("/imaging", StaticFiles(directory=str(imaging_path)), name="imaging")

# Templates
templates_path = Path(__file__).parent / "templates"
templates_path.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(templates_path))


# ══════════════════════════════════════════════════════════════════════════════
# Page routes
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Redirect to simulation page."""
    return templates.TemplateResponse("simulation.html", {"request": request})


@app.get("/simulation", response_class=HTMLResponse)
async def simulation_page(request: Request):
    """Clinical simulation lab for resident training."""
    return templates.TemplateResponse("simulation.html", {"request": request})


# ══════════════════════════════════════════════════════════════════════════════
# Health & Readiness endpoints
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/health")
async def api_health():
    """Health check endpoint with vLLM readiness status."""
    global vllm_ready, vllm_warming_up
    return {
        "status": "ok",
        "vllm_ready": vllm_ready,
        "vllm_warming_up": vllm_warming_up,
        "vllm_mode": os.getenv("VLLM_MODE", "simulated"),
    }


@app.post("/api/vllm/warmup")
async def api_vllm_warmup():
    """Manually trigger vLLM warmup (for Modal wake-on-first-request)."""
    global vllm_ready, vllm_warming_up

    if vllm_ready:
        return {"status": "already_ready"}

    if vllm_warming_up:
        return {"status": "warming_up"}

    # Start warmup in background
    asyncio.create_task(warmup_vllm())
    return {"status": "warming_up"}


# ══════════════════════════════════════════════════════════════════════════════
# API routes
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/simulation/cases")
async def api_sim_cases():
    """Return the list of available simulation cases."""
    return JSONResponse(content=list_cases())


@app.post("/api/simulation/start")
async def api_sim_start(payload: dict):
    """
    Start a new simulation session.
    Body: { "resident_name": str, "case_id": str }
    Returns full session dict.
    """
    resident_name = payload.get("resident_name", "Resident").strip()
    case_id = payload.get("case_id", "").strip()
    if not case_id:
        raise HTTPException(status_code=400, detail="case_id is required")
    try:
        session = simulation_engine.start_session(resident_name, case_id)
        log_with_context("Session started", session_id=session.session_id, case_id=case_id)
        return JSONResponse(content=session.to_dict())
    except ValueError as e:
        log_with_context("Session start failed", case_id=case_id, error=str(e))
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/simulation/session/{session_id}")
async def api_sim_get_session(session_id: str):
    """Retrieve session metadata and history."""
    session = simulation_engine.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return JSONResponse(content=session.to_dict())


@app.post("/api/simulation/history")
async def api_sim_history(payload: dict):
    """
    Resident asks the simulated patient a history question.
    Body: { "session_id": str, "question": str }
    Returns: { "question": str, "response": str, "ai": bool }
    """
    session_id = payload.get("session_id", "")
    question = payload.get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    try:
        result = simulation_engine.ask_history(session_id, question)
        log_with_context("History question", session_id=session_id, question=question[:50])

        # Phase E: Generate Audio if we have an OpenAI API Key
        import os
        from src.simulation.media import generate_patient_voice
        openai_key = os.getenv("OPENAI_API_KEY")
        if result.get("ai") and openai_key:
            session = simulation_engine.get_session(session_id)
            if session:
                client = AsyncOpenAI(api_key=openai_key)
                text = result.get("response", "")

                # Try to extract sex from presentation
                sex = "U"
                if "female" in session.case.presentation.lower() or "woman" in session.case.presentation.lower():
                    sex = "F"
                elif "male" in session.case.presentation.lower() or "man" in session.case.presentation.lower():
                    sex = "M"

                audio_url = await generate_patient_voice(client, text, sex)
                if audio_url:
                    result["audio_url"] = audio_url

        return JSONResponse(content=result)
    except ValueError as e:
        log_with_context("History question failed", session_id=session_id, error=str(e))
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/simulation/exam")
async def api_sim_exam(payload: dict):
    """
    Resident requests physical examination findings.
    Body: { "session_id": str, "system": str }
    Returns: { "system": str, "findings": str }
    """
    session_id = payload.get("session_id", "")
    system = payload.get("system", "").strip()
    if not system:
        raise HTTPException(status_code=400, detail="system is required")
    try:
        result = simulation_engine.view_exam(session_id, system)
        log_with_context("Exam requested", session_id=session_id, system=system)
        return JSONResponse(content=result)
    except ValueError as e:
        log_with_context("Exam failed", session_id=session_id, system=system, error=str(e))
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/simulation/investigate")
async def api_sim_investigate(payload: dict):
    """
    Resident orders an investigation.
    Body: { "session_id": str, "investigation": str }
    Returns: { "investigation": str, "result": str }
    """
    session_id = payload.get("session_id", "")
    investigation = payload.get("investigation", "").strip()
    if not investigation:
        raise HTTPException(status_code=400, detail="investigation is required")
    try:
        result = simulation_engine.order_investigation(session_id, investigation)
        log_with_context("Investigation ordered", session_id=session_id, investigation=investigation)
        return JSONResponse(content=result)
    except ValueError as e:
        log_with_context("Investigation failed", session_id=session_id, investigation=investigation, error=str(e))
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/simulation/imaging")
async def api_sim_imaging(payload: dict):
    """
    Resident views a medical image/waveform.
    Body: { "session_id": str, "study_id": str }
    Returns: { study_id, modality, description, image_url, findings }
    """
    session_id = payload.get("session_id", "")
    study_id = payload.get("study_id", "").strip()
    if not study_id:
        raise HTTPException(status_code=400, detail="study_id is required")
    try:
        result = simulation_engine.view_imaging(session_id, study_id)
        log_with_context("Imaging viewed", session_id=session_id, study_id=study_id)
        return JSONResponse(content=result)
    except ValueError as e:
        log_with_context("Imaging failed", session_id=session_id, study_id=study_id, error=str(e))
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/simulation/submit")
async def api_sim_submit(payload: dict):
    """
    Resident submits diagnosis and management for scoring.
    Body: { "session_id": str, "diagnosis": str, "management": list[str] }
    Returns scores immediately, AI feedback streams in asynchronously.
    """
    global vllm_ready

    session_id = payload.get("session_id", "")
    diagnosis = payload.get("diagnosis", "").strip()
    management = payload.get("management", [])

    if not diagnosis:
        raise HTTPException(status_code=400, detail="diagnosis is required")

    # Block submission until vLLM is ready (prevents 400 errors during warmup)
    if vllm_client is not None and not vllm_ready:
        log_with_context("Submission blocked - vLLM not ready", session_id=session_id)
        raise HTTPException(
            status_code=503,
            detail="AI model is still warming up. Please wait 30-60 seconds and try again."
        )

    try:
        # Use async scoring - returns immediately with rule-based scores
        result = simulation_engine.submit_assessment(
            session_id, diagnosis, management, wait_for_ai=False
        )
        log_with_context("Assessment submitted (async)", session_id=session_id, diagnosis=diagnosis[:50])
        return JSONResponse(content=result)
    except ValueError as e:
        log_with_context("Assessment failed", session_id=session_id, error=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/simulation/session/{session_id}/status")
async def api_sim_status(session_id: str):
    """
    Polling endpoint for AI feedback status.
    Returns current scoring state - call this repeatedly until ai_ready=True.
    """
    session = simulation_engine.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check if AI feedback is ready
    ai_ready = not getattr(session, "ai_pending", False)
    ai_feedback = session.score.get("ai_feedback", "") if session.score else ""
    debrief = session.debrief if hasattr(session, "debrief") and session.debrief else None

    return JSONResponse(content={
        "session_id": session_id,
        "status": session.status,
        "ai_ready": ai_ready,
        "ai_feedback": ai_feedback if ai_ready else "",
        "debrief": debrief if ai_ready else {"status": "generating"},
        "scores": session.score if session.score else None,
    })


@app.get("/api/simulation/session/{session_id}/debrief")
async def api_sim_debrief(session_id: str):
    """Retrieve the full debrief after submission (if already scored)."""
    session = simulation_engine.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != "scored":
        raise HTTPException(
            status_code=400,
            detail="Session not yet scored — submit assessment first",
        )
    return JSONResponse(content={
        "session_id": session_id,
        "scores": session.score,
        "debrief": session.debrief,
    })


@app.get("/api/simulation/session/{session_id}/export/json")
async def api_export_json(session_id: str):
    """Export full session transcript as a structured JSON file."""
    session = simulation_engine.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != "scored":
        raise HTTPException(status_code=400, detail="Session not yet scored — submit assessment first")
    from src.simulation.exporter import build_export_dict
    import json
    data = build_export_dict(session)
    return Response(
        content=json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="session_{session_id}.json"'},
    )


@app.get("/api/simulation/session/{session_id}/export/pdf")
async def api_export_pdf(session_id: str):
    """Export full session transcript as a PDF file."""
    session = simulation_engine.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != "scored":
        raise HTTPException(status_code=400, detail="Session not yet scored — submit assessment first")
    from src.simulation.exporter import build_export_dict, generate_pdf
    data = build_export_dict(session)
    pdf_bytes = generate_pdf(data)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="session_{session_id}.pdf"'},
    )


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "inference_mode": os.getenv("VLLM_MODE", "simulated"),
        "agent_loaded": vllm_client is not None,
        "cases_loaded": len(list_cases()),
    }


@app.get("/api/model/status")
async def model_status():
    """Check vLLM inference backend status."""
    if vllm_client is None:
        return {
            "mode": "simulated",
            "connected": False,
            "model": None,
            "message": "Running in keyword-based mode. Set VLLM_MODE=local or VLLM_MODE=cloud to enable AI.",
        }
    healthy = await vllm_client.health_async()
    return {
        "mode": os.getenv("VLLM_MODE", "local"),
        "connected": healthy,
        "model": vllm_client.model,
        "base_url": vllm_client.base_url,
        "message": "Connected" if healthy else "vLLM server not responding",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Case Management API (Phase B)
# ══════════════════════════════════════════════════════════════════════════════


@app.post("/api/cases/import/pubmed")
async def api_import_pubmed(payload: dict):
    """
    Import case reports from PubMed.
    Body: { "specialty": str, "max_results": int (default 5) }
    Returns list of imported case summaries.
    """
    from src.simulation.case_sources.pubmed import search_pubmed_cases, fetch_pubmed_abstracts, abstract_to_case

    specialty = payload.get("specialty", "Emergency Medicine")
    max_results = min(payload.get("max_results", 5), 20)

    if vllm_client is None:
        raise HTTPException(status_code=503, detail="vLLM not connected — set VLLM_MODE=local or cloud")

    pmids = await search_pubmed_cases(specialty, max_results=max_results)
    if not pmids:
        return JSONResponse(content={"imported": 0, "cases": [], "message": f"No case reports found for '{specialty}'"})

    abstracts = await fetch_pubmed_abstracts(pmids[:max_results])
    imported = []
    for abstract in abstracts:
        try:
            case_data = await abstract_to_case(abstract, vllm_client)
            mode = os.getenv("VLLM_MODE", "simulated")
            status = "approved" if mode == "local" else "pending"
            save_case(case_data, source="pubmed", source_ref=abstract["pmid"], status=status)
            imported.append({"case_id": case_data["case_id"], "title": case_data.get("title", ""), "status": status})
        except Exception as e:
            logger.warning("Failed to import PMID %s: %s", abstract.get("pmid"), e)

    return JSONResponse(content={"imported": len(imported), "cases": imported})


@app.post("/api/cases/import/wiley")
async def api_import_wiley(payload: dict):
    """
    Import case reports from Wiley Clinical Case Reports.
    Body: { "query": str (optional), "max_results": int (default 5) }
    """
    from src.simulation.case_sources.wiley import search_wiley_cases, wiley_article_to_case

    query = payload.get("query", "")
    max_results = min(payload.get("max_results", 5), 20)

    if vllm_client is None:
        raise HTTPException(status_code=503, detail="vLLM not connected")

    articles = await search_wiley_cases(query=query, max_results=max_results)
    imported = []
    for article in articles:
        if not article.get("abstract"):
            continue
        try:
            case_data = await wiley_article_to_case(article, vllm_client)
            mode = os.getenv("VLLM_MODE", "simulated")
            status = "approved" if mode == "local" else "pending"
            save_case(case_data, source="wiley", source_ref=article["doi"], status=status)
            imported.append({"case_id": case_data["case_id"], "title": case_data.get("title", ""), "status": status})
        except Exception as e:
            logger.warning("Failed to import DOI %s: %s", article.get("doi"), e)

    return JSONResponse(content={"imported": len(imported), "cases": imported})


@app.post("/api/cases/import/endless")
async def api_import_endless(payload: dict):
    """
    Generate a case from EndlessMedical diagnostic engine.
    Body: { "disease": str }
    """
    from src.simulation.case_sources.endless_medical import build_case_from_disease

    disease = payload.get("disease", "").strip()
    if not disease:
        raise HTTPException(status_code=400, detail="disease is required")
    if vllm_client is None:
        raise HTTPException(status_code=503, detail="vLLM not connected")

    try:
        case_data = await build_case_from_disease(disease, vllm_client)
        mode = os.getenv("VLLM_MODE", "simulated")
        status = "approved" if mode == "local" else "pending"
        save_case(case_data, source="endless_medical", source_ref=disease, status=status)
        return JSONResponse(content={"case_id": case_data["case_id"], "title": case_data.get("title", ""), "status": status})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cases/generate")
async def api_generate_case(payload: dict):
    """
    Generate a case from a Free-text topic, optionally using external sources.
    Body: { "topic": str, "source": "auto"|"pubmed"|"wiley"|"endless_medical" }
    """
    from src.simulation.case_sources.ai_generator import generate_case
    from src.simulation.case_sources.pubmed import search_pubmed_cases, fetch_pubmed_abstracts, abstract_to_case
    from src.simulation.case_sources.wiley import search_wiley_cases, wiley_article_to_case
    from src.simulation.case_sources.endless_medical import build_case_from_disease

    topic = payload.get("topic", "").strip()
    source = payload.get("source", "auto").lower()

    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    if vllm_client is None:
        raise HTTPException(status_code=503, detail="vLLM not connected")

    # User-generated cases are auto-approved so they appear immediately in recommendations
    status = "approved"

    try:
        case_data = None

        # 1. EndlessMedical - diagnostic disease database
        if source == "endless_medical" or source == "auto":
            try:
                case_data = await build_case_from_disease(topic, vllm_client)
            except Exception as e:
                logger.warning("EndlessMedical failed: %s", e)
                if source == "endless_medical":
                    raise

        # 2. PubMed - peer-reviewed case reports
        if not case_data and (source == "pubmed" or source == "auto"):
            try:
                pmids = await search_pubmed_cases(topic, max_results=1)
                if pmids:
                    abstracts = await fetch_pubmed_abstracts(pmids)
                    if abstracts:
                        case_data = await abstract_to_case(abstracts[0], vllm_client)
            except Exception as e:
                logger.warning("PubMed failed: %s", e)
                if source == "pubmed":
                    raise

        # 3. Wiley - clinical case reports from Wiley Open Access
        if not case_data and (source == "wiley" or source == "auto"):
            try:
                articles = await search_wiley_cases(query=topic, max_results=1)
                if articles:
                    case_data = await wiley_article_to_case(articles[0], vllm_client)
            except Exception as e:
                logger.warning("Wiley failed: %s", e)
                if source == "wiley":
                    raise

        # 4. Fallback to generic AI generation
        if not case_data:
            source_text = (
                f"Generate a realistic clinical simulation case about {topic}.\n"
                "Make it clinically accurate with realistic vitals, history, exam findings, "
                "investigations, and management steps with specific drug names and doses."
            )
            case_data = await generate_case(vllm_client, source_text, source_type="ai_generated")

        save_case(case_data, source=case_data.get("_source_type", "ai_generated"), source_ref=case_data.get("_source_ref", ""), status=status)
        return JSONResponse(content={"case_id": case_data["case_id"], "title": case_data.get("title", ""), "status": status})

    except Exception as e:
        logger.error("Generation failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Specific routes MUST come before generic {case_id} routes ─────────────────

@app.get("/api/cases/recommended")
async def api_get_recommended_cases(limit: int = 6):
    """
    Phase F: Fetch cases recommended by the Thompson Sampling Bandit.
    Balances exploration and exploitation based on historical engagement.
    """
    cases = get_recommended_cases(limit=limit)
    return JSONResponse(content=cases)


@app.get("/api/cases/pending")
async def api_pending_cases():
    """Admin: list cases pending review."""
    return JSONResponse(content=get_pending_cases())


@app.get("/api/cases/db")
async def api_list_db_cases(source: str | None = None, status: str | None = None):
    """List all cases in the database with optional filters."""
    return JSONResponse(content=list_db_cases(source=source, status=status))


@app.get("/api/cases/{case_id}")
async def api_get_case(case_id: str):
    """Get a single case by ID."""
    from src.simulation.database import _connect
    import json

    with _connect() as conn:
        row = conn.execute(
            "SELECT case_id, title, specialty, difficulty, source, case_data FROM cases WHERE case_id = ?",
            (case_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Case not found")

    case_data = json.loads(row["case_data"])
    return JSONResponse(content={
        "case_id": row["case_id"],
        "title": row["title"],
        "specialty": row["specialty"],
        "difficulty": row["difficulty"],
        "source": row["source"],
        **case_data
    })


@app.post("/api/cases/{case_id}/approve")
async def api_approve_case(case_id: str):
    """Admin: approve a pending case."""
    if approve_case(case_id):
        return {"status": "approved", "case_id": case_id}
    raise HTTPException(status_code=404, detail="Case not found or not pending")


@app.post("/api/cases/{case_id}/reject")
async def api_reject_case(case_id: str):
    """Admin: reject a pending case."""
    if reject_case(case_id):
        return {"status": "rejected", "case_id": case_id}
    raise HTTPException(status_code=404, detail="Case not found or not pending")


# ── Adaptive Learning / Bandit Routes ─────────────────────────────────────────

class EngagementPayload(BaseModel):
    arm_id: str
    success: bool

@app.post("/api/cases/track_engagement")
async def api_track_engagement(payload: EngagementPayload):
    """
    Phase F: Record a telemetry event (e.g. resident clicks on a case).
    Updates the Bandit's internal Beta distribution for the specified arm.
    """
    update_bandit_state(payload.arm_id, payload.success)
    return {"status": "success", "arm_id": payload.arm_id, "success": payload.success}


@app.post("/api/cases/import/agentclinic")
async def api_import_agentclinic(payload: dict):
    """
    Import cases from AgentClinic benchmark datasets.
    No vLLM required — cases come pre-structured from USMLE/NEJM.

    Body: {
      "dataset": "medqa" | "medqa_ext" | "nejm" | "nejm_ext" (default: "medqa_ext"),
      "max_cases": int (optional, default: all)
    }
    """
    from src.simulation.case_sources.agentclinic import import_agentclinic, DATASET_URLS

    dataset = payload.get("dataset", "medqa_ext")
    max_cases = payload.get("max_cases", None)

    if dataset not in DATASET_URLS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown dataset '{dataset}'. Choose from: {list(DATASET_URLS.keys())}",
        )

    try:
        cases = await import_agentclinic(dataset=dataset, max_cases=max_cases)
        imported = []
        for case_data in cases:
            try:
                save_case(case_data, source="agentclinic", source_ref=dataset, status="approved")
                imported.append({
                    "case_id": case_data["case_id"],
                    "title": case_data.get("title", ""),
                    "specialty": case_data.get("specialty", ""),
                    "difficulty": case_data.get("difficulty", ""),
                })
            except Exception as e:
                logger.warning("Failed to save AgentClinic case %s: %s", case_data.get("case_id"), e)

        return JSONResponse(content={
            "dataset": dataset,
            "imported": len(imported),
            "cases": imported,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cases/import/dicom")
async def api_import_dicom(file: UploadFile = File(...)):
    """Upload a ZIP of DICOM files to create an image-based case."""
    try:
        import uuid
        # Save upload to temporary file
        temp_zip = Path(f"/tmp/{uuid.uuid4().hex}.zip")
        with open(temp_zip, "wb") as f:
            f.write(await file.read())
            
        case = process_dicom_zip(temp_zip)
        temp_zip.unlink(missing_ok=True)
        
        # Save case as pending review
        case.status = "pending"
        save_case(case)
        return JSONResponse({
            "status": "success",
            "message": "DICOM case uploaded and awaiting review",
            "case_id": case.case_id,
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("DICOM import failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/simulation/imaging/dicom_list")
async def api_dicom_list(case_id: str):
    """Returns a list of image URLs for a specific DICOM directory to load a stack."""
    base_dir = Path(__file__).parent / "data" / "imaging" / "dicom" / case_id
    if not base_dir.exists() or not base_dir.is_dir():
        raise HTTPException(status_code=404, detail="DICOM directory not found")
        
    # Find all .dcm files (or files without extension) inside
    urls = []
    for f in base_dir.rglob("*"):
        if f.is_file():
            # Build URL relative to the /imaging static mount
            # Mount maps `/imaging` to `data/imaging`
            rel_path = f.relative_to(base_dir.parent.parent)
            urls.append(f"/imaging/{rel_path.as_posix()}")
            
    urls.sort()  # rough heuristic, better to sort by InstanceNumber in a real app
    return {"urls": urls}


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MedSimulation server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8000, help="Port")
    parser.add_argument("--reload", action="store_true", help="Dev-mode auto-reload")
    args = parser.parse_args()

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
