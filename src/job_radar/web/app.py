"""FastAPI host for the local React app and its JSON API."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from job_radar.config import ROOT
from job_radar.cv_profile import CvSessionStore
from job_radar.web.api import router
from job_radar.web.runner import runner


def create_app(root: Path | None = None, frontend_dist: Path | None = None) -> FastAPI:
    project_root = Path(root or ROOT).resolve()
    dist = Path(frontend_dist or project_root / "frontend" / "dist").resolve()
    app = FastAPI(
        title="job-radar local API",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.root = project_root
    app.state.runner = runner
    app.state.cv_store = CvSessionStore()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.exception_handler(Exception)
    async def unhandled_error(_: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"detail": f"{type(exc).__name__}: {exc}"},
        )

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/robots.txt", include_in_schema=False, response_class=PlainTextResponse)
    def robots():
        return "User-agent: *\nDisallow: /\n"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        index = dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            {
                "detail": "React app is not built yet",
                "build": "npm --prefix frontend install && npm --prefix frontend run build",
                "dev": "npm --prefix frontend run dev",
                "api_docs": "/api/docs",
            },
            status_code=503,
        )

    return app


app = create_app()
