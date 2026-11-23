from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pixelrelay import jobs
from pixelrelay.app import create_app
from pixelrelay.config import load_settings
from tests.fixtures import textured_photo, to_bytes

HEADERS = {"X-PixelRelay": "1"}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    settings = load_settings(
        env={
            "DATA_DIR": str(tmp_path / "data"),
            "WORKER_MODE": "off",
            "AUTH_MODE": "dev",
            "APP_ENV": "development",
            "SECRET_KEY": "test-secret-" + "x" * 40,
            "BASE_URL": "http://testserver",
        },
        env_file=tmp_path / "none.env",
    )
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
