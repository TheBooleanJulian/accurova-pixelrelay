"""Room PIN brute-force protection: progressive delays, never a permanent lockout.

Three counters, all over sliding windows so they decay on their own:

* per (project, IP): after FREE_TRIES failures in WINDOW, each further try
  must wait 2^(n - FREE_TRIES) seconds after the last failure, capped at
  MAX_DELAY. A correct PIN resets nothing early; the window simply expires.
* per IP across all projects: the same curve, so one client cannot spread
  guesses over many rooms.
* per project across all IPs: above PROJECT_ALERT failures an hour the room is
  flagged as suspicious (the owner is shown a "rotate the PIN" prompt) and
  every attempt is spaced PROJECT_SPACING seconds apart. Legitimate people
  are slowed by seconds, never locked out.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import db

WINDOW = 15 * 60
FREE_TRIES = 5
MAX_DELAY = 300
IP_GLOBAL_FREE = 20
PROJECT_WINDOW = 60 * 60
PROJECT_ALERT = 60
PROJECT_SPACING = 3


@dataclass
class Decision:
    allowed: bool
    retry_after: int = 0
    suspicious: bool = False


def _delay(failures: int, free: int) -> int:
    if failures < free:
        return 0
    return min(MAX_DELAY, 2 ** (failures - free + 1))


def check(conn: db.Conn, project_id: str, ip_hash: str, now: float | None = None) -> Decision:
    now = time.time() if now is None else now

    row = db.one(
        conn,
        "SELECT COUNT(*) AS n, MAX(at) AS last FROM pin_attempts"
        " WHERE project_id = ? AND ip_hash = ? AND success = 0 AND at > ?",
        (project_id, ip_hash, now - WINDOW),
    )
    wait = 0.0
    if row["n"]:
        wait = max(wait, row["last"] + _delay(row["n"], FREE_TRIES) - now)

    row = db.one(
        conn,
        "SELECT COUNT(*) AS n, MAX(at) AS last FROM pin_attempts WHERE ip_hash = ? AND success = 0 AND at > ?",
        (ip_hash, now - WINDOW),
    )
    if row["n"]:
        wait = max(wait, row["last"] + _delay(row["n"], IP_GLOBAL_FREE) - now)

    row = db.one(
        conn,
        "SELECT COUNT(*) AS n, MAX(at) AS last FROM pin_attempts WHERE project_id = ? AND success = 0 AND at > ?",
        (project_id, now - PROJECT_WINDOW),
    )
    suspicious = row["n"] >= PROJECT_ALERT
    if suspicious:
        wait = max(wait, row["last"] + PROJECT_SPACING - now)

    if wait > 0:
        return Decision(False, int(wait) + 1, suspicious)
    return Decision(True, 0, suspicious)


def record(conn: db.Conn, project_id: str, ip_hash: str, success: bool, now: float | None = None) -> None:
    now = time.time() if now is None else now
    conn.execute(
        "INSERT INTO pin_attempts (project_id, ip_hash, success, at) VALUES (?, ?, ?, ?)",
        (project_id, ip_hash, 1 if success else 0, now),
    )


def prune(conn: db.Conn, now: float | None = None) -> None:
    now = time.time() if now is None else now
    conn.execute("DELETE FROM pin_attempts WHERE at < ?", (now - 2 * PROJECT_WINDOW,))
