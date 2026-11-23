"""Database access for two backends behind one small API.

- SQLite (stdlib sqlite3): local development, tests, a single server with a disk.
- Postgres (psycopg): production on Cloudflare, in the app's own schema of the shared
  Supabase project. The container's disk is wiped when it sleeps, so data can't live there.

Callers only use `connect`, `transaction`, `one`, `all_rows`, `insert`, `update` and
`conn.execute(sql, params)` with `?` placeholders. For Postgres, `Conn` rewrites `?` to `%s`
and turns Python booleans into 0/1, because the schema keeps SQLite's integer flags.
Every `transaction()` on Postgres takes one advisory lock, which gives the same
"one writer at a time" behaviour as SQLite's BEGIN IMMEDIATE, so the code that checks and then
writes (revision numbers, assignments, PIN attempts) is equally safe on both.

SQLite applies its own migrations (`migrations/*.sql`). Postgres never runs DDL from the app: the
schema is applied from thebooleanjulian-db (an identical copy is in `migrations/postgres/`), and
the app's role can only read and write rows. `init_db` checks that the schema is there.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql as pgsql

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
PG_MIGRATIONS_DIR = MIGRATIONS_DIR / "postgres"
DEFAULT_SCHEMA = "accurova_pixelrelay"

# Both drivers' errors, for callers (and tests) that catch database failures.
DatabaseError = (sqlite3.DatabaseError, psycopg.DatabaseError)
IntegrityError = (sqlite3.IntegrityError, psycopg.IntegrityError)


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso(offset: timedelta | None = None) -> str:
    moment = datetime.now(UTC)
    if offset:
        moment += offset
    return moment.isoformat(timespec="milliseconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def is_postgres(target: Path | str) -> bool:
    return isinstance(target, str) and target.startswith(("postgres://", "postgresql://"))


# --------------------------------------------------------------------------
# Rows and connections


class PgRow:
    """sqlite3.Row look-alike for Postgres: row["col"], row[0], keys(), dict(row)."""

    __slots__ = ("_index", "_names", "_values")

    def __init__(self, names: tuple[str, ...], index: dict[str, int], values: tuple):
        self._names, self._index, self._values = names, index, values

    def __getitem__(self, key: int | str | slice) -> Any:
        if isinstance(key, (int, slice)):
            return self._values[key]
        return self._values[self._index[key]]

    def keys(self) -> list[str]:
        return list(self._names)

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"PgRow({dict(zip(self._names, self._values, strict=False))!r})"


Row = sqlite3.Row | PgRow


def _pg_row_factory(cursor):
    if cursor.description is None:
        return lambda values: values
    names = tuple(c.name for c in cursor.description)
    index: dict[str, int] = {}
    for i, name in enumerate(names):
        index.setdefault(name, i)  # like sqlite3.Row: the first column of that name wins
    return lambda values: PgRow(names, index, values)


@lru_cache(maxsize=2048)
def _pg_sql(sql: str, has_params: bool) -> str:
    # The app's SQL never has a literal "?" or "%" inside a string, so this rewrite is safe.
    return sql.replace("%", "%%").replace("?", "%s") if has_params else sql


def _pg_params(params: tuple | list) -> tuple:
    return tuple(int(v) if isinstance(v, bool) else v for v in params)


class Conn:
    """One connection, either backend. `raw` is the driver's own connection."""

    def __init__(self, raw: Any, dialect: str, schema: str = DEFAULT_SCHEMA, pool: _PgPool | None = None):
        self.raw, self.dialect, self.schema, self._pool = raw, dialect, schema, pool

    def execute(self, sql: str, params: tuple | list = ()):
        if self.dialect == "postgres":
            return self.raw.execute(_pg_sql(sql, bool(params)), _pg_params(params) if params else None)
        return self.raw.execute(sql, params)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.release(self.raw)
            self._pool = None
        elif self.raw is not None:
            self.raw.close()
        self.raw = None


