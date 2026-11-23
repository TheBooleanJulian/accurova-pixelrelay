"""FastAPI application factory, middleware, page routes and file serving."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, access, db, jobs
from .api_auth import router as auth_router
from .api_owner import router as owner_router
from .api_room import invite_router
from .api_room import router as room_router
from .config import Settings, load_settings
from .images import configure as configure_images
from .logging_setup import setup_logging
from .storage import LocalDiskStorage, StorageError
from .web import get_conn

WEB_DIR = Path(__file__).parent / "web"
log = logging.getLogger("pixelrelay")

CSP = (
    "default-src 'self'; img-src 'self' blob: data:; script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    setup_logging()
    configure_images(settings.max_image_megapixels)
    db.init_db(settings.db_path)
    storage = LocalDiskStorage(settings.files_dir)
    ctx = jobs.Context(settings, storage, settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = None
        if settings.worker_mode == "thread":
            worker = jobs.Worker(ctx)
            worker.start()
        yield
        if worker:
            worker.stop()

    app = FastAPI(
        title="PixelRelay",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/openapi.json",
    )
    app.state.settings = settings
    app.state.storage = storage
    app.state.jobs = ctx

    allowed_origin = "{0.scheme}://{0.netloc}".format(urlsplit(settings.base_url))

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # CSRF: state-changing API calls must carry our header (which a
        # cross-site form cannot set) and, when present, a same-site Origin.
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("x-pixelrelay") != "1":
                return JSONResponse({"detail": "Request blocked."}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin not in (allowed_origin, f"{request.url.scheme}://{request.url.netloc}"):
                return JSONResponse({"detail": "Request blocked."}, status_code=403)
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "input"
        msg = first.get("msg", "Invalid input").removeprefix("Value error, ")
        return JSONResponse({"detail": f"{field}: {msg}"}, status_code=422)

    @app.exception_handler(StorageError)
    async def storage_error(request: Request, exc: StorageError):
        log.error("storage error on %s", request.url.path)
        return JSONResponse({"detail": "Couldn't read or save the file. Try again."}, status_code=500)

    app.include_router(auth_router)
    app.include_router(owner_router)
    app.include_router(room_router)
    app.include_router(invite_router)

    @app.get("/healthz")
    def healthz(conn: sqlite3.Connection = Depends(get_conn)):
        conn.execute("SELECT 1").fetchone()
        return {"ok": True, "version": __version__}

    @app.get("/api/files/{asset_id}")
    def get_file(asset_id: str, request: Request, download: int = 0, conn: sqlite3.Connection = Depends(get_conn)):
        asset = db.one(conn, "SELECT * FROM assets WHERE id = ?", (asset_id,))
        if not asset:
            raise access.not_found()
        owner = access.current_owner(request, conn)
        project = db.one(conn, "SELECT * FROM projects WHERE id = ?", (asset["project_id"],))
        room = None
        if project and project["status"] == "active":
            session = access.room_session(request, conn, project)
            if session and session["member_id"]:
                member = db.one(
                    conn, "SELECT * FROM project_members WHERE id = ? AND removed_at IS NULL", (session["member_id"],)
                )
                room = access.Room(project, session, member) if member else None
        if not access.can_read_asset(conn, asset, owner, room):
            raise access.not_found()
        path = storage.open_path(asset["storage_key"])
        headers = {"Cache-Control": "private, max-age=31536000, immutable"}  # assets never change
        if download:
            ext = path.suffix
            name = (
                (asset["original_filename"] or f"pixelrelay-{asset['kind']}")
                if asset["kind"] in ("original", "upload", "export")
                else f"pixelrelay-{asset['kind']}-{asset_id[:8]}{ext}"
            )
            return FileResponse(path, media_type=asset["content_type"], filename=name, headers=headers)
        return FileResponse(path, media_type=asset["content_type"], headers=headers)

    def page(name: str):
        def handler():
            return FileResponse(WEB_DIR / name, headers={"Cache-Control": "no-cache"})

        return handler

    app.add_api_route("/", page("index.html"), include_in_schema=False)
    app.add_api_route("/login", page("login.html"), include_in_schema=False)
    app.add_api_route("/auth/callback", page("auth-callback.html"), include_in_schema=False)
    app.add_api_route("/owner", page("owner.html"), include_in_schema=False)
    app.add_api_route("/owner/{rest:path}", page("owner.html"), include_in_schema=False)
    app.add_api_route("/r/{slug}", page("room.html"), include_in_schema=False)
    app.add_api_route("/r/{slug}/{rest:path}", page("room.html"), include_in_schema=False)
    app.add_api_route("/invite", page("invite.html"), include_in_schema=False)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    return app
