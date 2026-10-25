"""The e-mail registry and the back office's "E-mails" routes: who may use them, what the preview shows, the test mail
(to the signed-in account only, through a fake Resend), the limit of 10 an hour, the audit, the key staying secret,
and the rule that every e-mail the code sends is in the registry.

No network: an `httpx.MockTransport` stands for Resend. The route tests need TEST_DATABASE_URL."""

# ruff: noqa: F811 (the `org_with_users` fixture of test_cloud_api is imported, then used by name)
from __future__ import annotations

import ast
import json
import logging
import math
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import httpx
import jwt as pyjwt
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("psycopg")

from conftest import SECRET  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_cloud_api import org_with_users  # noqa: E402, F401
from test_mailer import _transport  # noqa: E402

import nyra  # noqa: E402
from nyra.cloud import db as cloud_db  # noqa: E402
from nyra.cloud import digest, email_registry, mailer  # noqa: E402
from nyra.cloud.api import CloudSettings, create_app  # noqa: E402

KEY = "unit-test-value"
SENDER = "Nyra <nyra@example.com>"
BANNER = "E-mail de test : données fictives, envoyé depuis le back office."
NO_MAIL = "Cet e-mail est envoyé par Supabase : aperçu seulement."


# --- the registry ---------------------------------------------------------------------------------------------------------

def test_the_registry_lists_the_three_mails_with_their_sender():
    senders = {entry.key: entry.sender for entry in email_registry.ENTRIES}
    assert senders == {"monthly_digest": "nyra", "invite": "supabase", "recovery": "supabase"}
    assert all(entry.label and entry.description for entry in email_registry.ENTRIES)


def test_every_sample_is_a_complete_mail_the_mailer_accepts():
    for entry in email_registry.ENTRIES:
        subject, html, text = entry.sample()
        assert subject.strip() and "\n" not in subject and len(subject) <= mailer.MAX_SUBJECT, entry.key
        assert html.strip() and text.strip() and "{{" not in html + text, entry.key
        assert len(html.encode()) < mailer.MAX_BODY_BYTES and len(text.encode()) < mailer.MAX_BODY_BYTES, entry.key


def test_the_monthly_sample_is_what_the_digest_code_renders_on_made_up_data():
    subject, html, text = email_registry.get("monthly_digest").sample()
    mail = digest.build(email_registry.digest_data(date.today()), email_registry.EXAMPLE_URL)
    assert (subject, html, text) == (mail.subject, mail.html, mail.text)
    assert "Maison Exemple" in html and "Nouveaux visuels à traiter" in html and "semble incomplète" in html
    assert mail.counts["new"] == 1 and mail.counts["soon"] >= 1 and mail.counts["later"] >= 1


def test_the_supabase_samples_are_the_template_files_with_example_values():
    root = Path(nyra.__file__).resolve().parents[2]
    for key, subject in (("invite", "Votre accès à Nyra"), ("recovery", "Nouveau mot de passe — Nyra")):
        got_subject, html, _ = email_registry.get(key).sample()
        template = (root / "supabase" / "templates" / ("invite.html" if key == "invite" else "recovery.html")).read_text(encoding="utf-8")
        assert got_subject == subject and "{{" not in html and "https://example.com/" in html
        assert re.sub(r"\{\{[^}]*\}\}", "", template)[:200] == re.sub(r"\{\{[^}]*\}\}", "", html)[:200]


# --- every mail the code sends is in the registry ----------------------------------------------------------------------------

def _unregistered(source: str, kinds, entry_key_ok: bool = False) -> list[str]:
    """What breaks the rule "every e-mail sent is in the registry" in `source`: a `mailer` imported under another name or
    through `from ...mailer import`; `mailer` used as anything but `mailer.<name>`; `mailer.send` / `mailer.deliver` used as
    anything but the function of a call (`getattr`, `partial`, `f = mailer.send`...); a call whose `kind=` is not a literal
    of the registry, or, only where `entry_key_ok` (the route that looks an entry up), exactly `entry.key`."""
    tree, problems = ast.parse(source), []
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and ((node.module or "").split(".")[-1] == "mailer" or any(a.name == "mailer" and a.asname for a in node.names)):
            problems.append(f"line {node.lineno}: mailer imported under another name or by `from ...mailer import` (use `from .. import mailer`)")
        if isinstance(node, ast.Import) and any(a.name.split(".")[-1] == "mailer" for a in node.names):
            problems.append(f"line {node.lineno}: `import ...mailer` (use `from .. import mailer`)")
        if isinstance(node, ast.Name) and node.id == "mailer" and not isinstance(parent.get(node), ast.Attribute):
            problems.append(f"line {node.lineno}: mailer used other than as mailer.<name>")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "mailer" and node.attr in {"send", "deliver"}:
            call = parent.get(node)
            if not (isinstance(call, ast.Call) and call.func is node):
                problems.append(f"line {node.lineno}: mailer.{node.attr} must be called directly")
                continue
            given = [k.value for k in call.keywords if k.arg == "kind"]
            literal = given and isinstance(given[0], ast.Constant) and given[0].value in kinds
            entry_key = given and entry_key_ok and isinstance(given[0], ast.Attribute) and given[0].attr == "key" \
                and isinstance(given[0].value, ast.Name) and given[0].value.id == "entry"
            if not (literal or entry_key):
                problems.append(f"line {node.lineno}: mailer.{node.attr} without kind= a literal of the registry")
    return problems


