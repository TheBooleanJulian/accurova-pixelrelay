"""SQLite access: connections, migrations and small query helpers (stdlib sqlite3)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso(offset: timedelta | None = None) -> str:
    moment = datetime.now(UTC)
    if offset:
        moment += offset
    return moment.isoformat(timespec="milliseconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db(path: Path) -> None:
    """Create the database if needed and apply pending migrations (idempotent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        # WAL lets the background worker write while requests read.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ( version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = file.stem
            if version in applied:
                continue
            sql = file.read_text(encoding="utf-8")
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _split_sql(sql):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, now_iso()),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()


def _split_sql(sql: str) -> list[str]:
    """Split a migration into statements, keeping trigger bodies (BEGIN ... END;) whole."""
    statements: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not buf and (not stripped or stripped.startswith("--")):
            continue
        buf.append(line)
        candidate = "\n".join(buf)
        if stripped.endswith(";") and sqlite3.complete_statement(candidate):
            statements.append(candidate)
            buf = []
    if "\n".join(buf).strip():
        statements.append("\n".join(buf))
    return statements


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, rolling back on error. Not re-entrant."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def one(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def all_rows(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))


def update(conn: sqlite3.Connection, table: str, row_id: str, values: dict[str, Any]) -> None:
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), row_id))


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


def log_event(
    conn: sqlite3.Connection,
    project_id: str,
    kind: str,
    actor_type: str,
    actor_id: str | None = None,
    photo_id: str | None = None,
    data: dict | None = None,
) -> None:
    insert(
        conn,
        "activity_events",
        {
            "id": new_id(),
            "project_id": project_id,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "kind": kind,
            "photo_id": photo_id,
            "data_json": dumps(data or {}),
            "created_at": now_iso(),
        },
    )
