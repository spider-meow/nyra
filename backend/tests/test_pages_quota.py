"""How the organizations stand against their month's pages (AXE-708): the count behind `nyra cloud-pages-quota`. Cloud tests (TEST_DATABASE_URL)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from nyra.cloud import db as cloud_db
from nyra.cloud import usage
from nyra.config import Config

BASE = Config().quota  # 5000 pages a month


def organization(conn, *, last=0, this=0, limit=None):
    org = cloud_db.create_organization(conn, name="Q", slug=f"q-{uuid.uuid4().hex[:8]}")
    if limit is not None:
        usage.set_overrides(conn, org, {"pages_per_month": limit})
    first = usage.month_start()
    for period, amount in (((first - timedelta(days=1)).replace(day=1), last), (first, this)):
        if amount:
            conn.execute("INSERT INTO usage_counters (org_id, period, kind, amount) VALUES (%s, %s, 'pages_read', %s)", (org, period, amount))
    return org


def test_the_outlook_counts_who_would_pass_their_limit_with_two_reads_and_where_this_month_stands(cloud_database_url):
    with cloud_db.connect(cloud_database_url) as conn:
        before = usage.pages_outlook(conn, BASE)
        organization(conn, last=3000)                      # 2 x 3000 passes 5000
        organization(conn, last=2500)                      # 2 x 2500 is exactly the limit: it does not pass it
        organization(conn, last=800, limit=1000)           # 2 x 800 passes 1000, with this organization's own limit
        organization(conn)                                 # read nothing
        organization(conn, this=4000)                      # 80 % of 5000: a warning
        organization(conn, this=5000)                      # the limit
        after = usage.pages_outlook(conn, BASE)
        three = usage.pages_outlook(conn, BASE, reads_per_month=3)
    delta = {key: after[key] - before[key] for key in after}
    assert delta == {"organizations": 6, "read_last_month": 3, "would_exceed": 2, "warning_this_month": 1, "reached_this_month": 1}
    assert three["would_exceed"] - before["would_exceed"] == 3  # 3 x 2500 passes too
    assert all(isinstance(value, int) for value in after.values())  # numbers: no name, no identifier


def test_the_command_prints_numbers_only(cloud_database_url, monkeypatch):
    from typer.testing import CliRunner

    from nyra import cli

    monkeypatch.setenv("DATABASE_URL", cloud_database_url)
    with cloud_db.connect(cloud_database_url) as conn:
        organization(conn, last=3000)
    out = CliRunner().invoke(cli.app, ["cloud-pages-quota"])
    assert out.exit_code == 0 and "would pass their limit at 2 read(s) a month" in out.output and "q-" not in out.output
    assert CliRunner().invoke(cli.app, ["cloud-pages-quota", "--reads", "0"]).exit_code != 0  # 1 at least


def test_the_alert_counts_the_organizations_at_the_share_and_only_when_it_is_set(cloud_database_url, monkeypatch):
    from nyra.cloud import alerts

    def codes():
        with cloud_db.connect(cloud_database_url) as conn:
            state = alerts.current(conn, services=[], quota=BASE)
        return {a["code"]: a for a in state["alerts"]}

    monkeypatch.delenv("NYRA_ALERT_PAGES_PERCENT", raising=False)
    with cloud_db.connect(cloud_database_url) as conn:
        organization(conn, this=4600)                       # 92 % of 5000
        organization(conn, this=500, limit=520)             # 96 % of its own limit
        organization(conn, this=4000)                       # 80 %: under 90
        organization(conn, this=10, limit=0)                # a limit of 0 is left out, not counted as full
        near = usage.pages_near_limit(conn, BASE, 90)
    assert "pages_quota" not in codes()                      # off by default
    monkeypatch.setenv("NYRA_ALERT_PAGES_PERCENT", "90")
    found = codes()["pages_quota"]
    assert found["severity"] == "warning" and f"{near} organisation" in found["message"] and near >= 2
    assert all(a["code"] != "pages_quota" for a in alerts.public(list(codes().values())))