def _production_sources():
    for path in sorted(Path(nyra.__file__).parent.rglob("*.py")):
        if path.name != "mailer.py":
            yield path, path.read_text(encoding="utf-8")


def test_every_mail_kind_is_registered():
    kinds = {entry.key for entry in email_registry.ENTRIES}
    problems = [f"{path.name} {p}" for path, source in _production_sources() for p in _unregistered(source, kinds, path.name == "staff_emails.py")]
    assert not problems, "an e-mail sent without being in cloud/email_registry.py (see 'Ajouter un e-mail' in docs/ARCHITECTURE.md): " + "; ".join(problems)
    sent = {k.value.value for _, source in _production_sources() for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) for k in n.keywords
            if k.arg == "kind" and isinstance(k.value, ast.Constant)}
    assert {e.key for e in email_registry.ENTRIES if e.sender == "nyra"} <= sent, "a registry entry sent by Nyra that no code sends"


def test_the_kind_check_is_not_fooled():
    kinds, call = {"monthly_digest"}, "mailer.send(s, o, t, 'S', 'h', 't', "
    assert not _unregistered(call + "kind='monthly_digest')", kinds)
    assert not _unregistered("mailer.deliver(k, f, t, 'S', 'h', 't', kind=entry.key)", kinds, entry_key_ok=True)
    assert not _unregistered("from .. import mailer\nmailer.MailError", kinds)
    for wrong in (call + ")",                                                    # no kind
                  call + "kind='made_up')",                                     # not registered
                  "mailer.deliver(k, f, t, 'S', 'h', 't', kind=some_variable)",  # not a literal
                  "mailer.deliver(k, f, t, 'S', 'h', 't', kind=entry.key)",      # entry.key outside the route that looks an entry up
                  "mailer.deliver(k, f, t, 'S', 'h', 't', kind=request.key)",    # another object's key
                  "mailer.deliver(k, f, t, 'S', 'h', 't', **options)",           # kind hidden in a dict
                  "from .mailer import send",
                  "from nyra.cloud.mailer import deliver",
                  "from .. import mailer as m\nm.send(s, o, t, 'S', 'h', 't')",
                  "import nyra.cloud.mailer as mm\nmm.send(s, o, t, 'S', 'h', 't')",
                  "import nyra.cloud.mailer",
                  "getattr(mailer, 'send')(s, o, t, 'S', 'h', 't')",
                  "functools.partial(mailer.send, s, o)(t, 'S', 'h', 't')",
                  "f = mailer.send\nf(s, o, t, 'S', 'h', 't')"):
        assert _unregistered(wrong, kinds, entry_key_ok=wrong.endswith("kind=request.key)")), wrong
    assert _unregistered("mailer.deliver(k, f, t, 'S', 'h', 't', kind=request.key)", kinds, entry_key_ok=True)


def test_the_mailer_refuses_a_call_without_kind():
    with pytest.raises(TypeError):
        mailer.deliver(KEY, SENDER, ["a@b.co"], "S", "h", "t")


# --- the routes -----------------------------------------------------------------------------------------------------------

def _headers(user_id, email="default") -> dict:
    claims = {"sub": str(user_id), "aud": "authenticated", "exp": int(time.time()) + 3600}
    if email:
        claims["email"] = f"{user_id}@x.com" if email == "default" else email
    return {"Authorization": "Bearer " + pyjwt.encode(claims, SECRET, algorithm="HS256")}


