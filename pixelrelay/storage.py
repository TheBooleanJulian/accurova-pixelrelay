"""File storage behind a small provider interface.

Only `LocalDiskStorage` exists today. A Google Drive or Supabase Storage
adapter implements the same four methods; the database keeps only the
provider name and key, so adapters can be added without schema changes.

Every key is written once. `put` refuses to overwrite, which is what keeps
originals, uploads, masks and composites immutable at the storage layer too.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Protocol

from . import db


class StorageError(Exception):
    pass


class StorageProvider(Protocol):
    name: str

    def put(self, key: str, data: bytes) -> None: ...

    def open_path(self, key: str) -> Path: ...

    def read(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


class LocalDiskStorage:
    name = "local"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not key or key.startswith(("/", "\\")) or ".." in key.split("/"):
            raise StorageError("invalid storage key")
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise StorageError("invalid storage key")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            # os.link fails if the target exists: write-once semantics.
            os.link(tmp, path)
        except FileExistsError as exc:
            raise StorageError(f"refusing to overwrite {key}") from exc
        finally:
            tmp.unlink(missing_ok=True)

    def open_path(self, key: str) -> Path:
        path = self._path(key)
        if not path.is_file():
            raise StorageError("file missing from storage")
        return path

    def read(self, key: str) -> bytes:
        return self.open_path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


CONTENT_TYPES = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
    "TIFF": ("image/tiff", "tif"),
    "ZIP": ("application/zip", "zip"),
}


def save_asset(
    conn: sqlite3.Connection,
    storage: StorageProvider,
    *,
    project_id: str,
    kind: str,
    data: bytes,
    fmt: str,
    width: int | None = None,
    height: int | None = None,
    photo_id: str | None = None,
    member_id: str | None = None,
    original_filename: str | None = None,
) -> str:
    """Write bytes to storage under a fresh key and record an immutable asset row."""
    asset_id = db.new_id()
    content_type, ext = CONTENT_TYPES[fmt]
    key = f"projects/{project_id}/{kind}/{asset_id}.{ext}"
    storage.put(key, data)
    db.insert(
        conn,
        "assets",
        {
            "id": asset_id,
            "project_id": project_id,
            "photo_id": photo_id,
            "member_id": member_id,
            "kind": kind,
            "storage_provider": storage.name,
            "storage_key": key,
            "content_type": content_type,
            "byte_size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "width": width,
            "height": height,
            "format": fmt,
            "original_filename": original_filename,
            "created_at": db.now_iso(),
        },
    )
    return asset_id
