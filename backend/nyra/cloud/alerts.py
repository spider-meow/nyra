"""Supervision thresholds: from the latest readings and the state of the queue, which alarms are on right now.

`evaluate` is pure (no database, no clock, no environment): the Serveur page of the back office and `/api/workerz`
both show its list, and the notification to come (e-mail, Slack) only has to read it. Nothing is sent from here.
An alert is `{code, severity, message}`: no organization, brand or user, so the list may leave the back office.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import psycopg

from . import hostmetrics
from . import jobs as cloud_jobs

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Thresholds:
    disk: float  # share of the machine's disk used
    worker_memory: float  # share of the worker's memory limit used
    queue_seconds: int  # a queued job waiting this long with no job running anywhere
    silent_seconds: int  # a worker whose last reading is older than this is taken for stopped


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
                      queue_seconds=_setting("queue_minutes") * 60, silent_seconds=_setting("silent_seconds"))


def _alert(code: str, severity: str, message: str, instance: Optional[str] = None) -> dict:
    """`instance` (the container's host name) tells which of several services it is about; the sentence never contains it,
    so the public probe can drop the field and keep the message (see `public`)."""
    return {"code": code, "severity": severity, "message": message} | ({"instance": instance} if instance else {})


# What the public probe has always said (a monitor may test these words): `status` is the first, `problems` lists them.
LEGACY_PROBLEMS = {"worker_absent": "no worker", "queue_stalled": "no worker", "disk_full": "disk almost full"}


def probe_problems(found: list[dict]) -> list[str]:
    """The critical alerts as the probe's historical labels, each once, in order."""
    return list(dict.fromkeys(LEGACY_PROBLEMS[a["code"]] for a in found if a["severity"] == "critical"))


def public(found: list[dict]) -> list[dict]:
    """The alerts without the host name, for the unauthenticated probe."""
    return [{key: value for key, value in alert.items() if key != "instance"} for alert in found]


def _memory_ratio(reading: dict) -> Optional[float]:
    """The worker's memory over its cgroup limit, or over the machine's memory when it has no limit; None if unknown."""
    total = reading.get("memory_limit") or (reading.get("host") or {}).get("memory_total")
    used = reading.get("memory")
    return used / total if total and used is not None else None


def evaluate(services: list[dict], stalled_seconds: float, disk: Optional[float], limits: Thresholds) -> list[dict]:
    """`services`: the latest reading of each service (`hostmetrics.published`); `stalled_seconds`: `jobs.stalled_seconds`;
    `disk`: the used share of the disk, None if unreadable. Missing data never raises: no reading is « worker absent »."""
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
    return found


def current(conn: psycopg.Connection, services: Optional[list[dict]] = None) -> dict:
    """What `/api/workerz` and the Serveur page show: `alerts`, plus the two figures behind them. `services` if already read."""
    stalled = cloud_jobs.stalled_seconds(conn)
    try:
        disk: Optional[float] = hostmetrics.disk_ratio()
    except OSError:
        log.exception("reading the disk failed")  # the probe must still answer about the worker
        disk = None
    found = evaluate(hostmetrics.published(conn) if services is None else services, stalled, disk, thresholds())
    return {"alerts": found, "queued_wait_seconds": round(stalled), "disk_percent": None if disk is None else round(disk * 100)}
