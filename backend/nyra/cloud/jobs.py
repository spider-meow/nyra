"""Durable job queue on Postgres (table `jobs`).

The web process enqueues; `nyra worker` claims, runs and reports. Nothing
about a job lives only in memory, so a restart of either process loses
nothing, several web instances can run behind a load balancer, and a
worker that dies mid-job is noticed through its heartbeat.

Rules:
- one running job per organization (unique partial index), several
  organizations in parallel across workers;
- a crawl, a match or a report is refused while the same kind is already
  queued or running for the organization; an `index` request joins the
  one already queued instead;
- cancelling a queued job cancels it at once; a running one gets
  `cancel_requested`, which the worker checks between steps.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

import psycopg

KINDS = {"crawl", "match", "index", "report"}
ACTIVE = ("queued", "running")
STALE_AFTER_SECONDS = 180


class JobConflict(Exception):
    """A job of the same kind is already waiting or running."""


def _row(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    out = dict(row)
    for key in ("id", "org_id", "created_by"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    for key in ("created_at", "started_at", "finished_at", "heartbeat_at"):
        if out.get(key) is not None:
            out[key] = out[key].isoformat()
    return out


def enqueue(
    conn: psycopg.Connection, *, org_id: uuid.UUID, kind: str, params: Optional[dict] = None,
    created_by: Optional[uuid.UUID] = None,
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind}")
    # Serialize enqueues per organization so two clicks can't both pass the check.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"jobs:{org_id}",))
    existing = conn.execute(
        "SELECT * FROM jobs WHERE org_id = %s AND kind = %s AND status = ANY(%s) ORDER BY created_at LIMIT 1",
        (org_id, kind, list(ACTIVE)),
    ).fetchone()
    if existing is not None:
        if kind == "index" and existing["status"] == "queued":
            return _row(existing)
        if kind != "index":
            raise JobConflict(kind)
    row = conn.execute(
        """INSERT INTO jobs (org_id, kind, params, created_by, message)
           VALUES (%s, %s, %s, %s, 'En attente…') RETURNING *""",
        (org_id, kind, json.dumps(params or {}), created_by),
    ).fetchone()
    return _row(row)


def claim(conn: psycopg.Connection) -> Optional[dict]:
    """Take the oldest queued job of an organization that has nothing running."""
    try:
        row = conn.execute(
            """
            UPDATE jobs SET status = 'running', started_at = now(), heartbeat_at = now(), message = 'Démarrage…'
            WHERE id = (
                SELECT j.id FROM jobs j
                WHERE j.status = 'queued'
                  AND NOT EXISTS (SELECT 1 FROM jobs r WHERE r.org_id = j.org_id AND r.status = 'running')
                ORDER BY j.created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING *
            """
        ).fetchone()
    except psycopg.errors.UniqueViolation:
        # Another worker started a job for the same organization in the meantime.
        conn.rollback()
        return None
    return _row(row)


def heartbeat(conn: psycopg.Connection, job_id: str, *, message: Optional[str] = None,
              progress: Optional[dict] = None) -> bool:
    """Record liveness (and progress). Returns True when cancellation was requested."""
    row = conn.execute(
        """UPDATE jobs SET heartbeat_at = now(),
               message = COALESCE(%s, message),
               progress = COALESCE(%s::jsonb, progress)
           WHERE id = %s RETURNING cancel_requested""",
        (message, json.dumps(progress) if progress is not None else None, job_id),
    ).fetchone()
    return bool(row and row["cancel_requested"])


def finish(conn: psycopg.Connection, job_id: str, *, status: str, message: str,
           result: Optional[dict] = None, error: Optional[str] = None) -> None:
    conn.execute(
        """UPDATE jobs SET status = %s, message = %s, result = %s, error = %s,
               finished_at = now(), heartbeat_at = now()
           WHERE id = %s""",
        (status, message, json.dumps(result, default=str) if result is not None else None, error, job_id),
    )


def request_cancel(conn: psycopg.Connection, org_id: uuid.UUID, job_id: uuid.UUID) -> Optional[dict]:
    row = conn.execute(
        """UPDATE jobs SET
               status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
               finished_at = CASE WHEN status = 'queued' THEN now() ELSE finished_at END,
               message = CASE WHEN status = 'queued' THEN 'Annulé avant le démarrage.' ELSE 'Arrêt demandé…' END,
               cancel_requested = true
           WHERE id = %s AND org_id = %s AND status = ANY(%s)
           RETURNING *""",
        (job_id, org_id, list(ACTIVE)),
    ).fetchone()
    return _row(row)


def reap_stale(conn: psycopg.Connection, older_than_seconds: int = STALE_AFTER_SECONDS) -> int:
    """Mark running jobs whose worker stopped reporting as failed, and their crawl runs too."""
    rows = conn.execute(
        """UPDATE jobs SET status = 'error', finished_at = now(),
               message = 'Interrompu : le worker s''est arrêté pendant la tâche.',
               error = 'heartbeat lost'
           WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => %s)
           RETURNING id""",
        (older_than_seconds,),
    ).fetchall()
    ids = [row["id"] for row in rows]
    if ids:
        conn.execute(
            "UPDATE crawl_runs SET status = 'error', finished_at = now() WHERE job_id = ANY(%s) AND status = 'running'",
            (ids,),
        )
    return len(ids)


def get(conn: psycopg.Connection, org_id: uuid.UUID, job_id: uuid.UUID) -> Optional[dict]:
    return _row(conn.execute("SELECT * FROM jobs WHERE id = %s AND org_id = %s", (job_id, org_id)).fetchone())


def current(conn: psycopg.Connection, org_id: uuid.UUID) -> dict[str, Any]:
    """Active jobs (running first) and the most recently finished one."""
    active = conn.execute(
        """SELECT * FROM jobs WHERE org_id = %s AND status = ANY(%s)
           ORDER BY (status = 'running') DESC, created_at""",
        (org_id, list(ACTIVE)),
    ).fetchall()
    last = conn.execute(
        """SELECT * FROM jobs WHERE org_id = %s AND status <> ALL(%s)
           ORDER BY finished_at DESC NULLS LAST LIMIT 1""",
        (org_id, list(ACTIVE)),
    ).fetchone()
    return {"active": [_row(row) for row in active], "last": _row(last)}


def recent(conn: psycopg.Connection, org_id: uuid.UUID, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE org_id = %s ORDER BY created_at DESC LIMIT %s", (org_id, limit)
    ).fetchall()
    return [_row(row) for row in rows]
