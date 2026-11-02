"""Supervision thresholds: from the latest readings and the state of the queue, which alarms are on right now.

`evaluate` is pure (no database, no clock, no environment): the Serveur page of the back office and `/api/workerz`
both show its list, and the notification to come (e-mail, Slack) only has to read it. Nothing is sent from here.
An alert is `{code, severity, message}`: no organization, brand or user, so the list may leave the back office.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import psycopg

from nyra.config import QuotaConfig

from . import hostmetrics, schedule, usage
from . import jobs as cloud_jobs

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Thresholds:
    disk: float  # share of the machine's disk used
    worker_memory: float  # share of the worker's memory limit used
    queue_seconds: int  # a queued job waiting this long with no job running anywhere
    silent_seconds: int  # a worker whose last reading is older than this is taken for stopped
    plan_storage: int = 100 * 1000**3  # bytes of Storage the Supabase plan includes (AXE-533)
    plan_database: int = 8 * 1000**3  # bytes of database the Supabase plan includes
    usage_warn: float = 0.7  # share of the plan at which Storage and the database raise a warning ...
    usage_critical: float = 0.9  # ... and a critical alert
    pages_percent: int = 0  # organizations that read this share of their month's pages raise an alert; 0 (default): off until measured (AXE-708)
    read_late_days: int = 0  # an active schedule with no finished read for this many days raises an alert; 0 (default): off until measured (AXE-684)


# Name, default, lowest, highest: whole numbers, anything else is ignored with a warning (as NYRA_JOB_LIMIT_<KIND>_MIN).
# Disk 80 %: the Droplet's disk holds Docker layers, logs and the worker's image cache, and a full one fails every job.
# Memory 85 %: a crawl with CLIP was measured at 1.88 GiB of the 2.5 GiB limit (73 %), so 85 % (2.1 GiB) is above
# what a normal crawl needs, and the kernel kills the worker at 100 %. Queue 5 min: an idle worker claims a job within
# seconds (it polls every 2 s), as in the probe's former 300 s. Silent 300 s: a worker publishes every 15 s, but a deployment
# recreates the container (new host name, first reading 15 s or more after the start) and must not page the uptime monitor.
SETTINGS = {
    "disk": ("NYRA_ALERT_DISK_PERCENT", 80, 1, 100),
    "worker_memory": ("NYRA_ALERT_WORKER_MEMORY_PERCENT", 85, 1, 100),
    "queue_minutes": ("NYRA_ALERT_QUEUE_MINUTES", 5, 1, 24 * 60),
    "silent_seconds": ("NYRA_ALERT_WORKER_SILENT_SECONDS", 300, 30, 3600),
    # The Supabase Pro plan includes 100 GB of Storage and 8 GB of database (docs/COUTS_PRODUCTION.md): past that it is paid for. Warning at 70 %, critical at 90 %.
    "plan_storage_gb": ("NYRA_PLAN_STORAGE_GB", 100, 1, 100_000),
    "plan_database_gb": ("NYRA_PLAN_DATABASE_GB", 8, 1, 100_000),
    "usage_warn": ("NYRA_ALERT_SUPABASE_WARN_PERCENT", 70, 1, 100),
    "usage_critical": ("NYRA_ALERT_SUPABASE_CRITICAL_PERCENT", 90, 1, 100),
    # 0 = no « late read » alert. Off until someone has looked at how long the real reads take (docs/DEPLOYMENT.md, « Reads that do not happen »).
    "read_late_days": ("NYRA_ALERT_READ_LATE_DAYS", 0, 0, 365),
    # 0 = no alert. With two reads a month the pages of the month go twice as fast (docs/DEPLOYMENT.md, « Pages of the month »): off until the share is chosen from `nyra cloud-pages-quota`.
    "pages_percent": ("NYRA_ALERT_PAGES_PERCENT", 0, 0, 100),
}


def _setting(key: str) -> int:
    name, default, low, high = SETTINGS[key]
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    if raw.isdigit() and low <= int(raw) <= high:
        return int(raw)
    log.warning("%s=%r ignored: expected a whole number from %d to %d", name, raw, low, high)
    return default


def thresholds() -> Thresholds:
    return Thresholds(disk=_setting("disk") / 100, worker_memory=_setting("worker_memory") / 100,
                      queue_seconds=_setting("queue_minutes") * 60, silent_seconds=_setting("silent_seconds"),
                      plan_storage=_setting("plan_storage_gb") * 1000**3, plan_database=_setting("plan_database_gb") * 1000**3,
                      usage_warn=_setting("usage_warn") / 100, usage_critical=_setting("usage_critical") / 100,
                      read_late_days=_setting("read_late_days"), pages_percent=_setting("pages_percent"))


def _alert(code: str, severity: str, message: str, instance: Optional[str] = None) -> dict:
    """`instance` (the container's host name) tells which of several services it is about; the sentence never contains it,
    so the public probe can drop the field and keep the message (see `public`)."""
    return {"code": code, "severity": severity, "message": message} | ({"instance": instance} if instance else {})


# What the public probe has always said (a monitor may test these words): `status` is the first, `problems` lists them.
LEGACY_PROBLEMS = {"worker_absent": "no worker", "queue_stalled": "no worker", "disk_full": "disk almost full"}


def probe_problems(found: list[dict]) -> list[str]:
    """The critical alerts as the probe's historical labels, each once, in order."""
    return list(dict.fromkeys(LEGACY_PROBLEMS[a["code"]] for a in found if a["severity"] == "critical" and a["code"] in LEGACY_PROBLEMS))


# What is about the customers (how many brands are not read, how many organizations are near their pages) is for the Nyra team: the probe is public.
TEAM_ONLY = frozenset({"reads_unscheduled", "reads_late", "pages_quota"})


def public(found: list[dict]) -> list[dict]:
    """The alerts without the host name, and without those about the customers, for the unauthenticated probe."""
    return [{key: value for key, value in alert.items() if key != "instance"} for alert in found if alert["code"] not in TEAM_ONLY]


def _memory_ratio(reading: dict) -> Optional[float]:
    """The worker's memory over its cgroup limit, or over the machine's memory when it has no limit; None if unknown."""
    total = reading.get("memory_limit") or (reading.get("host") or {}).get("memory_total")
    used = reading.get("memory")
    return used / total if total and used is not None else None


def _plan_alert(code: str, what: str, used: Optional[int], plan: int, limits: Thresholds) -> list[dict]:
    """A warning from `usage_warn` of the plan, a critical alert from `usage_critical`; none when the size is unknown."""
    if used is None or used < limits.usage_warn * plan:
        return []
    severity = "critical" if used >= limits.usage_critical * plan else "warning"
    return [_alert(code, severity, f"{what} de Supabase est à {used / plan * 100:.0f} % de ce que le plan inclut ({used / 1000**3:.1f} Go sur {plan / 1000**3:.0f} Go) : au-delà, c'est payant.")]


def _reads_alerts(reads: Optional[dict], limits: Thresholds) -> list[dict]:
    """Brands that are not read by themselves (AXE-684). With nobody able to launch a read by hand but the Nyra team, a brand with no active schedule
    is never read and its expired images are never found: the team is told, with a number and no name."""
    found: list[dict] = []
    if reads and reads.get("unscheduled"):
        count = reads["unscheduled"]
        found.append(_alert("reads_unscheduled", "warning",
                            "1 marque avec un site n'a aucune lecture automatique active : ses images expirées ne seront pas détectées." if count == 1
                            else f"{count} marques avec un site n'ont aucune lecture automatique active : leurs images expirées ne seront pas détectées."))
    if reads and reads.get("late"):
        count = reads["late"]
        found.append(_alert("reads_late", "warning", f"{count} marque{'' if count == 1 else 's'} programmée{'' if count == 1 else 's'} sans lecture terminée depuis plus de {limits.read_late_days} jours."))
    return found


def _pages_alerts(near: Optional[int], limits: Thresholds) -> list[dict]:
    """Organizations close to their month's pages (AXE-708): a read that reaches the limit stops there, so the team is told before, with a number and no name."""
    if not near:
        return []
    who = "1 organisation a lu" if near == 1 else f"{near} organisations ont lu"
    return [_alert("pages_quota", "warning", f"{who} {limits.pages_percent} % ou plus des pages de leur mois : une lecture de plus risque de s'arrêter à la limite.")]


def evaluate(services: list[dict], stalled_seconds: float, disk: Optional[float], limits: Thresholds,
             supabase: Optional[dict] = None, reads: Optional[dict] = None, pages_near: Optional[int] = None) -> list[dict]:
    """`services`: the latest reading of each service (`hostmetrics.published`); `stalled_seconds`: `jobs.stalled_seconds`;
    `disk`: the used share of the disk, None if unreadable; `supabase`: `{storage, database}` in bytes (`supabase_usage`), None if unknown.
    Missing data never raises: no reading is « worker absent »."""
    found: list[dict] = []
    workers = [s for s in services if s.get("role") == "worker"]
    alive = [s for s in workers if s.get("age_seconds", limits.silent_seconds + 1) <= limits.silent_seconds]
    if workers and not alive:
        quiet = min(s.get("age_seconds", 0) for s in workers)
        found.append(_alert("worker_absent", "critical", f"Aucun worker n'a donné de nouvelles depuis {quiet / 60:.0f} min : les tâches ne seront pas traitées."))
    elif not workers:  # none ever published (or the rows were purged after a day): a dev or CI environment must not page the monitor
        found.append(_alert("worker_absent", "warning", "Aucun worker n'a publié de métriques (normal sans worker, par exemple en développement)."))
    for service in alive:
        ratio = _memory_ratio(service.get("reading") or {})
        if ratio is not None and ratio >= limits.worker_memory:
            found.append(_alert("worker_memory", "warning", f"La mémoire d'un worker est à {ratio * 100:.0f} % de sa limite : il risque d'être arrêté.", service.get("instance")))
    if stalled_seconds > limits.queue_seconds:
        found.append(_alert("queue_stalled", "critical", f"Une tâche attend depuis {stalled_seconds / 60:.0f} min alors qu'aucune ne tourne."))
    if disk is not None and disk >= limits.disk:
        found.append(_alert("disk_full", "critical", f"Le disque de la machine est plein à {disk * 100:.0f} %."))
    usage = supabase or {}
    found += _plan_alert("storage_plan", "Le stockage", usage.get("storage"), limits.plan_storage, limits)
    found += _plan_alert("database_plan", "La base", usage.get("database"), limits.plan_database, limits)
    return found + _reads_alerts(reads, limits) + _pages_alerts(pages_near, limits)


_USAGE_SECONDS = 600  # reading the size of Storage adds up every object: once in ten minutes at most, whoever asks
_usage: dict = {"at": float("-inf"), "value": None}


def supabase_usage(conn: psycopg.Connection) -> Optional[dict]:
    """What the project holds, in bytes: `{storage, database}`. None when it cannot be read (logged). Kept for ten minutes."""
    if time.monotonic() - _usage["at"] < _USAGE_SECONDS:
        return _usage["value"]
    try:
        row = conn.execute(
            """SELECT pg_database_size(current_database()) AS database,
                      (SELECT COALESCE(sum((metadata->>'size')::bigint), 0) FROM storage.objects) AS storage"""
        ).fetchone()
        _usage["value"] = {"storage": int(row["storage"]), "database": int(row["database"])}
    except psycopg.Error:
        log.exception("reading the size of Storage and of the database failed")
        conn.rollback()
        _usage["value"] = None
    _usage["at"] = time.monotonic()
    return _usage["value"]


def reads_coverage(conn: psycopg.Connection, limits: Thresholds) -> Optional[dict]:
    """`schedule.coverage` for the alerts; None when it cannot be read (the table is missing before its migration, logged)."""
    try:
        return schedule.coverage(conn, limits.read_late_days)
    except psycopg.Error:
        log.exception("counting the brands that are not read by themselves failed")
        conn.rollback()
        return None


def pages_near(conn: psycopg.Connection, limits: Thresholds, quota: Optional[QuotaConfig]) -> Optional[int]:
    """`usage.pages_near_limit` for the alerts; None when it is off (0 %), the default quota is not given, or it cannot be read (logged)."""
    if not limits.pages_percent or quota is None:
        return None
    try:
        return usage.pages_near_limit(conn, quota, limits.pages_percent)
    except psycopg.Error:
        log.exception("counting the organizations near their month's pages failed")
        conn.rollback()
        return None


def current(conn: psycopg.Connection, services: Optional[list[dict]] = None, quota: Optional[QuotaConfig] = None) -> dict:
    """What `/api/workerz` and the Serveur page show: `alerts`, plus the two figures behind them. `services` if already read."""
    stalled = cloud_jobs.stalled_seconds(conn)
    try:
        disk: Optional[float] = hostmetrics.disk_ratio()
    except OSError:
        log.exception("reading the disk failed")  # the probe must still answer about the worker
        disk = None
    limits = thresholds()
    found = evaluate(hostmetrics.published(conn) if services is None else services, stalled, disk, limits, supabase_usage(conn), reads_coverage(conn, limits), pages_near(conn, limits, quota))
    return {"alerts": found, "queued_wait_seconds": round(stalled), "disk_percent": None if disk is None else round(disk * 100)}
