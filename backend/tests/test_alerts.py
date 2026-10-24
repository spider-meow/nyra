"""The supervision thresholds: what is judged from the latest readings, with no database and no server."""

from __future__ import annotations

import logging

import pytest

from nyra.cloud import alerts

LIMITS = alerts.Thresholds(disk=0.8, worker_memory=0.85, queue_seconds=300, silent_seconds=300)
GIB = 1024**3


def worker(memory=1 * GIB, limit=2560 * 1024**2, age=5.0, instance="w1", host_total=16 * GIB):
    return {"instance": instance, "role": "worker", "age_seconds": age,
            "reading": {"memory": memory, "memory_limit": limit, "host": {"memory_total": host_total}}}


def codes(**kwargs):
    args = {"services": [worker()], "stalled_seconds": 0.0, "disk": 0.1, "limits": LIMITS} | kwargs
    return [a["code"] for a in alerts.evaluate(**args)]


def test_a_healthy_platform_has_no_alert():
    assert codes() == []


def test_disk_alert_starts_at_the_threshold_not_below():
    assert codes(disk=0.79) == []
    assert codes(disk=0.8) == ["disk_full"]


def test_an_unreadable_disk_raises_nothing():
    assert codes(disk=None) == []


def test_worker_memory_is_judged_against_its_own_limit():
    limit = 2560 * 1024**2
    assert codes(services=[worker(memory=limit * 84 // 100)]) == []
    assert codes(services=[worker(memory=limit * 85 // 100)]) == ["worker_memory"]


def test_without_a_memory_limit_the_machine_memory_is_the_reference():
    assert codes(services=[worker(memory=10 * GIB, limit=None, host_total=16 * GIB)]) == []
    assert codes(services=[worker(memory=14 * GIB, limit=None, host_total=16 * GIB)]) == ["worker_memory"]
    assert codes(services=[worker(memory=14 * GIB, limit=None, host_total=None)]) == []  # nothing to compare with


def test_each_worker_over_its_limit_raises_its_own_alert():
    heavy = 2400 * 1024**2
    found = alerts.evaluate([worker(memory=heavy, instance="a"), worker(memory=heavy, instance="b"), worker()], 0.0, 0.1, LIMITS)
    assert [a["code"] for a in found] == ["worker_memory", "worker_memory"]
    assert [a["instance"] for a in found] == ["a", "b"]  # the page keys its list on code + instance
    assert found[0]["message"] == found[1]["message"] and "a" not in found[0]["message"].split()  # the host name is not in the sentence


def test_a_queued_job_waiting_over_the_limit_with_nothing_running_is_stalled():
    assert codes(stalled_seconds=300) == []
    assert codes(stalled_seconds=301) == ["queue_stalled"]


def test_no_reading_at_all_means_no_worker_but_only_a_warning():
    """An environment without a worker (dev, CI, a fresh table) must not page the uptime monitor."""
    found = alerts.evaluate([], 0.0, 0.1, LIMITS)
    assert [(a["code"], a["severity"]) for a in found] == [("worker_absent", "warning")]


def test_a_silent_worker_is_critical_only_past_the_limit_and_its_memory_is_not_judged():
    assert codes(services=[worker(age=300)]) == []
    found = alerts.evaluate([worker(age=301, memory=2559 * 1024**2)], 0.0, 0.1, LIMITS)
    assert [(a["code"], a["severity"]) for a in found] == [("worker_absent", "critical")]


def test_the_web_process_does_not_count_as_a_worker():
    web = {"instance": "web", "role": "web", "age_seconds": 0.0, "reading": {}}
    assert codes(services=[web]) == ["worker_absent"]  # (a warning: no worker row at all)


def test_a_malformed_reading_is_skipped_not_fatal():
    broken = {"instance": "w", "role": "worker", "age_seconds": 1.0, "reading": {}}
    assert codes(services=[broken]) == []


def test_every_alert_has_a_code_a_severity_and_a_french_message():
    found = alerts.evaluate([worker(age=900)], 900.0, 0.95, LIMITS)
    assert [a["code"] for a in found] == ["worker_absent", "queue_stalled", "disk_full"]
    assert all(set(a) <= {"code", "severity", "message", "instance"} and {"code", "severity", "message"} <= set(a) and a["severity"] in {"warning", "critical"} and a["message"] for a in found)
    assert "95 %" in found[2]["message"] and "15 min" in found[1]["message"]


def test_the_probe_keeps_its_historical_words_each_once():
    found = alerts.evaluate([worker(age=900)], 900.0, 0.95, LIMITS)  # worker_absent + queue_stalled + disk_full
    assert alerts.probe_problems(found) == ["no worker", "disk almost full"]
    assert alerts.probe_problems(alerts.evaluate([worker(memory=2400 << 20)], 0.0, 0.1, LIMITS)) == []  # a warning is not a problem


def test_thresholds_default_to_the_documented_values(monkeypatch):
    for name in ("NYRA_ALERT_DISK_PERCENT", "NYRA_ALERT_WORKER_MEMORY_PERCENT", "NYRA_ALERT_QUEUE_MINUTES", "NYRA_ALERT_WORKER_SILENT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    assert alerts.thresholds() == LIMITS


def test_thresholds_can_be_set_and_an_invalid_value_warns_and_keeps_the_default(monkeypatch, caplog):
    monkeypatch.setenv("NYRA_ALERT_DISK_PERCENT", "90")
    monkeypatch.setenv("NYRA_ALERT_QUEUE_MINUTES", "10")
    assert (alerts.thresholds().disk, alerts.thresholds().queue_seconds) == (0.9, 600)
    for bad in ("abc", "0", "101", "-5", "12.5"):
        monkeypatch.setenv("NYRA_ALERT_WORKER_MEMORY_PERCENT", bad)
        with caplog.at_level(logging.WARNING):
            assert alerts.thresholds().worker_memory == 0.85
        assert "NYRA_ALERT_WORKER_MEMORY_PERCENT" in caplog.text
        caplog.clear()


@pytest.mark.parametrize("name", ["NYRA_ALERT_QUEUE_MINUTES", "NYRA_ALERT_WORKER_SILENT_SECONDS"])
def test_out_of_range_durations_keep_the_default(monkeypatch, name):
    monkeypatch.setenv(name, "99999999")
    assert alerts.thresholds() == LIMITS
