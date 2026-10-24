"""Punto de entrada de la API (FastAPI) y del frontend estático (PWA)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import settings
from .errors import AppError
from .jobs import queue, tasks  # noqa: F401  (registra los manejadores)
from .api import auth, dialogues, files, identities, processing, projects, studio, system

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

_worker: queue.Worker | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker
    settings.ensure_dirs()
    db.conn()
    if settings.inline_worker:
        _worker = queue.Worker(settings.worker_concurrency)
        _worker.start()
    yield
    if _worker:
        _worker.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Estudio IA de vídeo", version="0.3.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
        if not request.url.path.startswith("/api/docs"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' blob: data:; "
                "media-src 'self' blob:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        return response

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status,
                            content={"error": exc.code, "message": exc.message, "details": exc.details})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": "datos_no_validos",
                                                      "message": "Algunos datos enviados no son válidos.",
                                                      "details": {"errors": exc.errors()[:5]}},)

    for r in (auth.router, projects.router, studio.router, identities.router, identities.public, dialogues.router,
              processing.router, files.router, system.router):
        app.include_router(r)

    if FRONTEND.is_dir():
        app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

        @app.get("/sw.js", include_in_schema=False)
        def sw():
            return FileResponse(FRONTEND / "sw.js", media_type="application/javascript",
                                headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

        @app.get("/manifest.webmanifest", include_in_schema=False)
        def manifest():
            return FileResponse(FRONTEND / "manifest.webmanifest", media_type="application/manifest+json")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
