"""Durable job queue on Postgres (table `jobs`).

The web process enqueues; `nyra worker` claims, runs and reports. Nothing
about a job lives only in memory, so a restart of either process loses
nothing, several web instances can run behind a load balancer, and a
worker that dies mid-job is noticed through its heartbeat.

Rules:
- every job belongs to one brand of an organization;
- one running job per brand (unique partial index), several brands (of
  the same organization or not) in parallel across workers;
- a crawl, a match or a report is refused while the same kind is already
  queued or running for the brand; an `index` request joins the one
  already queued instead;
- cancelling a queued job cancels it at once; a running one gets
  `cancel_requested`, which the worker checks between steps.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

import psycopg

KINDS = {"crawl", "match", "index", "report", "locate"}
ACTIVE = ("queued", "running")
STALE_AFTER_SECONDS = 180
# A job whose worker died goes back to the queue this many times before it is marked failed.
MAX_RETRIES = 2


class JobConflict(Exception):
    """A job of the same kind is already waiting or running."""


def _row(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    out = dict(row)
    for key in ("id", "org_id", "brand_id", "created_by"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    for key in ("created_at", "started_at", "finished_at", "heartbeat_at"):
        if out.get(key) is not None:
            out[key] = out[key].isoformat()
    return out


def enqueue(
    conn: psycopg.Connection, *, org_id: uuid.UUID, brand_id: uuid.UUID, kind: str,
    params: Optional[dict] = None, created_by: Optional[uuid.UUID] = None,
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown job kind {kind}")
    # Serialize enqueues per brand so two clicks can't both pass the check.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"jobs:{brand_id}",))
    if kind == "index":
        # Every upload asks for one: they all join the one still waiting, even
        # while another index job is running.
        waiting = conn.execute(
            "SELECT * FROM jobs WHERE brand_id = %s AND kind = 'index' AND status = 'queued' ORDER BY created_at LIMIT 1",
            (brand_id,),
        ).fetchone()
        if waiting is not None:
            return _row(waiting)
    elif conn.execute(
        "SELECT 1 FROM jobs WHERE brand_id = %s AND kind = %s AND status = ANY(%s) LIMIT 1",
        (brand_id, kind, list(ACTIVE)),
    ).fetchone():
        raise JobConflict(kind)
    row = conn.execute(
        """INSERT INTO jobs (org_id, brand_id, kind, params, created_by, message)
           VALUES (%s, %s, %s, %s, %s, 'En attente…') RETURNING *""",
        (org_id, brand_id, kind, json.dumps(params or {}), created_by),
    ).fetchone()
    return _row(row)


def claim(conn: psycopg.Connection) -> Optional[dict]:
    """Take the oldest queued job of a brand that has nothing running."""
    try:
        row = conn.execute(
            """
            UPDATE jobs SET status = 'running', started_at = now(), heartbeat_at = now(), message = 'Démarrage…'
            WHERE id = (
                SELECT j.id FROM jobs j
                WHERE j.status = 'queued'
                  AND NOT EXISTS (SELECT 1 FROM jobs r WHERE r.brand_id = j.brand_id AND r.status = 'running')
                ORDER BY (j.kind = 'locate'), j.created_at  -- a long search never waits ahead of another kind
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING *
            """
        ).fetchone()
    except psycopg.errors.UniqueViolation:
        # Another worker started a job for the same brand in the meantime.
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


def request_cancel(conn: psycopg.Connection, brand_id: uuid.UUID, job_id: uuid.UUID) -> Optional[dict]:
    row = conn.execute(
        """UPDATE jobs SET
               status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
               finished_at = CASE WHEN status = 'queued' THEN now() ELSE finished_at END,
               message = CASE WHEN status = 'queued' THEN 'Annulé avant le démarrage.' ELSE 'Arrêt demandé…' END,
               cancel_requested = true
           WHERE id = %s AND brand_id = %s AND status = ANY(%s)
           RETURNING *""",
        (job_id, brand_id, list(ACTIVE)),
    ).fetchone()
    return _row(row)


def reap_stale(conn: psycopg.Connection, older_than_seconds: int = STALE_AFTER_SECONDS) -> tuple[int, int]:
    """Recover running jobs whose worker stopped reporting: (put back in the queue, marked failed).

    A job goes back to the queue (with `retries` counted in its params, and a crawl resuming where it was)
    up to `MAX_RETRIES` times, unless a stop was asked; past that it fails, so a job that kills its worker
    every time cannot loop. Their crawl runs, dead either way, are closed.
    """
    stale = "status = 'running' AND heartbeat_at < now() - make_interval(secs => %s)"
    retries = "COALESCE((params ->> 'retries')::int, 0)"
    requeued = conn.execute(
        f"""UPDATE jobs SET status = 'queued', started_at = NULL, heartbeat_at = NULL, progress = '{{}}'::jsonb,
               message = 'Relancé automatiquement : le worker s''est arrêté pendant la tâche.',
               params = jsonb_set(params - 'fresh', '{{retries}}', to_jsonb({retries} + 1))
           WHERE {stale} AND NOT cancel_requested AND {retries} < %s
           RETURNING id""",
        (older_than_seconds, MAX_RETRIES),
    ).fetchall()
    failed = conn.execute(
        f"""UPDATE jobs SET status = 'error', finished_at = now(),
               message = 'Interrompu : le worker s''est arrêté pendant la tâche.',
               error = 'heartbeat lost'
           WHERE {stale}
           RETURNING id""",
        (older_than_seconds,),
    ).fetchall()
    ids = [row["id"] for row in (*requeued, *failed)]
    if ids:
        conn.execute(
            "UPDATE crawl_runs SET status = 'error', finished_at = now() WHERE job_id = ANY(%s) AND status = 'running'",
            (ids,),
        )
    return len(requeued), len(failed)


def stalled_seconds(conn: psycopg.Connection) -> float:
    """How long the oldest queued job has waited while nothing runs, else 0.

    An idle worker claims a queued job within seconds, so a job that waits with no job running anywhere
    means no worker is alive (a busy worker shows as a running job)."""
    row = conn.execute(
        """SELECT COALESCE(EXTRACT(epoch FROM now() - min(created_at) FILTER (WHERE status = 'queued')), 0) AS waited,
                  COALESCE(bool_or(status = 'running'), false) AS running
           FROM jobs WHERE status = ANY(%s)""",
        (list(ACTIVE),),
    ).fetchone()
    return 0.0 if row["running"] else float(row["waited"])


def get(conn: psycopg.Connection, brand_id: uuid.UUID, job_id: uuid.UUID) -> Optional[dict]:
    return _row(conn.execute("SELECT * FROM jobs WHERE id = %s AND brand_id = %s", (job_id, brand_id)).fetchone())


def current(conn: psycopg.Connection, brand_id: uuid.UUID) -> dict[str, Any]:
    """Active jobs (running first) and the most recently finished one."""
    active = conn.execute(
        """SELECT * FROM jobs WHERE brand_id = %s AND status = ANY(%s)
           ORDER BY (status = 'running') DESC, created_at""",
        (brand_id, list(ACTIVE)),
    ).fetchall()
    last = conn.execute(
        """SELECT * FROM jobs WHERE brand_id = %s AND status <> ALL(%s)
           ORDER BY finished_at DESC NULLS LAST LIMIT 1""",
        (brand_id, list(ACTIVE)),
    ).fetchone()
    return {"active": [_row(row) for row in active], "last": _row(last)}


def recent(conn: psycopg.Connection, brand_id: uuid.UUID, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE brand_id = %s ORDER BY created_at DESC LIMIT %s", (brand_id, limit)
    ).fetchall()
    return [_row(row) for row in rows]