@pytest.fixture()
def make_client(cloud_database_url, fake_storage_client):
    def make(key=KEY, sender=SENDER, **options) -> TestClient:
        settings = CloudSettings(database_url=cloud_database_url, supabase_url="https://x.supabase.co", service_role_key="key",
                                 jwt_secret=SECRET, resend_api_key=key, mail_from=sender, public_url="https://nyra.test")
        settings.storage_client = lambda: fake_storage_client
        return TestClient(create_app(settings), **options)
    return make


@pytest.fixture()
def resend(monkeypatch):
    """A fake Resend in place of the network; `resend(500)` / `resend(403)` change its answer. Returns the transport."""
    real = mailer.deliver
    state = {}

    def wire(*answers):
        state["transport"] = _transport(*answers)
        return state["transport"]

    monkeypatch.setattr(mailer, "deliver", lambda *a, **k: real(*a, transport=state["transport"], sleep=lambda s: None, **k))
    wire(200)
    wire.state = state
    return wire


@pytest.fixture()
def staff2(cloud_database_url):
    """A second platform admin (platform_staff is shared by every test: removed after)."""
    user = uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (user, f"{user}@x.com"))
        conn.execute("INSERT INTO platform_staff (user_id) VALUES (%s)", (user,))
    yield user
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("DELETE FROM platform_staff WHERE user_id = %s", (user,))


def audit_rows(url, user, result=None):
    """The `email.test` rows of an account, oldest first; each attempt leaves `started` then `sent` or `failed`."""
    with cloud_db.connect(url) as conn:
        rows = conn.execute("SELECT * FROM audit_log WHERE user_id = %s AND action = 'email.test' ORDER BY created_at, id", (user,)).fetchall()
    return [r for r in rows if result in (None, r["detail"]["result"])]


def test_only_the_nyra_team_uses_the_email_routes(make_client, org_with_users, cloud_database_url, resend):
    client, (org, _, staff, member, outsider) = make_client(), org_with_users
    org_admin = uuid.uuid4()
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (org_admin, f"{org_admin}@x.com"))
        cloud_db.add_membership(conn, user_id=org_admin, org_id=org, role="admin")
    calls = [("GET", "/api/staff/emails"), ("GET", "/api/staff/emails/monthly_digest/preview"), ("POST", "/api/staff/emails/monthly_digest/test")]
    for method, path in calls:
        assert client.request(method, path).status_code == 401, path
        for user in (org_admin, member, outsider):
            assert client.request(method, path, headers=_headers(user)).status_code == 403, (path, user)
        assert client.request(method, path, headers=_headers(staff)).status_code == 200, path


def test_an_unknown_key_is_a_404(make_client, org_with_users):
    client, staff = make_client(), org_with_users[2]
    assert client.get("/api/staff/emails/nope/preview", headers=_headers(staff)).status_code == 404
    assert client.post("/api/staff/emails/nope/test", headers=_headers(staff)).status_code == 404


def test_the_list_says_what_can_be_sent_and_never_shows_the_key(make_client, org_with_users):
    staff = org_with_users[2]
    got = make_client().get("/api/staff/emails", headers=_headers(staff))
    body = got.json()
    assert body["configured"] is True and body["sender"] == SENDER and KEY not in got.text
    rows = {row["key"]: row for row in body["emails"]}
    assert set(rows) == {"monthly_digest", "invite", "recovery"}
    assert {k: (r["sender"], r["can_send_test"]) for k, r in rows.items()} == {
        "monthly_digest": ("nyra", True), "invite": ("supabase", False), "recovery": ("supabase", False)}
    assert all(row["label"] and row["description"] for row in rows.values())
    for key, sender in (("", SENDER), (KEY, ""), ("", "")):
        unconfigured = make_client(key=key, sender=sender).get("/api/staff/emails", headers=_headers(staff)).json()
        assert unconfigured["configured"] is False, (key, sender)


def test_the_preview_is_what_the_render_functions_produce_even_when_sending_is_off(make_client, org_with_users):
    staff = org_with_users[2]
    got = make_client(key="", sender="").get("/api/staff/emails/monthly_digest/preview", headers=_headers(staff))
    mail = digest.build(email_registry.digest_data(date.today()), email_registry.EXAMPLE_URL)
    assert got.status_code == 200 and got.json() == {"subject": mail.subject, "html": mail.html, "text": mail.text}
    invite = make_client().get("/api/staff/emails/invite/preview", headers=_headers(staff)).json()
    assert invite["subject"] == "Votre accès à Nyra" and "{{" not in invite["html"] and invite["text"]


