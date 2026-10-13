import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from src.simulation.database import _connect, init_db
from src.simulation.cases import CASES, load_cases_from_json
from src.simulation.simulator import SimulationEngine
from src.simulation.imaging import IMAGING_DIR
from .config import Settings
from .models import DicomList, Health, ModelStatus, Status
from .routers import cases, exports, simulation
from .security import current_user, rate_limit_actor

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


def create_app(settings=None, agent=None, initialize_agent=True):
    settings = settings or Settings()
    settings.validate()

    async def warmup(app):
        app.state.warming_up = True
        try:
            for attempt in range(30):
                try:
                    if await app.state.agent.health_async():
                        await app.state.agent.chat_async(
                            [{"role": "user", "content": "Say ready."}], max_tokens=10, timeout=60
                        )
                        app.state.ready = True
                        return
                except Exception:
                    logger.info("Patient service is not ready (attempt %s)", attempt + 1)
                await asyncio.sleep(2)
        finally:
            app.state.warming_up = False

    @asynccontextmanager
    async def lifespan(app):
        load_cases_from_json()
        init_db()
        if app.state.agent is None and initialize_agent:
            from src.simulation.vllm_client import VLLMClient

            app.state.agent = VLLMClient.from_env()
        app.state.simulation_engine = SimulationEngine(app.state.agent)
        if app.state.agent is not None and initialize_agent:
            app.state.warmup_task = asyncio.create_task(warmup(app))
        try:
            from src.simulation.imaging import generate_sample_ecgs

            for case_id, studies in generate_sample_ecgs().items():
                if case_id in CASES:
                    CASES[case_id].imaging_studies = studies
        except Exception:
            logger.warning("Sample imaging could not be generated")
        yield
        if app.state.warmup_task:
            app.state.warmup_task.cancel()
            with suppress(asyncio.CancelledError):
                await app.state.warmup_task

    app = FastAPI(title="MedSimulation: Clinical Reasoning Practice", lifespan=lifespan)
    app.state.settings = settings
    app.state.agent = agent
    app.state.ready = agent is not None
    app.state.warming_up = False
    app.state.warmup_task = None
    app.state.reload_portrait_volume = None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.middleware("http")
    async def headers(request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled request failure")
            response = JSONResponse({"detail": "Request failed. Please retry."}, status_code=500)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith(("/api/", "/imaging/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")

    @app.get("/", include_in_schema=False)
    def home(request: Request):
        return templates.TemplateResponse(request, "home.html")

    @app.get("/simulation", include_in_schema=False)
    def workspace(request: Request):
        return templates.TemplateResponse(
            request,
            "simulation.html",
            {"passwordless_access": True},
        )

    @app.get("/review", include_in_schema=False)
    def review_page(request: Request):
        raise HTTPException(404, "Not found")

    @app.get("/api/health", response_model=Health)
    def health():
        with _connect() as conn:
            conn.execute("SELECT 1")
        return dict(
            status="ok",
            vllm_ready=app.state.ready,
            vllm_warming_up=app.state.warming_up,
            agent_loaded=app.state.agent is not None,
            cases_loaded=len(CASES),
        )

    @app.post("/api/vllm/warmup", response_model=Status, dependencies=[Depends(current_user)])
    async def trigger_warmup(request: Request):
        rate_limit_actor(request, "warmup", 3)
        if app.state.agent is None:
            raise HTTPException(503, "Patient service is not configured")
        if not app.state.ready and not app.state.warming_up:
            app.state.warming_up = True
            app.state.warmup_task = asyncio.create_task(warmup(app))
        return {"status": "ready" if app.state.ready else "warming_up"}

    @app.get("/api/model/status", response_model=ModelStatus, dependencies=[Depends(current_user)])
    def model_status():
        return {
            "connected": app.state.ready,
            "message": "Ready" if app.state.ready else "Unavailable",
        }

    def authorize_image(request, path):
        user = current_user(request)
        if user["role"] == "reviewer":
            return
        from src.simulation.database import decode

        with _connect() as conn:
            rows = conn.execute(
                "SELECT session_data FROM sessions JOIN session_owners USING(session_id) WHERE user_id=?",
                (user["user_id"],),
            ).fetchall()
        for row in rows:
            data = decode(row["session_data"])
            for study in data.get("case_snapshot", {}).get("imaging_studies", []):
                file_path = study.get("file_path", "")
                if path == file_path or (
                    file_path.startswith("dicom/") and path.startswith(file_path + "/")
                ):
                    return
        raise HTTPException(404, "Image not found")

    @app.get("/imaging/{path:path}", include_in_schema=False)
    def image_file(path: str, request: Request):
        base = IMAGING_DIR.resolve()
        target = (base / path).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise HTTPException(404, "Image not found")
        authorize_image(request, path)
        return FileResponse(target)

    @app.get(
        "/api/simulation/imaging/dicom_list",
        response_model=DicomList,
        dependencies=[Depends(current_user)],
    )
    def dicom_list(case_id: str, request: Request):
        import re

        if not re.fullmatch(r"[\w-]+", case_id):
            raise HTTPException(422, "Invalid case identifier")
        base = IMAGING_DIR / "dicom" / case_id
        paths = sorted(p for p in base.rglob("*") if p.is_file())
        if not paths:
            raise HTTPException(404, "DICOM series not found")
        authorize_image(request, paths[0].relative_to(IMAGING_DIR).as_posix())
        return {"urls": ["/imaging/" + p.relative_to(IMAGING_DIR).as_posix() for p in paths]}

    for router in (simulation.router, cases.router, exports.router):
        app.include_router(router)
    return app
