"""The server readings: cgroup and /proc parsing, and the CPU share between two readings."""

from __future__ import annotations

from nyra.cloud import hostmetrics


def test_kv_reads_cgroup_and_meminfo_lines():
    assert hostmetrics._kv("usage_usec 2500000\nuser_usec 1\nnr_periods x") == {"usage_usec": 2500000, "user_usec": 1}
    assert hostmetrics._kv("MemTotal:       16384 kB\nMemAvailable:    8192 kB") == {"MemTotal": 16384 * 1024, "MemAvailable": 8192 * 1024}


def test_cpu_percent_is_a_share_of_one_core(monkeypatch):
    clock, cpu = iter([100.0, 102.0]), iter([10.0, 13.0])  # 3 CPU seconds in 2 seconds: 150 %
    monkeypatch.setattr(hostmetrics.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(hostmetrics, "_cgroup_cpu_seconds", lambda: next(cpu))
    sampler = hostmetrics.Sampler()
    assert sampler.read()["cpu_percent"] is None
    reading = sampler.read()
    assert reading["cpu_percent"] == 150.0
    assert reading["memory"] > 0 and reading["host"]["cpus"] >= 1
