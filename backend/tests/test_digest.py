"""The monthly e-mail (cloud/digest.py): what it says (pure), when it leaves, once a month and never twice.

No network: an `httpx.MockTransport` stands for Resend. The database tests skip unless TEST_DATABASE_URL is set."""

from __future__ import annotations

import json
import logging
import threading
import time
import types
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

psycopg = pytest.importorskip("psycopg")

from conftest import MIGRATIONS_DIR  # noqa: E402

from nyra.cloud import db as cloud_db  # noqa: E402
from nyra.cloud import digest, mailer, schedule  # noqa: E402
from nyra.cloud import worker as worker_module  # noqa: E402
from nyra.cloud.api import CloudSettings  # noqa: E402
from nyra.cloud.routes.members import EmailLimit  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
TODAY = NOW.date()
MONTH = date(2026, 11, 1)
OLD = datetime(2026, 9, 1, tzinfo=UTC)         # long before the period: an old image, an old reference
IN_PERIOD = datetime(2026, 11, 5, tzinfo=UTC)
VALUE = "unit-test-value"
SENDER = "Nyra <nyra@example.com>"
NO_SLEEP = lambda seconds: None  # noqa: E731


def days(n: int) -> date:
    return TODAY + timedelta(days=n)


# --- what the e-mail says: pure -----------------------------------------------------------------------------------------

def brand(name="Marque A", slug="marque-a", **kw):
    base = {"name": name, "slug": slug, "new": [], "new_total": 0, "soon": [], "soon_total": 0, "later": [], "later_total": 0,
            "suspect": False, "capped": False, "unfinished": False}
    return {**base, **kw}


def data(*brands, org="Maison Test"):
    return {"org_name": org, "org_slug": "maison-test", "brands": list(brands)}


def soon_item(name, left=10):
    return {"filename": name, "expiry_date": days(left), "days": left}


def build(*brands, url="https://nyra.test", org="Maison Test"):
    return digest.build(data(*brands, org=org), url)


def test_there_is_no_mail_when_there_is_nothing_to_say():
    assert build(brand(), brand("B", "b")) is None


def test_a_mail_holds_the_three_lists_per_brand_and_counts_them():
    mail = build(brand(new=[{"filename": "vieux.jpg", "expiry_date": days(-3), "images": 2}], new_total=1,
                       soon=[soon_item("bientot.jpg", 12)], soon_total=1,
                       later=[soon_item("plus-tard.jpg", 60)], later_total=1))
    for text in (mail.text, mail.html):
        assert all(word in text for word in ("Nouveaux visuels à traiter", "Visuels qui expirent dans 30 jours",
                                             "Visuels qui expirent dans 31 à 90 jours", "vieux.jpg", "bientot.jpg", "plus-tard.jpg", "Marque A"))
    assert mail.counts == {"new": 1, "soon": 1, "later": 1, "brands": 1}


def test_an_empty_section_is_left_out():
    mail = build(brand(soon=[soon_item("a.jpg")], soon_total=1))
    # (the row of figures keeps its three labels, even at 0: only the sections are left out)
    assert "Nouveaux visuels" not in mail.text and "expirent dans 31 à 90" not in mail.text and "dans 30 jours" in mail.text


def test_the_two_thresholds_are_named_constants_in_one_place():
    assert (digest.SOON_DAYS, digest.LATER_DAYS) == (30, 90)
    mail = build(brand(soon=[soon_item("a.jpg")], soon_total=1, later=[soon_item("b.jpg", 40)], later_total=1))
    assert f"dans {digest.SOON_DAYS} jours" in mail.text and f"{digest.SOON_DAYS + 1} à {digest.LATER_DAYS} jours" in mail.text


def test_a_list_is_capped_at_20_lines_with_and_n_others():
    items = [soon_item(f"v{i:02}.jpg", 5) for i in range(25)]
    mail = build(brand(soon=items, soon_total=25))
    assert digest.LIST_MAX == 20
    assert "v19.jpg" in mail.text and "v20.jpg" not in mail.text and "et 5 autres" in mail.text and "et 5 autres" in mail.html
    # the query hands over 20 rows and the real total: the line says how many are left
    assert "et 15 autres" in build(brand(soon=items[:20], soon_total=35)).text
    assert "autre" not in build(brand(soon=items[:20], soon_total=20)).text


def test_a_name_is_escaped_in_the_html_and_cannot_inject_anything():
    trap = '<script>alert(1)</script>.jpg'
    mail = build(brand(name='M"><img src=x onerror=alert(2)>', soon=[soon_item(trap)], soon_total=1), org="<b>Maison</b>")
    assert "<script>" not in mail.html and "&lt;script&gt;alert(1)&lt;/script&gt;.jpg" in mail.html
    assert "<img" not in mail.html and "<b>Maison" not in mail.html and "&lt;b&gt;Maison" in mail.html
    assert trap in mail.text  # the text version is plain


def test_one_sentence_per_suspect_capped_or_unfinished_brand():
    mail = build(brand("Alpha", "alpha", suspect=True), brand("Beta", "beta", capped=True), brand("Gamma", "gamma", unfinished=True),
                 brand("Delta", "delta"))
    lines = [line for line in mail.text.splitlines() if line.strip()]
    suspect = [line for line in lines if "incomplète" in line]
    capped = [line for line in lines if "limite de pages" in line]
    unfinished = [line for line in lines if "n'est pas terminée" in line]
    assert len(suspect) == len(capped) == len(unfinished) == 1
    assert "Alpha" in suspect[0] and "rien n'est marqué retiré" in suspect[0]
    assert "Beta" in capped[0] and "Gamma" in unfinished[0] and "Delta" not in mail.text
    assert mail.counts["new"] == mail.counts["soon"] == 0


def test_the_subject_is_short_one_line_and_does_not_carry_a_long_client_name():
    mail = build(brand(soon=[soon_item("a.jpg")], soon_total=1), org="Grande Maison " * 30)
    assert len(mail.subject) <= 100 and "\n" not in mail.subject and mail.subject.startswith("Nyra")
    assert "Grande Maison" in mail.subject  # a short one stays whole
    assert "Grande Maison " * 5 not in mail.subject


def test_no_word_about_billing_prices_or_subscription():
    mail = build(brand(new=[{"filename": "x.jpg", "expiry_date": days(-1), "images": 1}], new_total=1, soon=[soon_item("a.jpg")],
                       soon_total=1, suspect=True, capped=True, unfinished=True))
    low = (mail.text + mail.html + mail.subject).lower()
    for word in ("factur", "prix", "tarif", "abonnement", "paiement", "€", "euro", "forfait", "quota", "plan "):
        assert word not in low, word


