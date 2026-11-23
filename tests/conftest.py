"""Shared fixtures. By default every test runs on SQLite and a temp folder.

Production-like runs (CI does both):
  PIXELRELAY_TEST_PG_ADMIN_URL=postgresql://postgres:…@host/db  run on Postgres. Each test re-creates the
      accurova_pixelrelay schema from migrations/postgres/ as that admin, then the app connects as the
      least-privilege accurova_pixelrelay_app role, so a missing grant fails a test.
  PIXELRELAY_TEST_STORAGE=r2  store files through the R2 adapter against an in-memory S3 server.
"""

from __future__ import annotations

import os
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from pixelrelay import db, jobs
from pixelrelay.app import create_app
from pixelrelay.config import load_settings
from tests.fake_s3 import ACCESS_KEY, SECRET_KEY, FakeS3
from tests.fixtures import textured_photo, to_bytes

HEADERS = {"X-PixelRelay": "1"}
PG_ADMIN_URL = os.environ.get("PIXELRELAY_TEST_PG_ADMIN_URL", "")
STORAGE = os.environ.get("PIXELRELAY_TEST_STORAGE", "local")
APP_ROLE, APP_PASSWORD = "accurova_pixelrelay_app", "pixelrelay-test-only"


def reset_pg_schema() -> str:
    """Fresh schema from the migration file; returns the app role's connection URL."""
    import psycopg

    migration = "\n".join(f.read_text(encoding="utf-8") for f in sorted(db.PG_MIGRATIONS_DIR.glob("*.sql")))
    with psycopg.connect(PG_ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {db.DEFAULT_SCHEMA} CASCADE")
        admin.execute(migration)
        admin.execute(f"ALTER ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'")
    parts = urllib.parse.urlsplit(PG_ADMIN_URL)
    host = parts.netloc.rsplit("@", 1)[-1]
    return urllib.parse.urlunsplit(parts._replace(netloc=f"{APP_ROLE}:{APP_PASSWORD}@{host}"))


@pytest.fixture(scope="session")
def fake_s3():
    server = FakeS3()
    yield server
    server.close()


@pytest.fixture()
def app(tmp_path, request):
    env = {
        "DATA_DIR": str(tmp_path / "data"),
        "WORKER_MODE": "off",
        "AUTH_MODE": "dev",
        "APP_ENV": "development",
        "SECRET_KEY": "test-secret-" + "x" * 40,
        "BASE_URL": "http://testserver",
    }
    if PG_ADMIN_URL:
        env["DATABASE_URL"] = reset_pg_schema()
    if STORAGE == "r2":
        s3 = request.getfixturevalue("fake_s3")
        env.update(
            STORAGE_PROVIDER="r2",
            R2_ENDPOINT=s3.endpoint,
            R2_BUCKET=f"test-{tmp_path.name}",
            R2_ACCESS_KEY_ID=ACCESS_KEY,
            R2_SECRET_ACCESS_KEY=SECRET_KEY,
        )
    settings = load_settings(env=env, env_file=tmp_path / "none.env")
    return create_app(settings)


def make_client(app) -> TestClient:
    c = TestClient(app)
    c.headers.update(HEADERS)
    return c


@pytest.fixture()
def owner(app):
    c = make_client(app)
    r = c.post("/api/auth/dev-login", json={"email": "owner@example.com"})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture()
def run_jobs(app):
    def run():
        return jobs.run_pending(app.state.jobs)

    return run


@pytest.fixture(scope="session")
def photo_bytes():
    return to_bytes(textured_photo(), "JPEG", quality=95)


@pytest.fixture()
def project(owner, run_jobs, photo_bytes):
    """A project with three participants and three ready photos."""
    r = owner.post("/api/projects", json={"name": "Group shoot", "participants": ["Ava", "Ben", "Cy"]})
    assert r.status_code == 201, r.text
    p = r.json()
    for i in range(3):
        r = owner.post(f"/api/projects/{p['id']}/photos", files={"file": (f"IMG_{i}.jpg", photo_bytes, "image/jpeg")})
        assert r.status_code == 201, r.text
    run_jobs()
    members = owner.get(f"/api/projects/{p['id']}/members").json()["members"]
    p["members"] = {m["display_name"]: m["id"] for m in members}
    p["photos"] = [x["id"] for x in owner.get(f"/api/projects/{p['id']}/photos").json()["photos"]]
    return p


def join(app, project, name: str) -> TestClient:
    c = make_client(app)
    r = c.post(f"/api/rooms/{project['slug']}/join", json={"pin": project["pin"]})
    assert r.status_code == 200, r.text
    r = c.post(f"/api/rooms/{project['slug']}/identify", json={"member_id": project["members"][name]})
    assert r.status_code == 200, r.text
    return c
