"""What a container uses, read the way `docker stats` does: the cgroup's own counters (CPU time, memory), plus
the machine's load, memory and disk.

The web process is read live; each worker publishes its reading every `PUBLISH_SECONDS` into `service_metrics`
(a worker busy in a crawl cannot be asked), and the back office lists both. Nothing needs the Docker socket.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Optional

import psycopg

log = logging.getLogger(__name__)

PUBLISH_SECONDS = 15
CGROUP = Path("/sys/fs/cgroup")
# A published reading older than this is dropped (a worker that was scaled down or replaced leaves its row).
KEEP_HOURS = 24
# The history (`service_metrics_history`, the Serveur page's curves): one row per container and minute, kept this long.
HISTORY_SECONDS = 60
HISTORY_DAYS = 30
# What the curves can show: (span, step) in seconds. The step grows with the span, so a series never has more than ~200 points.
RANGES = {"1h": (3600, 60), "24h": (86400, 600), "7d": (7 * 86400, 3600), "30d": (30 * 86400, 4 * 3600)}



def disk_ratio() -> float:
    usage = shutil.disk_usage("/")
    return usage.used / usage.total


def _kv(text: str) -> dict[str, int]:
    """`key value` lines (cpu.stat, memory.stat, /proc/meminfo's `key: value kB`) as numbers; other lines are skipped."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1]) * (1024 if len(parts) > 2 and parts[2] == "kB" else 1)
    return out


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text()
    except OSError:  # no cgroup v2 here (a laptop, macOS): the caller falls back or leaves the figure out
        return None


def _cgroup_cpu_seconds() -> float:
    """CPU time used by the container so far; the process's own when there is no cgroup."""
    usage = _kv(_read(CGROUP / "cpu.stat") or "").get("usage_usec")
    return usage / 1e6 if usage is not None else time.process_time()


def _cgroup_memory() -> tuple[int, Optional[int]]:
    """(bytes in use, limit or None). Like `docker stats`, the cache the kernel can drop is not counted."""
    current = _read(CGROUP / "memory.current")
    if current is None:
        rss = _kv(_read(Path("/proc/self/status")) or "").get("VmRSS", 0)
        return rss, None
    cache = _kv(_read(CGROUP / "memory.stat") or "").get("inactive_file", 0)
    limit = (_read(CGROUP / "memory.max") or "max").strip()
    return max(int(current) - cache, 0), int(limit) if limit.isdigit() else None


def memory_snapshot() -> dict[str, int]:
    """What a stopped worker's last heartbeat must still say (AXE-644): `mem` in use (as `docker stats`), `mem_limit`, `mem_peak`
    (the kernel's `memory.peak`, which counts the page cache: an upper bound) and `oom_kill` (processes the kernel killed in this
    container, Chromium included, since it started). A figure the machine does not give (no cgroup v2) is left out."""
    memory, limit = _cgroup_memory()
    out = {"mem": memory}
    if limit is not None:
        out["mem_limit"] = limit
    peak = (_read(CGROUP / "memory.peak") or "").strip()
    if peak.isdigit():
        out["mem_peak"] = int(peak)
    out["oom_kill"] = _kv(_read(CGROUP / "memory.events") or "").get("oom_kill", 0)
    return out


def _cpu_limit() -> Optional[float]:
    """Cores the container may use (`cpus:` in compose), or None for no limit."""
    quota, _, period = (_read(CGROUP / "cpu.max") or "max").partition(" ")
    return int(quota) / int(period) if quota.isdigit() and period.strip().isdigit() else None


def _host() -> dict:
    meminfo = _kv(_read(Path("/proc/meminfo")) or "")
    disk = shutil.disk_usage("/")
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)  # Windows (a dev machine) has no load average
    return {
        "cpus": os.cpu_count() or 1,
        "load": [round(value, 2) for value in load],
        "memory_total": meminfo.get("MemTotal"),
        "memory_available": meminfo.get("MemAvailable"),
        "disk_total": disk.total,
        "disk_used": disk.used,
    }