def test_a_test_goes_to_the_signed_in_account_only_with_the_test_marks(make_client, org_with_users, resend):
    staff = org_with_users[2]
    got = make_client().post("/api/staff/emails/monthly_digest/test", headers=_headers(staff, email="Alice@Client.fr"))
    assert got.status_code == 200 and got.json() == {"sent": True, "to": "a***@client.fr"}
    (request,) = resend.state["transport"].seen
    sent = json.loads(request.content)
    assert sent["to"] == ["alice@client.fr"] and sent["from"] == SENDER
    assert sent["subject"].startswith("[TEST] Nyra · Maison Exemple")
    assert BANNER in sent["html"] and sent["html"].index(BANNER) < sent["html"].index("Maison Exemple")
    assert sent["text"].startswith(BANNER) and "Maison Exemple" in sent["text"]
    assert request.headers["authorization"] == f"Bearer {KEY}"


def test_no_other_address_can_be_given_and_an_empty_body_is_fine(make_client, org_with_users, resend):
    client, staff = make_client(), org_with_users[2]
    for body in ({"to": "victim@x.com"}, {"email": "victim@x.com"}, {"extra": 1}):
        assert client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff), json=body).status_code == 422, body
    assert resend.state["transport"].seen == []
    for kwargs in ({}, {"json": {}}):
        assert client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff), **kwargs).status_code == 200
    assert {json.loads(r.content)["to"][0] for r in resend.state["transport"].seen} == {f"{staff}@x.com"}


def test_an_account_without_an_address_gets_a_clear_refusal(make_client, org_with_users, resend, cloud_database_url):
    staff = org_with_users[2]
    got = make_client().post("/api/staff/emails/monthly_digest/test", headers=_headers(staff, email=None))
    assert got.status_code == 400 and "adresse" in got.json()["detail"] and resend.state["transport"].seen == []
    assert audit_rows(cloud_database_url, staff) == []


def test_a_mail_sent_by_supabase_cannot_be_tested(make_client, org_with_users, resend, cloud_database_url):
    client, staff = make_client(), org_with_users[2]
    for key in ("invite", "recovery"):
        got = client.post(f"/api/staff/emails/{key}/test", headers=_headers(staff))
        assert got.status_code == 400 and got.json()["detail"] == NO_MAIL
    assert resend.state["transport"].seen == [] and audit_rows(cloud_database_url, staff) == []


def test_without_configuration_the_test_is_a_503_and_nothing_leaves(make_client, org_with_users, resend, cloud_database_url):
    staff = org_with_users[2]
    for key, sender, word in (("", SENDER, "RESEND_API_KEY"), (KEY, "", "NYRA_MAIL_FROM"), ("", "", "RESEND_API_KEY")):
        got = make_client(key=key, sender=sender).post("/api/staff/emails/monthly_digest/test", headers=_headers(staff))
        assert got.status_code == 503 and word in got.json()["detail"], (key, sender)
    assert resend.state["transport"].seen == [] and audit_rows(cloud_database_url, staff) == []


def test_a_refusal_of_resend_is_a_502_cut_short_without_the_key(make_client, org_with_users, resend, cloud_database_url, caplog):
    staff = org_with_users[2]
    resend(403)
    with caplog.at_level(logging.DEBUG):
        got = make_client().post("/api/staff/emails/monthly_digest/test", headers=_headers(staff))
    detail = got.json()["detail"]
    assert got.status_code == 502 and "The domain is not verified" in detail and KEY not in got.text and len(detail) < 260
    assert KEY not in caplog.text
    assert [r["detail"]["result"] for r in audit_rows(cloud_database_url, staff)] == ["started", "failed"]


def test_a_timeout_is_a_502(make_client, org_with_users, resend):
    import httpx
    staff = org_with_users[2]
    resend(httpx.ConnectTimeout("slow"))
    got = make_client().post("/api/staff/emails/monthly_digest/test", headers=_headers(staff))
    assert got.status_code == 502 and "slow" not in got.text


def test_each_attempt_is_traced_without_address_or_content(make_client, org_with_users, resend, cloud_database_url, caplog):
    client, staff = make_client(), org_with_users[2]
    email = f"{staff}@x.com"
    with caplog.at_level(logging.DEBUG):
        client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff))
        resend(500)
        client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff))
    rows = audit_rows(cloud_database_url, staff)
    assert [(r["detail"], r["org_id"], r["object_type"], r["object_id"]) for r in rows] == [
        ({"kind": "monthly_digest", "result": result}, None, "email", "monthly_digest") for result in ("started", "sent", "started", "failed")]
    assert email not in json.dumps([{k: str(v) for k, v in r.items()} for r in rows]) and KEY not in caplog.text and email not in caplog.text