def test_links_point_to_the_application_pages_and_are_left_out_without_a_public_address():
    mail = build(brand(new=[{"filename": "x.jpg", "expiry_date": days(-1), "images": 1}], new_total=1, soon=[soon_item("a.jpg")], soon_total=1))
    assert "https://nyra.test/o/maison-test/b/marque-a/review" in mail.text
    assert "https://nyra.test/o/maison-test/b/marque-a/library" in mail.text
    assert 'href="https://nyra.test/o/maison-test/b/marque-a/review"' in mail.html
    assert "http" not in build(brand(soon=[soon_item("a.jpg")], soon_total=1), url="").text
    assert "https://nyra.test/o" in build(brand(soon=[soon_item("a.jpg")], soon_total=1), url="https://nyra.test/").text


def test_the_html_and_text_fit_the_mailer_limits_with_many_brands():
    brands = [brand(f"Marque {i}", f"m{i}", soon=[soon_item(f"{'long' * 20}{j}.jpg") for j in range(20)], soon_total=40) for i in range(30)]
    mail = build(*brands)
    assert len(mail.html.encode()) < mailer.MAX_BODY_BYTES and len(mail.text.encode()) < mailer.MAX_BODY_BYTES


# --- when: pure -----------------------------------------------------------------------------------------------------------

def test_the_mail_waits_for_the_last_date_and_for_the_reads_but_not_for_more_than_48_hours():
    due = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)
    assert not digest.is_ready(due, 0, due - timedelta(seconds=1))      # (b) the latest date is not passed
    assert digest.is_ready(due, 0, due)                                  # (c) nothing waits or runs
    assert not digest.is_ready(due, 1, due + timedelta(hours=1))         # a scheduled read waits or runs
    assert not digest.is_ready(due, 3, due + timedelta(hours=47, minutes=59))
    assert digest.is_ready(due, 3, due + timedelta(hours=48))            # 48 hours: it leaves anyway


# --- the database ---------------------------------------------------------------------------------------------------------

@pytest.fixture()
def url(cloud_database_url):
    """Nothing else is scheduled anywhere: `run` looks at every organization, these tests only at their own."""
    schedule._LAST_TOLD.clear()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE brand_schedules SET enabled = false")
        conn.execute("DELETE FROM monthly_digests")      # what earlier runs left on a database that is kept
    return cloud_database_url


def settings(url, key=VALUE, sender=SENDER, public="https://nyra.test"):
    return CloudSettings(database_url=url, supabase_url="https://x.supabase.co", service_role_key="k", jwt_secret=None,
                         resend_api_key=key, mail_from=sender, public_url=public)


def transport(answer=200, delay=0.0, idempotent=False):
    """A fake Resend. `idempotent`: like the real one, the same key with another body is refused (409 invalid_idempotent_request)."""
    seen, keys = [], {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        time.sleep(delay)
        if idempotent and keys.setdefault(request.headers["idempotency-key"], request.content) != request.content:
            return httpx.Response(409, json={"name": "invalid_idempotent_request", "message": "different payload"})
        status = answer(len(seen)) if callable(answer) else answer
        if isinstance(status, tuple):
            return httpx.Response(status[0], json=status[1])
        return httpx.Response(status, json={"id": "mail-1"} if status == 200 else {"name": "validation_error", "message": "refused"})

    t = httpx.MockTransport(handler)
    t.seen = seen
    return t


def make_org(conn, *, brands=1, admins=1, clients=1, day=10, enabled=True):
    """(org_id, [brand ids], [admin e-mails], [client e-mails]). The monthly read of every brand is marked done for the month."""
    org = cloud_db.create_organization(conn, name="Maison Test", slug=f"d-{uuid.uuid4().hex[:10]}")
    ids = [conn.execute("SELECT id FROM brands WHERE org_id = %s", (org,)).fetchone()["id"]]
    ids += [cloud_db.create_brand(conn, org_id=org, name=f"Marque {i}", slug=f"marque-{i}") for i in range(2, brands + 1)]
    for brand_id in ids:
        cloud_db.create_site(conn, org_id=org, brand_id=brand_id, url=f"https://{uuid.uuid4().hex[:8]}.test/")
        if enabled:
            schedule.enable(conn, brand_id, org, day, now=NOW)
    conn.execute("UPDATE brand_schedules SET created_at = %s WHERE org_id = %s", (OLD, org))   # switched on long before the dates
    people = {"admin": [], "client": []}
    for role, count in (("admin", admins), ("client", clients)):
        for i in range(count):
            user, address = uuid.uuid4(), f"{role}{i}-{uuid.uuid4().hex[:6]}@client.test"
            conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (user, address))
            cloud_db.add_membership(conn, user_id=user, org_id=org, role=role)
            people[role].append(address)
    return org, ids, people["admin"], people["client"]


