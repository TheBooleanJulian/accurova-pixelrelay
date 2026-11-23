"""Shared web plumbing: DB dependency, cookies, client IP, upload reading, errors."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import HTTPException, Request, Response, UploadFile

from . import db


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.settings.db_path)
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
