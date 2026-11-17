import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

from offbook import config, db, jobs
from offbook.api import audio as audio_api, auth as auth_api, scripts as scripts_api
from offbook.api.deps import get_conn, require_xhr
from offbook.auth import Throttle


def create_app(settings: config.Settings | None = None) -> FastAPI:
    settings = settings or config.load()
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    conn.close()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = None
        if settings.start_worker:
            worker = jobs.Worker(settings)
            worker.start()
        yield
        if worker:
            worker.stop()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan,
                  dependencies=[Depends(require_xhr)])
    app.state.settings = settings
    app.state.throttle = Throttle()
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
                       max_age=60 * 60 * 24 * 60, https_only=settings.secure_cookies, session_cookie="offbook")

    app.include_router(auth_api.router)
    app.include_router(scripts_api.router)
    app.include_router(audio_api.router)

    @app.get("/api/health")
    def health(conn=Depends(get_conn)):
        return {"ok": True, "version": config.VERSION, "schema": db.schema_version(conn)}

    _mount_frontend(app, settings.web_dir)
    return app


mimetypes.add_type("application/manifest+json", ".webmanifest")


def _mount_frontend(app: FastAPI, web_dir: Path) -> None:
    """Serve the built SPA. Real files are served as-is; any other non-API path gets index.html so the
    client-side router can handle it."""
    root = web_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request):
        if path.startswith("api/") or path == "api":
            raise HTTPException(404)
        if path:
            target = (root / path).resolve()
            if target.is_relative_to(root) and target.is_file():
                return FileResponse(target)
        index = root / "index.html"
        if not index.is_file():
            raise HTTPException(404, "frontend not built")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