class _PgPool:
    """A few reusable Postgres connections (Supabase's pooler limits how many we may hold)."""

    def __init__(self, dsn: str, schema: str, size: int = 4):
        self.dsn, self.schema, self.size = dsn, schema, size
        self._idle: list[tuple[Any, float]] = []
        self._lock = threading.Lock()

    def _new(self):
        extra = {}
        host = urllib.parse.urlsplit(self.dsn).hostname or ""
        if "sslmode=" not in self.dsn and host not in ("localhost", "127.0.0.1", "::1"):
            extra["sslmode"] = "require"  # Supabase: never fall back to plain text (libpq's default is "prefer")
        raw = psycopg.connect(
            self.dsn, autocommit=True, row_factory=_pg_row_factory, prepare_threshold=None, connect_timeout=10, **extra
        )
        raw.execute(pgsql.SQL("SET search_path TO {}").format(pgsql.Identifier(self.schema)))
        raw.execute("SET lock_timeout TO '10s'")  # same patience as SQLite's busy_timeout
        return raw

    def get(self):
        while True:
            with self._lock:
                if not self._idle:
                    break
                raw, since = self._idle.pop()
            if raw.closed:
                continue
            if time.monotonic() - since > 30:  # the pooler may have dropped an idle connection
                try:
                    raw.execute("SELECT 1")
                except psycopg.Error:
                    raw.close()
                    continue
            return raw
        return self._new()

    def release(self, raw) -> None:
        if raw.closed:
            return
        if raw.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            raw.close()  # never hand on a connection that is mid-transaction or broken
            return
        with self._lock:
            if len(self._idle) < self.size:
                self._idle.append((raw, time.monotonic()))
                return
        raw.close()

    def close_all(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, []
        for raw, _ in idle:
            raw.close()


_pools: dict[tuple[str, str], _PgPool] = {}
_pools_lock = threading.Lock()


def _pool(dsn: str, schema: str) -> _PgPool:
    with _pools_lock:
        if (dsn, schema) not in _pools:
            _pools[(dsn, schema)] = _PgPool(dsn, schema)
        return _pools[(dsn, schema)]


def close_pools() -> None:
    with _pools_lock:
        pools = list(_pools.values())
    for p in pools:
        p.close_all()


def connect(target: Path | str, schema: str = DEFAULT_SCHEMA) -> Conn:
    """`target` is a SQLite file path or a postgres:// URL."""
    if is_postgres(target):
        pool = _pool(str(target), schema)
        return Conn(pool.get(), "postgres", schema, pool)
    raw = sqlite3.connect(target, isolation_level=None, timeout=10, check_same_thread=False)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("PRAGMA busy_timeout = 10000")
    return Conn(raw, "sqlite")


def connect_settings(settings) -> Conn:
    return connect(settings.db_target, settings.db_schema)


# --------------------------------------------------------------------------
# Schema


def init_db(target: Path | str, schema: str = DEFAULT_SCHEMA) -> None:
    """SQLite: create the file and apply pending migrations. Postgres: check the schema is applied."""
    if is_postgres(target):
        _check_pg_schema(str(target), schema)
        return
    path = Path(target)
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


def pg_versions() -> list[str]:
    """Schema versions this code expects in Postgres (one per file in migrations/postgres/)."""
    return [f.stem.split("_", 1)[0] for f in sorted(PG_MIGRATIONS_DIR.glob("*.sql"))]


def _check_pg_schema(dsn: str, schema: str) -> None:
    conn = connect(dsn, schema)
    try:
        if conn.execute("SELECT to_regclass('schema_migrations') AS t").fetchone()["t"] is None:
            raise RuntimeError(
                f"Postgres schema '{schema}' has no PixelRelay tables. Apply the accurova_pixelrelay "
                "migration from thebooleanjulian-db first (see progress.md)."
            )
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        missing = [v for v in pg_versions() if v not in applied]
        if missing:
            raise RuntimeError(f"Postgres schema '{schema}' is missing migrations {missing}. Apply them first.")
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


# --------------------------------------------------------------------------
# Queries


def _write_lock_key(schema: str) -> int:
    return int.from_bytes(hashlib.sha256(f"pixelrelay-writes:{schema}".encode()).digest()[:8], "big", signed=True)


@contextmanager
def transaction(conn: Conn) -> Iterator[Conn]:
    """One writer at a time, rolling back on error. Not re-entrant."""
    if conn.dialect == "postgres":
        conn.raw.execute("BEGIN")
        try:
            conn.raw.execute("SELECT pg_advisory_xact_lock(%s)", (_write_lock_key(conn.schema),))
            yield conn
        except BaseException:
            conn.raw.execute("ROLLBACK")
            raise
        else:
            conn.raw.execute("COMMIT")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def one(conn: Conn, sql: str, params: tuple | list = ()) -> Row | None:
    return conn.execute(sql, params).fetchone()


def all_rows(conn: Conn, sql: str, params: tuple | list = ()) -> list[Row]:
    return conn.execute(sql, params).fetchall()


_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def insert(conn: Conn, table: str, values: dict[str, Any]) -> None:
    assert _IDENT.match(table) and all(_IDENT.match(k) for k in values)
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))


def update(conn: Conn, table: str, row_id: str, values: dict[str, Any]) -> None:
    assert _IDENT.match(table) and all(_IDENT.match(k) for k in values)
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), row_id))


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


def log_event(
    conn: Conn,
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
