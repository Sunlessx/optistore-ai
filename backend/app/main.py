"""OptiStore AI - FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import require_api_key
from app.api.routes import analysis, files, locations, optimize, plans
from app.core.config import get_settings
from app.core.paths import PathNotAllowedError
from app.db.migrate import upgrade_database
from app.services.embeddings import embedder_status
from app.services.orchestrator import ActionError
from app.services.s3_service import S3NotConfiguredError, S3PermissionError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("optistore")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    upgrade_database()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="OptiStore AI API",
        version="0.1.0",
        description=(
            "AI-assisted storage analysis: inventory, exact & semantic duplicates, consolidation, "
            "measured compression, access prediction, S3 cost optimisation and approval-gated plans."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    def _error(status: int):
        async def handler(_request: Request, exc: Exception):
            return JSONResponse(status_code=status, content={"detail": str(exc)})

        return handler

    app.add_exception_handler(PathNotAllowedError, _error(403))
    app.add_exception_handler(S3PermissionError, _error(403))
    app.add_exception_handler(S3NotConfiguredError, _error(503))
    app.add_exception_handler(ActionError, _error(409))
    app.add_exception_handler(LookupError, _error(404))
    app.add_exception_handler(ValueError, _error(400))

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error. See server logs."})

    @app.get("/api/health", tags=["health"])
    def health():
        from app.services.tools import tool_status
        from app.services.vector_index import active_backend

        return {
            "status": "ok",
            "app": settings.app_name,
            "embedding": embedder_status(),
            "vector_backend": active_backend(),
            "tools": tool_status(),
        }

    api = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])
    for module in (locations, files, analysis, optimize, plans):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
