"""What a container uses, read the way `docker stats` does: the cgroup's own counters (CPU time, memory), plus
the machine's load, memory and disk.

The web process is read live; each worker publishes its reading every `PUBLISH_SECONDS` into `service_metrics`
(a worker busy in a crawl cannot be asked), and the back office lists both. Nothing needs the Docker socket.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

import psycopg

PUBLISH_SECONDS = 15
CGROUP = Path("/sys/fs/cgroup")
# A published reading older than this is dropped (a worker that was scaled down or replaced leaves its row).
KEEP_HOURS = 24


# The disk is full enough to worry at this share (the Droplet's disk holds Docker layers, logs and the worker's image cache).
DISK_ALERT = 0.8


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


def _cpu_limit() -> Optional[float]:
    """Cores the container may use (`cpus:` in compose), or None for no limit."""
    quota, _, period = (_read(CGROUP / "cpu.max") or "max").partition(" ")
    return int(quota) / int(period) if quota.isdigit() and period.strip().isdigit() else None


def _host() -> dict:
    meminfo = _kv(_read(Path("/proc/meminfo")) or "")
    disk = shutil.disk_usage("/")
    return {
        "cpus": os.cpu_count() or 1,
        "load": [round(value, 2) for value in os.getloadavg()],
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


def published(conn: psycopg.Connection) -> list[dict]:
    """The latest reading of each service that published, with its age in seconds."""
    return conn.execute(
        """SELECT instance, role, reading, EXTRACT(EPOCH FROM now() - sampled_at)::float AS age_seconds
           FROM service_metrics ORDER BY role, instance"""
    ).fetchall()
