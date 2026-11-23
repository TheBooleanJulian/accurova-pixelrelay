"""File storage behind a small provider interface.

- `LocalDiskStorage`: a folder (development, tests, a server with a disk).
- `R2Storage`: Cloudflare R2 over its S3 API (production: the container's disk is wiped
  when it sleeps). Stdlib only: AWS Signature V4 over urllib, no boto3.

A Google Drive adapter would implement the same three methods; the database keeps only
the provider name and key, so adapters can be added without schema changes.

Every key is written once. `put` refuses to overwrite (a hard link on disk, a conditional
`If-None-Match: *` PUT on R2), which keeps originals, uploads, masks and composites
immutable at the storage layer too.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from . import db

log = logging.getLogger("pixelrelay.storage")


class StorageError(Exception):
    pass


class StorageProvider(Protocol):
    name: str

    def put(self, key: str, data: bytes) -> None: ...

    def read(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


def _check_key(key: str) -> str:
    if not key or key.startswith(("/", "\\")) or "\\" in key or ".." in key.split("/"):
        raise StorageError("invalid storage key")
    return key


class LocalDiskStorage:
    name = "local"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _check_key(key)).resolve()
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


# --------------------------------------------------------------------------
# Cloudflare R2 (S3 API)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_headers(
    *,
    method: str,
    host: str,
    path: str,
    query: str,
    headers: dict[str, str],
    payload_hash: str,
    access_key: str,
    secret_key: str,
    region: str,
    amz_date: str,
    service: str = "s3",
) -> dict[str, str]:
    """AWS Signature Version 4 for one request. `path` must already be URI-encoded.

    Returns every header to send, lower-cased, including `authorization`.
    """
    signed = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    signed["host"] = host
    signed["x-amz-date"] = amz_date
    signed["x-amz-content-sha256"] = payload_hash
    names = sorted(signed)
    canonical = "\n".join(
        [method, path, query, "".join(f"{k}:{signed[k]}\n" for k in names), ";".join(names), payload_hash]
    )
    scope = f"{amz_date[:8]}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, _sha256(canonical.encode())])
    key = _hmac(("AWS4" + secret_key).encode(), amz_date[:8])
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    signed["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"
    )
    return signed


class R2Storage:
    """Write-once object storage in one R2 bucket, reached over the S3 API with a bucket-scoped token."""

    name = "r2"

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str, timeout: float = 120):
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme not in ("https", "http") or not parsed.netloc:
            raise StorageError("R2_ENDPOINT must be a URL")
        self.base, self.host = f"{parsed.scheme}://{parsed.netloc}", parsed.netloc
        self.bucket, self.access_key, self.secret_key, self.timeout = bucket, access_key, secret_key, timeout

    def _request(self, method: str, key: str, body: bytes = b"", headers: dict | None = None):
        path = "/" + urllib.parse.quote(f"{self.bucket}/{_check_key(key)}", safe="/-_.~")
        extra = dict(headers or {})
        if method == "PUT":
            extra["content-length"] = str(len(body))
        signed = sigv4_headers(
            method=method,
            host=self.host,
            path=path,
            query="",
            headers=extra,
            payload_hash=_sha256(body),
            access_key=self.access_key,
            secret_key=self.secret_key,
            region="auto",
            amz_date=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        )
        signed.pop("host")  # urllib sends the same value itself
        req = urllib.request.Request(
            self.base + path, data=body if method == "PUT" else None, method=method, headers=signed
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def _call(self, method: str, key: str, body: bytes = b"", headers: dict | None = None):
        """Retry network errors and 5xx a few times. Returns (status, body, attempt number)."""
        last: Exception | None = None
        for attempt in range(4):
            if attempt:
                time.sleep(0.5 * 2**attempt)
            try:
                status, data = self._request(method, key, body, headers)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                continue
            if status < 500:
                return status, data, attempt
            last = StorageError(f"R2 {method} answered {status}")
        log.error("R2 %s failed after retries: %s", method, type(last).__name__)
        raise StorageError("storage is unreachable") from last

    def put(self, key: str, data: bytes) -> None:
        status, _, attempt = self._call("PUT", key, data, {"if-none-match": "*"})
        if status in (200, 201):
            return
        if status == 412:
            if attempt and self.exists(key):
                return  # an earlier attempt that timed out did write it (keys are fresh UUIDs)
            raise StorageError(f"refusing to overwrite {key}")
        raise StorageError(f"R2 PUT answered {status}")

    def read(self, key: str) -> bytes:
        status, data, _ = self._call("GET", key)
        if status == 200:
            return data
        if status == 404:
            raise StorageError("file missing from storage")
        raise StorageError(f"R2 GET answered {status}")

    def exists(self, key: str) -> bool:
        status, _, _ = self._call("HEAD", key)
        if status == 200:
            return True
        if status == 404:
            return False
        raise StorageError(f"R2 HEAD answered {status}")


def from_settings(settings) -> StorageProvider:
    if settings.storage_provider == "r2":
        return R2Storage(
            settings.r2_endpoint, settings.r2_bucket, settings.r2_access_key_id, settings.r2_secret_access_key
        )
    return LocalDiskStorage(settings.files_dir)


CONTENT_TYPES = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
    "TIFF": ("image/tiff", "tif"),
    "ZIP": ("application/zip", "zip"),
}


def save_asset(
    conn: db.Conn,
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
