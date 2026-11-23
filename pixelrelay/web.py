"""Shared web plumbing: DB dependency, cookies, client IP, upload reading, errors."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import quote

from fastapi import HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from . import db
from .storage import LocalDiskStorage


def get_conn(request: Request) -> Iterator[db.Conn]:
    conn = db.connect_settings(request.app.state.settings)
    try:
        yield conn
    finally:
        conn.close()


def set_cookie(request: Request, response: Response, name: str, value: str, days: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=days * 86400,
        path="/",
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.cookie_secure,
    )


def clear_cookie(request: Request, response: Response, name: str) -> None:
    response.delete_cookie(
        name, path="/", httponly=True, samesite="lax", secure=request.app.state.settings.cookie_secure
    )


def client_ip(request: Request) -> str:
    if request.app.state.settings.trust_proxy:
        for header in ("cf-connecting-ip", "x-real-ip"):
            if request.headers.get(header):
                return request.headers[header].strip()
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def file_response(storage, asset, *, filename: str | None = None, headers: dict | None = None) -> Response:
    """Serve a stored asset: streamed from disk locally, from memory when it comes from R2."""
    if isinstance(storage, LocalDiskStorage):
        path = storage.open_path(asset["storage_key"])
        return FileResponse(path, media_type=asset["content_type"], filename=filename, headers=headers)
    response = Response(storage.read(asset["storage_key"]), media_type=asset["content_type"], headers=headers)
    if filename:
        response.headers["content-disposition"] = content_disposition(filename)
    return response


def content_disposition(filename: str) -> str:
    """What FileResponse sends: a plain filename, or RFC 5987 encoding for non-ASCII names."""
    quoted = quote(filename)
    if quoted != filename:
        return f"attachment; filename*=utf-8''{quoted}"
    return f'attachment; filename="{filename}"'


def read_upload(request: Request, file: UploadFile) -> bytes:
    limit = request.app.state.settings.max_upload_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Files must be {request.app.state.settings.max_upload_mb} MB or smaller.")
    return data


def bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail=message)