class Sampler:
    """Takes readings; the CPU share needs two of them (CPU seconds used over the seconds between them, so 100 %
    is one core, as in `docker stats`), hence the first reading has no `cpu_percent`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: Optional[tuple[float, float]] = None  # (monotonic time, CPU seconds): one reading, never grows

    def read(self) -> dict:
        now, cpu = time.monotonic(), _cgroup_cpu_seconds()
        with self._lock:
            last, self._last = self._last, (now, cpu)
        percent = round((cpu - last[1]) / (now - last[0]) * 100, 1) if last and now > last[0] else None
        memory, memory_limit = _cgroup_memory()
        return {"cpu_percent": percent, "cpu_limit": _cpu_limit(), "memory": memory, "memory_limit": memory_limit, "host": _host()}


def publish(conn: psycopg.Connection, instance: str, role: str, reading: dict) -> None:
    conn.execute(
        """INSERT INTO service_metrics (instance, role, sampled_at, reading) VALUES (%s, %s, now(), %s::jsonb)
           ON CONFLICT (instance) DO UPDATE SET role = excluded.role, sampled_at = now(), reading = excluded.reading""",
        (instance, role, json.dumps(reading)),
    )
    conn.execute("DELETE FROM service_metrics WHERE sampled_at < now() - make_interval(hours => %s)", (KEEP_HOURS,))
    record_history(conn, instance, role, reading)


def record_history(conn: psycopg.Connection, instance: str, role: str, reading: dict) -> None:
    """Keep this reading in the history unless the container already has one for this minute (the worker calls it every 15 s)."""
    host = reading["host"]
    conn.execute(
        """INSERT INTO service_metrics_history
               (instance, role, sampled_at, cpu_percent, cpu_limit, memory, memory_limit, load1, disk_ratio)
           VALUES (%s, %s, date_trunc('minute', now()), %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
        (instance, role, reading["cpu_percent"], reading["cpu_limit"], reading["memory"], reading["memory_limit"],
         host["load"][0], host["disk_used"] / host["disk_total"]),
    )
    conn.execute("DELETE FROM service_metrics_history WHERE sampled_at < now() - make_interval(days => %s)", (HISTORY_DAYS,))


def record_web(database_url: str, stop: threading.Event) -> None:
    """The web process has no `service_metrics` row (the page reads it live): it only writes its history, every minute."""
    from . import db as cloud_db  # imported here: `db` is not needed by the readings themselves

    sampler, instance = Sampler(), socket.gethostname()
    sampler.read()  # the CPU share needs a previous reading: the first history row has one
    while not stop.wait(HISTORY_SECONDS):
        try:
            with cloud_db.connect(database_url) as conn:
                record_history(conn, instance, "web", sampler.read())
        except Exception:  # noqa: BLE001 - the curves are a convenience; a missed point must not stop the web process
            log.exception("recording the web history failed")


def published(conn: psycopg.Connection) -> list[dict]:
    """The latest reading of each service that published, with its age in seconds."""
    return conn.execute(
        """SELECT instance, role, reading, EXTRACT(EPOCH FROM now() - sampled_at)::float AS age_seconds
           FROM service_metrics ORDER BY role, instance"""
    ).fetchall()


def history(conn: psycopg.Connection, span: int, step: int) -> dict:
    """The last `span` seconds in steps of `step`: per container the mean and peak CPU and memory of each step (`t` in epoch
    seconds), and the machine's disk and load. A step where a container did not write has no point: a gap stays a gap."""
    params = {"span": span, "step": step}
    containers = conn.execute(
        """SELECT instance, role, EXTRACT(EPOCH FROM date_bin(make_interval(secs => %(step)s), sampled_at, 'epoch'))::float AS t,
                  avg(cpu_percent)::float AS cpu, max(cpu_percent)::float AS cpu_max, avg(memory)::float AS memory,
                  max(memory)::float AS memory_max, max(memory_limit)::float AS memory_limit, max(cpu_limit)::float AS cpu_limit
           FROM service_metrics_history WHERE sampled_at >= now() - make_interval(secs => %(span)s)
           GROUP BY instance, role, t ORDER BY t""",
        params,
    ).fetchall()
    host = conn.execute(
        """SELECT EXTRACT(EPOCH FROM date_bin(make_interval(secs => %(step)s), sampled_at, 'epoch'))::float AS t,
                  avg(disk_ratio)::float AS disk, avg(load1)::float AS load
           FROM service_metrics_history WHERE sampled_at >= now() - make_interval(secs => %(span)s)
           GROUP BY t ORDER BY t""",
        params,
    ).fetchall()
    return {"containers": containers, "host": host}