def add_ref(conn, org, brand_id, name, expiry=None, unlimited=False, created=OLD):
    return conn.execute(
        """INSERT INTO reference_images (org_id, brand_id, filename, storage_path, expiry_date, unlimited_rights, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (org, brand_id, name, f"{org}/{name}", expiry, unlimited, created)).fetchone()["id"]


def add_hit(conn, org, brand_id, ref, first_seen=IN_PERIOD, decision=None, name=None):
    """A site image first seen at `first_seen`, matched with `ref`, and optionally decided."""
    site = conn.execute("SELECT id FROM sites WHERE brand_id = %s LIMIT 1", (brand_id,)).fetchone()["id"]
    image = conn.execute("INSERT INTO site_images (org_id, site_id, url, first_seen) VALUES (%s, %s, %s, %s) RETURNING id",
                         (org, site, f"https://s.test/{name or uuid.uuid4().hex}.jpg", first_seen)).fetchone()["id"]
    cloud_db.write_matches(conn, org, [(ref, image, "phash", 1.0, "haut")])
    if decision:
        conn.execute("INSERT INTO reviews (reference_id, site_image_id, org_id, decision) VALUES (%s, %s, %s, %s)", (ref, image, org, decision))
    return image


def gather(url, org, since=None, schedules=None):
    with cloud_db.connect(url) as conn:
        rows = schedules if schedules is not None else conn.execute(
            "SELECT brand_id, org_id, enabled, day_of_month, last_run_at FROM brand_schedules WHERE org_id = %s", (org,)).fetchall()
        return digest.gather(conn, org, since or NOW - timedelta(days=digest.PERIOD_DAYS), NOW, rows)


def names(items):
    return [item["filename"] for item in items]


def the_brand(result, index=0):
    return result["brands"][index]


def test_new_visuals_are_the_matches_found_in_the_period_on_an_expired_visual_not_yet_decided(url):
    with cloud_db.connect(url) as conn:
        org, (b1, b2), *_ = make_org(conn, brands=2)
        add_hit(conn, org, b1, add_ref(conn, org, b1, "nouveau.jpg", days(-5)))
        two = add_ref(conn, org, b1, "deux-images.jpg", days(-9))
        add_hit(conn, org, b1, two)
        add_hit(conn, org, b1, two)
        add_hit(conn, org, b1, add_ref(conn, org, b1, "ancienne-image.jpg", days(-5)), first_seen=OLD)     # seen long ago
        add_hit(conn, org, b1, add_ref(conn, org, b1, "decide.jpg", days(-5)), decision="ecarte")           # decided
        add_hit(conn, org, b1, add_ref(conn, org, b1, "traite.jpg", days(-5)), decision="traite")
        add_hit(conn, org, b1, add_ref(conn, org, b1, "pas-expire.jpg", days(+5)))                          # not expired
        add_hit(conn, org, b1, add_ref(conn, org, b1, "illimite.jpg", None, unlimited=True))                # unlimited rights
        add_hit(conn, org, b1, add_ref(conn, org, b1, "sans-date.jpg", None))
        add_ref(conn, org, b2, "jamais-lue.jpg", days(-5))                                                  # brand without any read image
        # a reference added in the period that finds an image seen long ago: the match could not exist before the later of the two
        add_hit(conn, org, b1, add_ref(conn, org, b1, "ref-recente.jpg", days(-2), created=IN_PERIOD), first_seen=OLD)
    result = gather(url, org)
    first = the_brand(result)
    assert sorted(names(first["new"])) == ["deux-images.jpg", "nouveau.jpg", "ref-recente.jpg"] and first["new_total"] == 3
    assert {item["filename"]: item["images"] for item in first["new"]}["deux-images.jpg"] == 2
    assert the_brand(result, 1)["new"] == []


def test_a_match_written_again_today_for_an_old_image_is_not_new(url):
    """`matches.created_at` is reset every time a pass rewrites a match (a re-indexed reference, a new threshold): it cannot
    say when a match was first found. The image's `first_seen` and the reference's `created_at` can."""
    with cloud_db.connect(url) as conn:
        org, (b1,), *_ = make_org(conn)
        ref = add_ref(conn, org, b1, "ancienne.jpg", days(-5))
        image = add_hit(conn, org, b1, ref, first_seen=OLD)
        cloud_db.write_matches(conn, org, [(ref, image, "phash", 1.0, "haut")])
        assert conn.execute("SELECT created_at > %s AS recent FROM matches WHERE site_image_id = %s", (OLD, image)).fetchone()["recent"]
    assert the_brand(gather(url, org))["new"] == []


def test_the_period_starts_at_the_last_mail_sent_to_the_organization(url):
    with cloud_db.connect(url) as conn:
        org, (b1,), *_ = make_org(conn)
        add_hit(conn, org, b1, add_ref(conn, org, b1, "avant.jpg", days(-5)), first_seen=datetime(2026, 11, 2, tzinfo=UTC))
        add_hit(conn, org, b1, add_ref(conn, org, b1, "apres.jpg", days(-5)), first_seen=datetime(2026, 11, 12, tzinfo=UTC))
        assert digest.period_start(conn, org, NOW) == NOW - timedelta(days=digest.PERIOD_DAYS)
        conn.execute("INSERT INTO monthly_digests (org_id, month, status, attempts, sent_at) VALUES (%s, '2026-10-01', 'sent', 1, %s)",
                     (org, datetime(2026, 11, 8, tzinfo=UTC)))
        conn.execute("INSERT INTO monthly_digests (org_id, month, status, attempts) VALUES (%s, '2026-09-01', 'skipped', 1)", (org,))
        since = digest.period_start(conn, org, NOW)
    assert since == datetime(2026, 11, 8, tzinfo=UTC)
    assert names(the_brand(gather(url, org, since=since))["new"]) == ["apres.jpg"]
    assert sorted(names(the_brand(gather(url, org))["new"])) == ["apres.jpg", "avant.jpg"]


def test_the_30_and_90_day_lists_have_exact_limits(url):
    with cloud_db.connect(url) as conn:
        org, (b1,), *_ = make_org(conn)
        for left in (-1, 0, 29, 30, 31, 90, 91, 400):
            add_ref(conn, org, b1, f"in-{left}.jpg", days(left))
        add_ref(conn, org, b1, "illimite.jpg", None, unlimited=True)
        add_ref(conn, org, b1, "sans-date.jpg", None)
    first = the_brand(gather(url, org))
    assert names(first["soon"]) == ["in-0.jpg", "in-29.jpg", "in-30.jpg"]       # expires in 30 days: the 30-day list
    assert names(first["later"]) == ["in-31.jpg", "in-90.jpg"]                  # in 31 days: the 90-day list; in 91: neither
    assert [item["days"] for item in first["soon"]] == [0, 29, 30] and (first["soon_total"], first["later_total"]) == (3, 2)
    assert "in--1.jpg" not in names(first["soon"] + first["later"] + first["new"])


def test_each_list_is_capped_at_20_per_brand_with_the_real_total(url):
    with cloud_db.connect(url) as conn:
        org, (b1, b2), *_ = make_org(conn, brands=2)
        for i in range(30):
            add_ref(conn, org, b1, f"s{i:02}.jpg", days(5 + i % 20))
        for i in range(3):
            add_ref(conn, org, b2, f"t{i}.jpg", days(60))
    result = gather(url, org)
    assert len(the_brand(result)["soon"]) == digest.LIST_MAX == 20 and the_brand(result)["soon_total"] == 30
    assert (len(the_brand(result, 1)["later"]), the_brand(result, 1)["later_total"]) == (3, 3)
    first = names(the_brand(result)["soon"])
    assert first == sorted(first, key=lambda n: (days(5 + int(n[1:3]) % 20), n))    # the soonest first


def test_the_new_visuals_are_capped_at_20_too_with_the_real_total(url):
    with cloud_db.connect(url) as conn:
        org, (b1,), *_ = make_org(conn)
        for i in range(25):
            add_hit(conn, org, b1, add_ref(conn, org, b1, f"n{i:02}.jpg", days(-1 - i)))
    first = the_brand(gather(url, org))
    assert len(first["new"]) == digest.LIST_MAX and first["new_total"] == 25
    assert names(first["new"])[0] == "n24.jpg"                               # the oldest expiry first


def test_a_suspect_last_read_a_read_stopped_by_the_page_limit_and_a_read_not_finished_are_told_per_brand(url):
    with cloud_db.connect(url) as conn:
        org, (b1, b2, b3, b4), *_ = make_org(conn, brands=4)

        def run(brand_id, suspect, started=IN_PERIOD, status="done"):
            site = conn.execute("SELECT id FROM sites WHERE brand_id = %s", (brand_id,)).fetchone()["id"]
            conn.execute("INSERT INTO crawl_runs (org_id, site_id, status, started_at, suspect) VALUES (%s, %s, %s, %s, %s)",
                         (org, site, status, started, suspect))

        def job(brand_id, status, result=None, when=datetime(2026, 11, 10, 3, tzinfo=UTC)):
            conn.execute("INSERT INTO jobs (org_id, brand_id, kind, status, params, result, created_at) VALUES (%s, %s, 'crawl', %s, %s, %s, %s)",
                         (org, brand_id, status, json.dumps({"scheduled": True}), json.dumps(result) if result else None, when))

        run(b1, True)
        run(b1, False, started=OLD)                 # an older, healthy read does not hide the last one
        run(b2, True, started=OLD)                  # a suspect read from before the period is not news
        run(b2, False, status="error", started=IN_PERIOD + timedelta(days=1))
        job(b2, "done", {"quota_reached": True})
        job(b3, "error")
        job(b4, "done", {"quota_reached": False})
        job(b4, "done", {"quota_reached": True}, when=datetime(2026, 10, 10, tzinfo=UTC))   # last month's read: not this month's
    flags = {b["name"]: (b["suspect"], b["capped"], b["unfinished"]) for b in gather(url, org)["brands"]}
    assert flags["Maison Test"] == (True, False, False)
    assert flags["Marque 2"] == (False, True, False)
    assert flags["Marque 3"] == (False, False, True)
    assert flags["Marque 4"] == (False, False, False)


def test_a_brand_due_but_never_queued_counts_as_not_finished_and_a_brand_without_a_site_does_not(url):
    with cloud_db.connect(url) as conn:
        org, (b1, b2), *_ = make_org(conn, brands=2)
        conn.execute("UPDATE brand_schedules SET last_run_at = NULL WHERE brand_id = %s", (b1,))     # never queued: still due
        conn.execute("DELETE FROM sites WHERE brand_id = %s", (b2,))                                  # marked done, no job: nothing to wait for
    flags = {b["name"]: b["unfinished"] for b in gather(url, org)["brands"]}
    assert flags == {"Maison Test": True, "Marque 2": False}


def test_a_member_receives_the_monthly_mail_if_and_only_if_administrator_allowed_by_the_team_and_wanted():
    for role in ("admin", "client"):
        for allowed in (True, False):
            for wanted in (True, False):
                assert digest.receives_monthly(role, allowed, wanted) == (role == "admin" and allowed and wanted), (role, allowed, wanted)


def test_the_recipients_are_the_administrators_only_at_most_ten(url):
    with cloud_db.connect(url) as conn:
        org, _, admins, clients = make_org(conn, admins=12, clients=3)
        nobody = uuid.uuid4()
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, NULL)", (nobody,))
        cloud_db.add_membership(conn, user_id=nobody, org_id=org, role="admin")
        total, found = digest.recipients(conn, org)
    assert digest.MAX_ADMINS == 10 and total == 12 and sorted(found) == sorted(admins) and not set(found) & set(clients)
    assert found == sorted(found)


def switch(url, org, address, **columns):
    """Set `monthly_email_allowed` / `monthly_email_wanted` of the member with this address."""
    with cloud_db.connect(url) as conn:
        for column, value in columns.items():
            conn.execute(f"UPDATE memberships SET {column} = %s WHERE org_id = %s AND user_id = (SELECT id FROM auth.users WHERE email = %s)",
                         (value, org, address))


def test_the_team_s_block_and_a_person_s_refusal_each_leave_that_administrator_out_and_the_counters_stay_right(url):
    org, _, admins, clients = org_with_something_to_say(url, admins=4, clients=2)
    blocked, quiet, kept_a, kept_b = sorted(admins)
    switch(url, org, blocked, monthly_email_allowed=False)
    switch(url, org, quiet, monthly_email_wanted=False)
    switch(url, org, clients[0], monthly_email_allowed=True, monthly_email_wanted=True)   # a client never receives it
    t = transport()
    assert run(url, t) == 1
    assert sorted(json.loads(t.seen[0].content)["to"]) == [kept_a, kept_b]
    detail = audit_rows(url, org, "digest.sent")[0]["detail"]
    assert (detail["recipients"], detail["admins"]) == (2, 4)


def test_an_administrator_blocked_by_the_team_who_also_refused_stays_out_and_defaults_are_yes(url):
    org, _, admins, _ = org_with_something_to_say(url, admins=2, clients=0)
    with cloud_db.connect(url) as conn:
        assert {tuple(r.values()) for r in conn.execute("SELECT monthly_email_allowed, monthly_email_wanted FROM memberships WHERE org_id = %s",
                                                        (org,)).fetchall()} == {(True, True)}
    switch(url, org, admins[0], monthly_email_allowed=False, monthly_email_wanted=False)
    t = transport()
    assert run(url, t) == 1 and json.loads(t.seen[0].content)["to"] == [admins[1]]


@pytest.mark.parametrize("column", ["monthly_email_allowed", "monthly_email_wanted"])
def test_when_nobody_receives_the_month_is_skipped_with_its_own_reason_and_nothing_leaves(url, column):
    org, _, admins, _ = org_with_something_to_say(url, admins=2, clients=1)
    for address in admins:
        switch(url, org, address, **{column: False})
    t = transport()
    assert run(url, t) == 0 and run(url, t) == 0 and not t.seen
    (row,) = digests(url, org)
    assert (row["status"], row["sent_at"]) == ("skipped", None)
    (event,) = audit_rows(url, org, "digest.skipped")
    assert event["detail"] == {"reason": "no_recipient"} and not audit_rows(url, org, "digest.sent") and not audit_rows(url, org, "digest.failed")


def test_the_reason_no_recipient_is_not_no_admin_and_nothing_to_say_comes_first(url):
    quiet, *_ = org_with_something_to_say(url, admins=1, clients=1)
    switch(url, quiet, [a for a in digest_addresses(url, quiet)][0], monthly_email_wanted=False)
    with cloud_db.connect(url) as conn:
        empty, *_ = make_org(conn, admins=1)           # nothing to say, and nobody receives: "nothing"
        conn.execute("UPDATE memberships SET monthly_email_allowed = false WHERE org_id = %s AND role = 'admin'", (empty,))
    t = transport()
    run(url, t)
    assert audit_rows(url, quiet, "digest.skipped")[0]["detail"]["reason"] == "no_recipient"
    assert audit_rows(url, empty, "digest.skipped")[0]["detail"]["reason"] == "nothing"
    assert not t.seen


def digest_addresses(url, org):
    with cloud_db.connect(url) as conn:
        return [r["email"] for r in cloud_db.list_members(conn, org) if r["role"] == "admin"]


def test_the_mail_ends_with_the_way_to_stop_it_and_a_link_to_the_settings_when_the_address_is_known():
    mail = build(brand(soon=[soon_item("a.jpg")], soon_total=1))
    sentence = "Vous pouvez choisir de ne plus recevoir ce message dans Réglages > Notifications."
    assert sentence in mail.text and "Réglages &gt; Notifications" in mail.html
    assert "https://nyra.test/o/maison-test/b/marque-a/settings?section=notifications" in mail.text
    assert 'href="https://nyra.test/o/maison-test/b/marque-a/settings?section=notifications"' in mail.html
    bare = build(brand(soon=[soon_item("a.jpg")], soon_total=1), url="")
    assert sentence in bare.text and "http" not in bare.text and "href" not in bare.html


def test_columns_missing_stops_the_pass_before_anything_is_claimed_and_the_worker_names_the_migration_once_an_hour(url, monkeypatch, caplog):
    org, *_ = org_with_something_to_say(url)
    with cloud_db.connect(url) as conn:
        conn.execute("ALTER TABLE memberships RENAME COLUMN monthly_email_wanted TO monthly_email_wanted_off")
    try:
        t = transport()
        with pytest.raises(psycopg.errors.UndefinedColumn):
            run(url, t)
        assert not t.seen and not digests(url, org) and not audit_rows(url, org, "digest.skipped")   # nothing claimed, nothing marked
        schedule._LAST_TOLD.clear()
        worker = a_worker(url, mail_settings=settings(url))
        real = digest.run
        monkeypatch.setattr(digest, "run", lambda *a, **k: real(*a, transport=t, sleep=NO_SLEEP, **k))
        with caplog.at_level(logging.WARNING, logger="nyra.worker"):
            worker._send_digests(), worker._send_digests()
        missing = [r for r in caplog.records if "20261025000035" in r.getMessage()]
        assert len(missing) == 1 and not missing[0].exc_info and not digests(url, org) and not t.seen
    finally:
        with cloud_db.connect(url) as conn:
            conn.execute("ALTER TABLE memberships RENAME COLUMN monthly_email_wanted_off TO monthly_email_wanted")


def test_more_than_ten_administrators_get_ten_copies_and_the_audit_and_the_log_say_so(url, caplog):
    with cloud_db.connect(url) as conn:
        org, brands, admins, _ = make_org(conn, admins=12, clients=1)
        add_hit(conn, org, brands[0], add_ref(conn, org, brands[0], "expire.jpg", days(-5)))
    t = transport()
    with caplog.at_level(logging.WARNING, logger="nyra.cloud.digest"):
        assert run(url, t) == 1
    sent = json.loads(t.seen[0].content)["to"]
    assert len(sent) == 10 and set(sent) <= set(admins)
    detail = audit_rows(url, org, "digest.sent")[0]["detail"]
    assert (detail["recipients"], detail["admins"]) == (10, 12)
    cut = [r for r in caplog.records if "12 administrators" in r.getMessage()]
    assert len(cut) == 1 and str(org) in cut[0].getMessage() and all("@" not in r.getMessage() for r in caplog.records)


def schedules_of(conn, org):
    return conn.execute("SELECT brand_id, org_id, enabled, day_of_month, last_run_at FROM brand_schedules WHERE org_id = %s", (org,)).fetchall()


def due(url, now=NOW):
    with cloud_db.connect(url) as conn:
        return {item["org_id"]: item for item in digest.due_orgs(conn, now)}


def test_an_organization_is_due_after_the_date_of_its_latest_brand(url):
    with cloud_db.connect(url) as conn:
        org, (b1, b2), *_ = make_org(conn, brands=2, day=5)
        conn.execute("UPDATE brand_schedules SET day_of_month = 25 WHERE brand_id = %s", (b2,))
    assert org not in due(url, schedule.due_date(b2, 25, 2026, 11) - timedelta(minutes=1))
    assert org in due(url, schedule.due_date(b2, 25, 2026, 11))
    assert org not in due(url, schedule.due_date(b2, 25, 2026, 11).replace(month=12, day=1, hour=0))   # next month: not yet


def test_an_organization_without_an_active_monthly_read_is_never_due(url):
    with cloud_db.connect(url) as conn:
        off, _, *_ = make_org(conn, enabled=False)
        mixed, (b1, b2), *_ = make_org(conn, brands=2)
        conn.execute("UPDATE brand_schedules SET enabled = false WHERE brand_id = %s", (b2,))
        conn.execute("UPDATE brand_schedules SET day_of_month = 28 WHERE brand_id = %s", (b2,))          # off: its late date does not count
    found = due(url)
    assert off not in found and mixed in found


def test_it_waits_for_the_scheduled_reads_of_the_organization_but_only_48_hours(url):
    with cloud_db.connect(url) as conn:
        org, (b1,), *_ = make_org(conn)
        latest = schedule.due_date(b1, 10, 2026, 11)
        conn.commit()      # `due` reads on its own connection
        for status in ("queued", "running"):
            conn.execute("DELETE FROM jobs WHERE org_id = %s", (org,))
            conn.execute("INSERT INTO jobs (org_id, brand_id, kind, status, params) VALUES (%s, %s, 'crawl', %s, %s)",
                         (org, b1, status, json.dumps({"scheduled": True})))
            conn.commit()
            assert org not in due(url, latest + timedelta(hours=1)), status
            assert org in due(url, latest + timedelta(hours=48)), status
        conn.execute("DELETE FROM jobs WHERE org_id = %s", (org,))
        conn.execute("INSERT INTO jobs (org_id, brand_id, kind, status) VALUES (%s, %s, 'crawl', 'queued')", (org, b1))   # started by hand: not in the way
        conn.commit()
        assert org in due(url, latest + timedelta(hours=1))


def test_a_brand_switched_on_after_its_date_does_not_count_this_month_but_does_the_next(url):
    with cloud_db.connect(url) as conn:
        org, (b1,), *_ = make_org(conn)
        conn.execute("UPDATE brand_schedules SET created_at = %s WHERE org_id = %s", (NOW - timedelta(hours=1), org))   # after the 10th
    assert org not in due(url)                                                      # no read this month, so no mail either
    assert org in due(url, schedule.due_date(b1, 10, 2026, 12))                     # next month: the read ran, the mail follows
    with cloud_db.connect(url) as conn:
        conn.execute("UPDATE brand_schedules SET created_at = %s WHERE org_id = %s", (schedule.due_date(b1, 10, 2026, 11) - timedelta(seconds=1), org))
    assert org in due(url)                                                          # existed just before the date: it counts


def test_a_brand_switched_off_and_on_again_keeps_its_place(url):
    with cloud_db.connect(url) as conn:
        org, (b1,), *_ = make_org(conn)
        schedule.disable(conn, b1)
        schedule.enable(conn, b1, org, 10, now=NOW)
        assert conn.execute("SELECT created_at = %s AS same FROM brand_schedules WHERE brand_id = %s", (OLD, b1)).fetchone()["same"]
    assert org in due(url)


def bare_org(conn, brands, day, org_id=None):
    """An organization with `brands` schedules and nothing else (no site, no member)."""
    if org_id is None:
        org_id = uuid.uuid4()
    conn.execute("INSERT INTO organizations (id, name, slug) VALUES (%s, 'Bare', %s)", (org_id, f"b-{uuid.uuid4().hex[:12]}"))
    for i in range(brands):
        brand = cloud_db.create_brand(conn, org_id=org_id, name=f"M{i}", slug=f"m{i}")
        schedule.enable(conn, brand, org_id, day, now=NOW)
    conn.execute("UPDATE brand_schedules SET created_at = %s, last_run_at = %s WHERE org_id = %s", (OLD, NOW, org_id))
    return org_id


def test_hundreds_of_brands_of_organizations_not_ready_do_not_hide_a_ready_one(url, monkeypatch):
    monkeypatch.setattr(digest, "ORG_LIMIT", 20)
    monkeypatch.setattr(digest, "MAX_PAGES", 1)
    last = uuid.UUID("ffffffff-ffff-4fff-8fff-" + uuid.uuid4().hex[:12])          # sorts after the others: the old scan never reached it
    with cloud_db.connect(url) as conn:
        for _ in range(60):
            bare_org(conn, 10, day=28)                                              # 600 rows, the 28th has not come
        ready = bare_org(conn, 2, day=10, org_id=last)
    found = due(url)
    assert list(found) == [ready] and len(found[ready]["schedules"]) == 2


def test_organizations_are_read_whole_and_page_after_page_up_to_the_bounds(url, monkeypatch):
    monkeypatch.setattr(digest, "ORG_LIMIT", 2)
    monkeypatch.setattr(digest, "MAX_PAGES", 10)
    monkeypatch.setattr(digest, "MAX_PER_PASS", 100)
    with cloud_db.connect(url) as conn:
        orgs = {bare_org(conn, 3, day=10) for _ in range(5)}
    found = due(url)
    assert set(found) == orgs and all(len(item["schedules"]) == 3 for item in found.values())   # never half an organization
    monkeypatch.setattr(digest, "MAX_PAGES", 2)
    assert len(due(url)) == 4                                                       # 2 pages of 2 organizations: bounded
    monkeypatch.setattr(digest, "MAX_PAGES", 10)
    monkeypatch.setattr(digest, "MAX_PER_PASS", 3)
    assert 3 <= len(due(url)) <= 4                                                  # it stops paging once enough are ready


def test_an_organization_already_handled_this_month_is_not_due_again(url):
    with cloud_db.connect(url) as conn:
        sent, _, *_ = make_org(conn)
        skipped, _, *_ = make_org(conn)
        last_month, _, *_ = make_org(conn)
        for org, status in ((sent, "sent"), (skipped, "skipped")):
            conn.execute("INSERT INTO monthly_digests (org_id, month, status, attempts) VALUES (%s, %s, %s, 1)", (org, MONTH, status))
        conn.execute("INSERT INTO monthly_digests (org_id, month, status, attempts) VALUES (%s, '2026-10-01', 'sent', 1)", (last_month,))
    found = due(url)
    assert sent not in found and skipped not in found and last_month in found


def digests(url, org):
    with cloud_db.connect(url) as conn:
        return conn.execute("SELECT * FROM monthly_digests WHERE org_id = %s ORDER BY month", (org,)).fetchall()


def audit_rows(url, org, action):
    with cloud_db.connect(url) as conn:
        return conn.execute("SELECT * FROM audit_log WHERE org_id = %s AND action = %s", (org, action)).fetchall()


def run(url, t, **kw):
    kw.setdefault("sleep", NO_SLEEP)
    return digest.run(settings(url), NOW, transport=t, **kw)


def org_with_something_to_say(url, **kw):
    with cloud_db.connect(url) as conn:
        org, brands, admins, clients = make_org(conn, **kw)
        add_hit(conn, org, brands[0], add_ref(conn, org, brands[0], "expire.jpg", days(-5)))
        add_ref(conn, org, brands[0], "bientot.jpg", days(20))
    return org, brands, admins, clients


def test_the_mail_leaves_once_to_the_administrators_with_the_monthly_key_and_is_recorded(url):
    org, _, admins, clients = org_with_something_to_say(url, admins=2, clients=2)
    t = transport()
    assert run(url, t) == 1
    (request,) = t.seen
    body = json.loads(request.content)
    assert sorted(body["to"]) == sorted(admins) and not set(body["to"]) & set(clients)
    assert request.headers["idempotency-key"] == f"monthly:{org}:2026-11"
    assert "expire.jpg" in body["text"] and "bientot.jpg" in body["html"] and body["from"] == SENDER
    (row,) = digests(url, org)
    assert (row["month"], row["status"], row["attempts"]) == (MONTH, "sent", 1) and row["sent_at"] is not None
    (event,) = audit_rows(url, org, "digest.sent")
    assert event["detail"] == {"new": 1, "soon": 1, "later": 0, "brands": 1, "recipients": 2, "admins": 2} and event["user_id"] is None
    assert "@" not in json.dumps(event["detail"]) and "expire.jpg" not in json.dumps(event["detail"])
    # again, and again later in the month: nothing more leaves
    assert run(url, t) == 0 and run(url, t) == 0 and len(t.seen) == 1


def test_nothing_to_say_marks_the_month_without_sending(url):
    with cloud_db.connect(url) as conn:
        org, *_ = make_org(conn)
    t = transport()
    assert run(url, t) == 0 and run(url, t) == 0
    assert not t.seen
    (row,) = digests(url, org)
    assert (row["status"], row["sent_at"]) == ("skipped", None)
    assert len(audit_rows(url, org, "digest.skipped")) == 1 and not audit_rows(url, org, "digest.sent")


def test_an_organization_without_an_administrator_is_skipped(url):
    org, *_ = org_with_something_to_say(url, admins=0, clients=2)
    t = transport()
    assert run(url, t) == 0 and not t.seen
    assert digests(url, org)[0]["status"] == "skipped"
    assert audit_rows(url, org, "digest.skipped")[0]["detail"]["reason"] == "no_admin"


def test_a_warning_alone_is_enough_to_send(url):
    with cloud_db.connect(url) as conn:
        org, (b1,), *_ = make_org(conn)
        conn.execute("INSERT INTO jobs (org_id, brand_id, kind, status, params, created_at) VALUES (%s, %s, 'crawl', 'error', %s, %s)",
                     (org, b1, json.dumps({"scheduled": True}), datetime(2026, 11, 10, 3, tzinfo=UTC)))
    t = transport()
    assert run(url, t) == 1 and "n'est pas terminée" in json.loads(t.seen[0].content)["text"]


def test_two_workers_at_the_same_moment_send_one_mail(url):
    org, *_ = org_with_something_to_say(url)
    t = transport(delay=0.3)                       # the first send is still going when the second worker looks
    barrier, results = threading.Barrier(2), []

    def worker():
        barrier.wait()
        results.append(run(url, t))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [thread.start() for thread in threads]
    [thread.join(30) for thread in threads]
    assert sorted(results) == [0, 1] and len(t.seen) == 1
    assert digests(url, org)[0]["status"] == "sent" and len(audit_rows(url, org, "digest.sent")) == 1


def age(url, org, minutes=31):
    with cloud_db.connect(url) as conn:
        conn.execute("UPDATE monthly_digests SET updated_at = now() - make_interval(mins => %s) WHERE org_id = %s", (minutes, org))


def test_a_claim_is_atomic_and_a_fresh_one_is_left_alone_until_it_is_30_minutes_old(url):
    with cloud_db.connect(url) as conn:
        org, *_ = make_org(conn)
        assert digest.claim(conn, org, MONTH) == 1
    with cloud_db.connect(url) as conn:
        assert digest.claim(conn, org, MONTH) is None                       # a worker is on it
    age(url, org, minutes=29)
    with cloud_db.connect(url) as conn:
        assert digest.claim(conn, org, MONTH) is None
    age(url, org, minutes=31)
    with cloud_db.connect(url) as conn:
        assert digest.claim(conn, org, MONTH) == 2                          # it died: taken over, the count goes up
        assert digest.claim(conn, org, MONTH) is None


def test_a_worker_that_dies_after_sending_is_replaced_and_resends_with_the_same_key(url, monkeypatch):
    org, *_ = org_with_something_to_say(url)
    t = transport()
    real = digest._mark
    monkeypatch.setattr(digest, "_mark", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed")))
    assert run(url, t) == 0                                 # the failure is logged, the pass goes on
    assert len(t.seen) == 1 and digests(url, org)[0]["status"] == "sending"
    monkeypatch.setattr(digest, "_mark", real)
    assert run(url, t) == 0 and len(t.seen) == 1          # another worker 5 minutes later: the claim is fresh, it waits
    age(url, org)
    assert run(url, t) == 1 and len(t.seen) == 2           # after 30 minutes it takes over and sends again
    assert t.seen[0].headers["idempotency-key"] == t.seen[1].headers["idempotency-key"] == f"monthly:{org}:2026-11"
    assert digests(url, org)[0]["status"] == "sent" and digests(url, org)[0]["attempts"] == 2


def test_a_refusal_keeps_the_month_open_and_the_next_try_comes_after_the_wait(url):
    org, *_ = org_with_something_to_say(url)
    t = transport(lambda n: 422 if n == 1 else 200)
    assert run(url, t) == 0
    (row,) = digests(url, org)
    assert (row["status"], row["attempts"], row["sent_at"]) == ("sending", 1, None)
    assert len(audit_rows(url, org, "digest.failed")) == 1 and not audit_rows(url, org, "digest.sent")
    assert run(url, t) == 0 and len(t.seen) == 1            # not at once: no hammering every 5 minutes
    age(url, org)
    assert run(url, t) == 1 and len(t.seen) == 2
    assert digests(url, org)[0]["status"] == "sent"


def test_the_daily_limit_of_e_mails_is_a_failure_to_try_again_later(url, monkeypatch):
    org, *_ = org_with_something_to_say(url)
    calls = []

    def limited(*args, **kwargs):
        calls.append(kwargs.get("idempotency_key"))
        raise EmailLimit()

    monkeypatch.setattr(mailer, "send", limited)
    assert run(url, transport()) == 0
    assert digests(url, org)[0]["status"] == "sending" and calls == [f"monthly:{org}:2026-11"]
    assert audit_rows(url, org, "digest.failed")[0]["detail"]["error"] == "EmailLimit"


def test_a_mail_that_left_but_was_not_marked_is_recognised_when_the_rebuilt_body_differs(url, monkeypatch, caplog):
    """The worker died between the send and the mark. Next pass the content has changed (a new visual): Resend refuses the
    same key with another body (409 invalid_idempotent_request). That means the first mail LEFT: the month is closed."""
    org, brands, *_ = org_with_something_to_say(url)
    t = transport(idempotent=True)
    real = digest._mark
    monkeypatch.setattr(digest, "_mark", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed")))
    assert run(url, t) == 0 and len(t.seen) == 1
    monkeypatch.setattr(digest, "_mark", real)
    with cloud_db.connect(url) as conn:
        add_hit(conn, org, brands[0], add_ref(conn, org, brands[0], "arrive-apres.jpg", days(-2)))       # the body is not the same any more
    age(url, org)
    with caplog.at_level(logging.INFO, logger="nyra.cloud.digest"):
        assert run(url, t) == 1
    assert len(t.seen) == 2 and t.seen[0].content != t.seen[1].content
    (row,) = digests(url, org)
    assert (row["status"], row["attempts"]) == ("sent", 2) and row["sent_at"] is not None
    (event,) = audit_rows(url, org, "digest.sent")
    assert event["detail"]["recovered"] == 1 and not audit_rows(url, org, "digest.failed")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "not sent" in r.getMessage()]
    assert any("already left" in r.getMessage() for r in caplog.records if r.levelno == logging.INFO)
    assert run(url, t) == 0 and len(t.seen) == 2


def test_an_identical_rebuilt_body_is_just_a_second_call_with_the_same_key(url, monkeypatch):
    org, *_ = org_with_something_to_say(url)
    t = transport(idempotent=True)
    real = digest._mark
    monkeypatch.setattr(digest, "_mark", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed")))
    run(url, t)
    monkeypatch.setattr(digest, "_mark", real)
    age(url, org)
    assert run(url, t) == 1 and len(t.seen) == 2
    assert "recovered" not in audit_rows(url, org, "digest.sent")[0]["detail"]


@pytest.mark.parametrize("answer", [(409, {"name": "concurrent_idempotent_requests", "message": "busy"}),
                                    (409, {"name": "validation_error", "message": "nope"}), 422])
def test_any_other_refusal_is_still_a_failure_counted_as_before(url, answer):
    org, *_ = org_with_something_to_say(url)
    assert run(url, transport(answer)) == 0
    (row,) = digests(url, org)
    assert (row["status"], row["attempts"]) == ("sending", 1)
    assert len(audit_rows(url, org, "digest.failed")) == 1 and not audit_rows(url, org, "digest.sent")


def test_after_5_attempts_it_gives_up_and_says_so_once_a_day(url, caplog):
    org, *_ = org_with_something_to_say(url)
    t = transport(422)
    with caplog.at_level(logging.WARNING, logger="nyra.cloud.digest"):
        for _ in range(9):
            age(url, org)
            run(url, t)
    assert len(t.seen) == digest.MAX_ATTEMPTS == 5
    (row,) = digests(url, org)
    assert (row["status"], row["attempts"]) == ("sending", 5)
    gave_up = [r for r in caplog.records if "abandoned" in r.getMessage()]
    assert len(gave_up) == 1 and str(org) in gave_up[0].getMessage() and not gave_up[0].exc_info
    assert not [r for r in caplog.records if r.exc_info]                         # never a full trace for a refusal
    assert all("@" not in r.getMessage() for r in caplog.records)
    schedule._LAST_TOLD.clear()                                                  # the next day it is told again, once
    age(url, org)
    with caplog.at_level(logging.WARNING, logger="nyra.cloud.digest"):
        run(url, t), run(url, t)
    assert len([r for r in caplog.records if "abandoned" in r.getMessage()]) == 2


def test_without_a_key_nothing_is_marked_nothing_crashes_and_the_warning_comes_once_an_hour(url, caplog):
    org, *_ = org_with_something_to_say(url)
    t = transport()
    with caplog.at_level(logging.WARNING, logger="nyra.cloud.digest"):
        for key, sender in (("", ""), ("", SENDER), (VALUE, ""), ("", "")):
            assert digest.run(settings(url, key=key, sender=sender), NOW, transport=t, sleep=NO_SLEEP) == 0
    assert not t.seen and digests(url, org) == []
    warnings = [r for r in caplog.records if "not configured" in r.getMessage()]
    assert len(warnings) == 1 and "RESEND_API_KEY" in warnings[0].getMessage() and not warnings[0].exc_info
    schedule._LAST_TOLD.clear()                                                   # an hour later
    with caplog.at_level(logging.WARNING, logger="nyra.cloud.digest"):
        digest.run(settings(url, key=""), NOW, transport=t, sleep=NO_SLEEP)
    assert len([r for r in caplog.records if "not configured" in r.getMessage()]) == 2
    assert run(url, t) == 1                                                       # configured at last: it leaves, once


def test_without_a_public_address_the_mail_leaves_without_links_and_says_so_once_an_hour(url, caplog):
    org, *_ = org_with_something_to_say(url)
    t = transport()
    with caplog.at_level(logging.WARNING, logger="nyra.cloud.digest"):
        assert digest.run(settings(url, public=""), NOW, transport=t, sleep=NO_SLEEP) == 1
        digest.run(settings(url, public=""), NOW, transport=t, sleep=NO_SLEEP)
    assert "http" not in json.loads(t.seen[0].content)["text"] and "href" not in json.loads(t.seen[0].content)["html"]
    assert len([r for r in caplog.records if "NYRA_PUBLIC_URL" in r.getMessage()]) == 1


def test_nothing_is_logged_about_the_people_or_the_files(url, caplog):
    org, _, admins, _ = org_with_something_to_say(url)
    with caplog.at_level(logging.DEBUG):
        run(url, transport(422))
    text = caplog.text
    assert not any(address in text for address in admins) and "expire.jpg" not in text and VALUE not in text


# --- the worker -----------------------------------------------------------------------------------------------------------

def a_worker(url, **kw):
    return worker_module.Worker(database_url=url, storage_client_factory=lambda: None, **kw)


def test_a_wrong_sender_stops_the_web_but_not_the_worker_which_goes_on_without_e_mail(monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:x@db.invalid/none")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", VALUE)
    monkeypatch.setenv("NYRA_MAIL_FROM", "not a sender at all")
    monkeypatch.setattr("nyra.envfile.load_env_files", lambda *a, **k: None)
    from nyra.cloud.api import settings_from_env

    with pytest.raises(RuntimeError, match="NYRA_MAIL_FROM"):
        settings_from_env()                                  # the web stays strict
    with caplog.at_level(logging.ERROR):
        assert worker_module.mail_settings_or_none(None) is None
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "NYRA_MAIL_FROM invalide" in errors[0].getMessage() and "désactivés" in errors[0].getMessage()
    assert VALUE not in caplog.text
    monkeypatch.setenv("NYRA_MAIL_FROM", SENDER)
    assert worker_module.mail_settings_or_none(None).mail_from == SENDER


def test_without_settings_the_digest_is_unconfigured_and_does_not_crash(url, caplog):
    org, *_ = org_with_something_to_say(url)
    worker = a_worker(url)                                    # mail_settings=None
    with caplog.at_level(logging.WARNING):
        worker._send_digests()
        worker._send_digests()
    assert digests(url, org) == []
    assert len([r for r in caplog.records if "not configured" in r.getMessage()]) == 1
    assert digest.run(None, NOW, database_url=url) == 0


def test_the_worker_sends_the_digests_unless_the_scheduler_is_off(monkeypatch):
    calls = []
    monkeypatch.setattr(digest, "run", lambda *a, **k: calls.append(a) or 0)
    worker = a_worker("postgresql://x:x@db.invalid/none", mail_settings="the-settings")
    worker._send_digests()
    assert len(calls) == 1 and calls[0][0] == "the-settings"
    monkeypatch.setenv("NYRA_SCHEDULER", "off")
    worker._send_digests()
    assert len(calls) == 1


def test_a_missing_table_is_one_warning_an_hour_without_a_trace_and_another_failure_is_logged_in_full(monkeypatch, caplog):
    import psycopg

    schedule._LAST_TOLD.clear()
    worker = a_worker("postgresql://x:x@db.invalid/none", mail_settings="the-settings")
    monkeypatch.setattr(digest, "run", lambda *a, **k: (_ for _ in ()).throw(psycopg.errors.UndefinedTable("monthly_digests")))
    with caplog.at_level(logging.WARNING, logger="nyra.worker"):
        worker._send_digests(), worker._send_digests(), worker._send_digests()
    missing = [r for r in caplog.records if "monthly_digests" in r.getMessage()]
    assert len(missing) == 1 and "20261025000034" in missing[0].getMessage() and not missing[0].exc_info
    caplog.clear()
    monkeypatch.setattr(digest, "run", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    with caplog.at_level(logging.WARNING, logger="nyra.worker"):
        worker._send_digests()                               # never fatal
    assert any(r.exc_info for r in caplog.records)


def test_the_maintenance_loop_sends_the_digests_right_after_queueing_the_reads(monkeypatch):
    order = []
    worker = a_worker("postgresql://x:x@db.invalid/none")
    monkeypatch.setattr(worker, "_enqueue_scheduled", lambda: order.append("reads"))
    monkeypatch.setattr(worker, "_send_digests", lambda: order.append("digests"))
    monkeypatch.setattr(worker, "run_once", lambda: worker.stop() or False)
    monkeypatch.setattr(worker_module, "touch_alive", lambda: None)
    monkeypatch.setattr(worker_module, "clean_temp", lambda now: 0)
    monkeypatch.setattr(worker_module.cloud_storage, "trim_cache", lambda: None)
    monkeypatch.setattr(worker_module.cloud_audit, "purge", lambda *a, **k: 0)
    monkeypatch.setattr(worker_module.cloud_jobs, "reap_stale", lambda conn: (0, 0))
    monkeypatch.setattr(worker_module.cloud_db, "connect", lambda url: __import__("contextlib").nullcontext(None))
    monkeypatch.setattr(worker_module, "threading", types.SimpleNamespace(Thread=lambda **k: types.SimpleNamespace(start=lambda: None)))
    worker.run_forever()
    assert order == ["reads", "digests"]


# --- the table ------------------------------------------------------------------------------------------------------------

def test_the_migration_can_be_applied_twice_and_only_the_administrators_may_read(cloud_database_url, cloud_org):
    sql = (MIGRATIONS_DIR / "20261025000034_monthly_digests.sql").read_text(encoding="utf-8")
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute(sql)
        conn.execute(sql)
        policies = conn.execute("SELECT policyname, cmd, qual FROM pg_policies WHERE tablename = 'monthly_digests'").fetchall()
        rls = conn.execute("SELECT relrowsecurity FROM pg_class WHERE relname = 'monthly_digests'").fetchone()["relrowsecurity"]
    with pytest.raises(psycopg.errors.CheckViolation), cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO monthly_digests (org_id, month, status) VALUES (%s, '2026-01-01', 'other')", (cloud_org,))
    assert rls and [(p["cmd"], "is_org_admin" in p["qual"]) for p in policies] == [("SELECT", True)]