def test_ten_tests_an_hour_per_account_then_a_429_that_says_when(make_client, org_with_users, resend, staff2, cloud_database_url):
    client, staff = make_client(), org_with_users[2]
    for _ in range(10):
        assert client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff)).status_code == 200
    got = client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff))
    assert got.status_code == 429 and "10" in got.json()["detail"] and "heure" in got.json()["detail"]
    assert 1 <= int(got.headers["retry-after"]) <= 3600 and len(resend.state["transport"].seen) == 10
    assert len(audit_rows(cloud_database_url, staff, "started")) == 10   # a refused test is not an attempt: nothing traced
    assert client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff2)).status_code == 200
    with cloud_db.connect(cloud_database_url) as conn:           # the oldest one leaves the hour: one place is free
        conn.execute("UPDATE audit_log SET created_at = now() - interval '61 minutes' WHERE id = (SELECT id FROM audit_log WHERE user_id = %s AND action = 'email.test' AND detail->>'result' = 'started' ORDER BY created_at LIMIT 1)", (staff,))
    assert client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff)).status_code == 200


def test_failed_attempts_count_in_the_limit_and_retry_after_follows_the_oldest(make_client, org_with_users, resend, cloud_database_url):
    client, staff = make_client(), org_with_users[2]
    resend(403)
    for _ in range(10):
        assert client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff)).status_code == 502
    with cloud_db.connect(cloud_database_url) as conn:
        conn.execute("UPDATE audit_log SET created_at = now() - interval '50 minutes' WHERE user_id = %s AND action = 'email.test'", (staff,))
    wait = int(client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff)).headers["retry-after"])
    assert math.isclose(wait, 600, abs_tol=30)


def test_the_resend_key_is_in_no_answer(make_client, org_with_users, resend):
    client, staff = make_client(), org_with_users[2]
    answers = [client.get(p, headers=_headers(staff)) for p in ("/api/staff/emails", "/api/staff/emails/monthly_digest/preview", "/api/staff/emails/invite/preview")]
    answers.append(client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff)))
    assert all(KEY not in a.text and KEY not in repr(dict(a.headers)) for a in answers)


def test_a_missing_template_file_is_a_503_with_a_sentence(make_client, org_with_users, monkeypatch):
    def missing():
        raise FileNotFoundError("supabase/templates")

    monkeypatch.setattr(email_registry, "_templates_dir", missing)
    got = make_client().get("/api/staff/emails/invite/preview", headers=_headers(org_with_users[2]))
    assert got.status_code == 503 and got.json()["detail"] == "Le gabarit de cet e-mail est introuvable sur le serveur."


def _slow_resend(delay=0.3, status=200):
    """A Resend that takes `delay` seconds to answer and counts the mails it receives."""
    seen = []

    def handler(request):
        time.sleep(delay)
        seen.append(request)
        return httpx.Response(status, json={"id": "abc-123"} if status == 200 else {"name": "internal", "message": "down"})

    transport = httpx.MockTransport(handler)
    transport.seen = seen
    return transport


def _burst(client, user, count):
    """`count` simultaneous test requests of one account: the status codes."""
    with ThreadPoolExecutor(max_workers=count) as pool:
        return sorted(pool.map(lambda _: client.post("/api/staff/emails/monthly_digest/test", headers=_headers(user)).status_code, range(count)))


def test_thirty_simultaneous_tests_send_exactly_ten(make_client, org_with_users, resend, staff2, cloud_database_url):
    client, staff = make_client(), org_with_users[2]
    resend.state["transport"] = transport = _slow_resend()
    assert _burst(client, staff, 30) == [200] * 10 + [429] * 20
    assert len(transport.seen) == 10 and len(audit_rows(cloud_database_url, staff, "started")) == 10
    assert client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff2)).status_code == 200   # another account is not limited


def test_failed_sends_count_in_a_simultaneous_burst_too(make_client, org_with_users, resend):
    client, staff = make_client(), org_with_users[2]
    resend.state["transport"] = transport = _slow_resend(0.1, status=403)
    assert _burst(client, staff, 15) == [429] * 5 + [502] * 10 and len(transport.seen) == 10


def test_an_unexpected_error_after_the_first_row_still_counts(make_client, org_with_users, monkeypatch):
    client, staff = make_client(raise_server_exceptions=False), org_with_users[2]

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mailer, "deliver", broken)
    assert [client.post("/api/staff/emails/monthly_digest/test", headers=_headers(staff)).status_code for _ in range(11)] == [500] * 10 + [429]
