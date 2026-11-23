"""The two-backend database layer."""

from __future__ import annotations

import pytest

from pixelrelay import db
from tests.conftest import PG_ADMIN_URL


def test_pg_sql_rewrites_placeholders_only_when_there_are_params():
    assert db._pg_sql("SELECT * FROM t WHERE a = ? AND b LIKE '%x'", True) == (
        "SELECT * FROM t WHERE a = %s AND b LIKE '%%x'"
    )
    assert db._pg_sql("SELECT '%'", False) == "SELECT '%'"
    assert db._pg_params((True, False, 3, "x", None)) == (1, 0, 3, "x", None)


def test_pg_row_behaves_like_sqlite_row():
    row = db.PgRow(("id", "name", "id"), {"id": 0, "name": 1}, ("a", "b", "c"))
    assert row["id"] == "a" and row[1] == "b" and row[-1] == "c"
    assert row.keys() == ["id", "name", "id"] and list(row) == ["a", "b", "c"] and len(row) == 3
    assert dict(zip(row.keys(), row, strict=True))["name"] == "b"


def test_transaction_rolls_back_on_error(app):
    conn = db.connect_settings(app.state.settings)
    try:
        with pytest.raises(RuntimeError), db.transaction(conn):
            db.insert(
                conn,
                "jobs",
                {
                    "id": "j1",
                    "kind": "x",
                    "status": "queued",
                    "run_after": 0.0,
                    "created_at": db.now_iso(),
                    "updated_at": db.now_iso(),
                },
            )
            raise RuntimeError("boom")
        assert db.one(conn, "SELECT id FROM jobs WHERE id = ?", ("j1",)) is None
        with db.transaction(conn):
            db.insert(
                conn,
                "jobs",
                {
                    "id": "j2",
                    "kind": "x",
                    "status": "queued",
                    "run_after": 0.0,
                    "created_at": db.now_iso(),
                    "updated_at": db.now_iso(),
                },
            )
        assert db.one(conn, "SELECT id FROM jobs WHERE id = ?", ("j2",))["id"] == "j2"
    finally:
        conn.close()


@pytest.mark.skipif(not PG_ADMIN_URL, reason="needs PIXELRELAY_TEST_PG_ADMIN_URL")
def test_postgres_without_the_schema_fails_with_a_clear_message(app):
    with pytest.raises(RuntimeError, match="Apply the accurova_pixelrelay migration"):
        db.init_db(app.state.settings.database_url, "no_such_schema")


@pytest.mark.skipif(not PG_ADMIN_URL, reason="needs PIXELRELAY_TEST_PG_ADMIN_URL")
def test_postgres_job_claim_skips_locked_rows(app):
    from pixelrelay import jobs

    first, second = db.connect_settings(app.state.settings), db.connect_settings(app.state.settings)
    try:
        jobs.enqueue(first, "noop", {}, None)
        jobs.enqueue(first, "noop", {}, None)
        first.raw.execute("BEGIN")
        a = jobs.claim(first)  # row stays locked until COMMIT
        b = jobs.claim(second)  # must take the other job instead of waiting or doubling up
        first.raw.execute("COMMIT")
        assert a and b and a["id"] != b["id"]
    finally:
        first.close()
        second.close()
