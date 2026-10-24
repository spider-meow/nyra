"""The audit log: who did what in an organization.

One row per action of a person that changes something (see `supabase/migrations/..._audit_log.sql`). Jobs and
`crawl_runs` keep the history of what the robot did; this keeps the history of what people did.

**How it is written.** Every route that changes something calls `record` itself, with the same connection as the
change and before it commits: the action and its trace are one transaction. If the trace cannot be written the
action is rolled back and the person sees an error, never a silent gap. (Uploads and adoptions write one row per file, in the transaction of that file's reference: if the row fails, that
file is reported as refused to the person; the file already sent to Storage stays as a harmless orphan.) Explicit
calls rather than a middleware on POST/PUT/DELETE: a middleware runs after the response is built, so it cannot
cancel the action when the trace fails, and it only knows a URL, not that "this image was ignored". The price is
that a new route can forget the call: `test_audit.py` lists every changing route and fails for one that does.

**What it holds.** Identifiers and counters, never content: no request body, no file name, no address, no note.
`record` refuses anything else (a long text, a list, a dict), so a careless call fails in the tests.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

from psycopg.types.json import Jsonb

from . import db as cloud_db

RETENTION_MONTHS = 12
PURGE_BATCH = 1000
PURGE_MAX_BATCHES = 100  # 100,000 rows per run; the next run (the worker runs it daily) takes the rest
PURGE_LOCK = 531_001  # advisory lock: one purge at a time, whatever the number of workers
MAX_DETAIL_TEXT = 80  # a code or an identifier, not a sentence
MAX_PAGE = 200
MAX_OFFSET = 50_000
MAX_OBJECT_ID = 80
_CODE = re.compile(r"[a-z][a-z_.]*")  # action and object type: stable codes, not text


def _plain(key: str, value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)) or (isinstance(value, str) and len(value) <= MAX_DETAIL_TEXT):
        return value
    raise ValueError(f"audit detail {key!r}: only numbers, flags and short codes are kept, never content")


def record(conn, org_id: Optional[uuid.UUID], user_id: Optional[uuid.UUID], action: str, object_type: str,
           object_id: Any = None, *, brand_id: Optional[uuid.UUID] = None, **detail: Any) -> None:
    """Write one row on `conn`, in the transaction of the change it traces. `action` is a stable short code
    (`library.delete`, `exclusion.add`…); `object_id` the identifier of what it touched, `detail` counters and codes.
    `org_id` is None only for what belongs to no organization (the Nyra team's own list of platform admins)."""
    if not (_CODE.fullmatch(action) and _CODE.fullmatch(object_type)):
        raise ValueError(f"audit action/object type must be codes like 'library.delete': {action!r}, {object_type!r}")
    if object_id is not None and len(str(object_id)) > MAX_OBJECT_ID:
        raise ValueError("audit object_id is an identifier, not content")
    clean = {key: _plain(key, value) for key, value in detail.items()}
    written = conn.execute(
        """INSERT INTO audit_log (org_id, brand_id, user_id, action, object_type, object_id, detail)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (org_id, brand_id, user_id, action, object_type, None if object_id is None else str(object_id), Jsonb(clean)),
    ).rowcount
    if written != 1:
        raise RuntimeError(f"audit row for {action} not written")


def events(conn, org_id: uuid.UUID, *, limit: int = 50, offset: int = 0, brand_id: Optional[uuid.UUID] = None,
           action: Optional[str] = None, user_id: Optional[uuid.UUID] = None, since: Optional[datetime] = None,
           until: Optional[datetime] = None) -> list[dict]:
    """The newest rows of an organization first, with the author's e-mail and the brand's name as they are now.
    `limit` and `offset` are bounded here as well as in the route."""
    limit, offset = min(max(limit, 1), MAX_PAGE + 1), min(max(offset, 0), MAX_OFFSET)  # the route asks one more to know there is a next page
    where, args = ["a.org_id = %s"], [org_id]
    for clause, value in (("a.brand_id = %s", brand_id), ("a.action = %s", action), ("a.user_id = %s", user_id),
                          ("a.created_at >= %s", since), ("a.created_at < %s", until)):
        if value is not None:
            where.append(clause)
            args.append(value)
    return conn.execute(
        f"""SELECT a.id, a.created_at, a.action, a.object_type, a.object_id, a.brand_id, a.user_id, a.detail,
                   u.email, b.name AS brand_name
            FROM audit_log a
            LEFT JOIN auth.users u ON u.id = a.user_id
            LEFT JOIN brands b ON b.id = a.brand_id
            WHERE {' AND '.join(where)}
            ORDER BY a.created_at DESC, a.id DESC LIMIT %s OFFSET %s""",
        (*args, limit, offset),
    ).fetchall()


def purge(database_url: str, months: int = RETENTION_MONTHS, *, between: Optional[Callable[[], None]] = None) -> int:
    """Delete the rows older than `months`, by batches of `PURGE_BATCH` (committed one by one, at most
    `PURGE_MAX_BATCHES`), and return how many went. One purge at a time (advisory lock; 0 if another runs);
    `between` is called after each batch (the worker shows it is alive)."""
    total = 0
    with cloud_db.connect(database_url) as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (PURGE_LOCK,)).fetchone()["ok"]:
            return 0
        try:
            for _ in range(PURGE_MAX_BATCHES):
                removed = conn.execute(
                    """DELETE FROM audit_log WHERE id IN (
                           SELECT id FROM audit_log WHERE created_at < now() - make_interval(months => %s)
                           ORDER BY created_at LIMIT %s)""",
                    (months, PURGE_BATCH),
                ).rowcount
                conn.commit()
                total += removed
                if between:
                    between()
                if removed < PURGE_BATCH:
                    break
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (PURGE_LOCK,))
    return total
