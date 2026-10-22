"""DocuMind AI application entry point.

Run with:  uvicorn app.main:app --reload
"""
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.ai.embeddings import get_embedder
from app.ai.llm import get_llm
from app.api.routes import auth, chat, documents, health, sessions, usage
from app.config import DEFAULT_JWT_SECRET, get_settings
from app.core.errors import register_exception_handlers
from app.core.ratelimit import limiter
from app.db.database import init_db
from app.logging_config import request_id_var, setup_logging

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("documind")

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.chroma_path).mkdir(parents=True, exist_ok=True)
    embedder = get_embedder()  # load the model once at startup instead of on the first request
    llm = get_llm()
    log.info(
        "DocuMind ready | env=%s | mode=%s | model=%s | embeddings=%s",
        settings.app_env,
        "claude" if llm.available else "demo (no ANTHROPIC_API_KEY)",
        llm.model if llm.available else "-",
        embedder.name,
    )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level, json_logs=settings.is_production)
    if settings.is_production and settings.jwt_secret == DEFAULT_JWT_SECRET:
        raise RuntimeError("Set a strong JWT_SECRET before running with APP_ENV=production.")

    app = FastAPI(
        title="DocuMind AI",
        version="1.0.0",
        description="Ask questions about your documents and get grounded answers with citations.",
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    register_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        elapsed = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            response.headers["Content-Security-Policy"] = _CSP
        if request.url.path.startswith("/api"):
            log.info("%s %s -> %s (%d ms)", request.method, request.url.path, response.status_code, elapsed)
        return response

    for module in (health, auth, documents, chat, sessions, usage):
        app.include_router(module.router)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
