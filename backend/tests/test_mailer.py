"""Sending e-mail through Resend: the request that really leaves, the daily limit, the retries, and the key staying secret.

No network: an `httpx.MockTransport` answers in place of api.resend.com. Only the tests of the daily limit need the database."""

from __future__ import annotations

import json
import logging
import time

import httpx
import pytest

from nyra.cloud import db as cloud_db
from nyra.cloud import mailer, usage
from nyra.cloud.api import CloudSettings
from nyra.cloud.routes.members import EmailLimit

FAKE_SENDING_VALUE = "unit-test-value"
SENDER = "Nyra <nyra@example.com>"


def _transport(*answers):
    """A transport answering `answers` in turn (an int is a status, an Exception is raised); the requests are kept."""
    seen, queue = [], list(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, tuple):  # (status, json body)
            return httpx.Response(answer[0], json=answer[1])
        body = {"id": "abc-123"} if answer == 200 else {"name": "validation_error", "message": "The domain is not verified. " + "x" * 400 + FAKE_SENDING_VALUE}
        return httpx.Response(answer, json=body)

    transport = httpx.MockTransport(handler)
    transport.seen = seen
    return transport


def _deliver(transport, to=("a@b.co",), **kw):
    return mailer.deliver(FAKE_SENDING_VALUE, SENDER, list(to), "Sujet", "<p>Salut</p>", "Salut", transport=transport, sleep=lambda s: None, **kw)


def _settings(url="postgresql://x:x@db.invalid/none", key=FAKE_SENDING_VALUE, sender=SENDER):
    return CloudSettings(database_url=url, supabase_url="https://x.supabase.co", service_role_key="k", jwt_secret=None,
                         resend_api_key=key, mail_from=sender)


def _send(settings, org_id, transport, to=("a@b.co",), **kw):
    return mailer.send(settings, org_id, list(to), "S", "<p>h</p>", "t", transport=transport, sleep=lambda s: None, **kw)


# --- the request ---------------------------------------------------------------------------

def test_the_request_sent_to_resend_is_the_documented_one():
    transport = _transport(200)
    assert _deliver(transport, to=["A@B.co", "a@b.co", "c@d.co"]) == "abc-123"
    (request,) = transport.seen
    assert request.method == "POST" and str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == f"Bearer {FAKE_SENDING_VALUE}"
    assert json.loads(request.content) == {"from": SENDER, "to": ["a@b.co", "c@d.co"], "subject": "Sujet", "html": "<p>Salut</p>", "text": "Salut"}
    assert request.headers["idempotency-key"]


def test_a_retry_keeps_the_same_idempotency_key_so_resend_never_sends_twice():
    transport = _transport(500, 200)
    _deliver(transport)
    assert len({r.headers["idempotency-key"] for r in transport.seen}) == 1 and len(transport.seen) == 2


# --- robustness ----------------------------------------------------------------------------

def test_5xx_then_success_on_the_second_try():
    transport = _transport(503, 200)
    assert _deliver(transport) == "abc-123" and len(transport.seen) == 2


def test_429_is_retried_too():
    assert _deliver(_transport(429, 200)) == "abc-123"


def test_three_5xx_in_a_row_fail_after_exactly_three_calls():
    transport = _transport(500)
    with pytest.raises(mailer.MailError):
        _deliver(transport)
    assert len(transport.seen) == 3


def test_a_4xx_fails_at_once_with_resends_message_cut_and_without_the_key():
    transport = _transport(422)
    with pytest.raises(mailer.MailError) as caught:
        _deliver(transport)
    assert len(transport.seen) == 1
    message = str(caught.value)
    assert "The domain is not verified" in message and len(message) <= 260 and FAKE_SENDING_VALUE not in message


def test_a_timeout_is_retried_then_reported_without_the_key():
    transport = _transport(httpx.ReadTimeout("slow " + FAKE_SENDING_VALUE))
    with pytest.raises(mailer.MailError) as caught:
        _deliver(transport)
    assert len(transport.seen) == 3 and FAKE_SENDING_VALUE not in str(caught.value) and "ReadTimeout" in str(caught.value)


def test_the_network_call_has_a_short_timeout_and_few_attempts():
    assert 0 < mailer.TIMEOUT_SECONDS <= 15 and mailer.ATTEMPTS == 3


def test_the_key_is_in_no_log_and_no_address_is_logged_in_full(caplog):
    caplog.set_level(logging.DEBUG)
    _deliver(_transport(200), to=["alice@client.fr"])
    with pytest.raises(mailer.MailError):
        _deliver(_transport(500), to=["alice@client.fr"])
    assert caplog.text and FAKE_SENDING_VALUE not in caplog.text and "alice@client.fr" not in caplog.text and "a***@client.fr" in caplog.text


# --- what is checked before anything leaves -----------------------------------------------

@pytest.mark.parametrize("to", [[], ["pas-un-mail"], ["a b@c.co"], [f"u{i}@x.co" for i in range(11)], [""], ["a@b.co\r\nBcc: x@y.co"]])
def test_bad_recipients_are_refused_before_any_call(to):
    transport = _transport(200)
    with pytest.raises(ValueError):
        _deliver(transport, to=to)
    assert not transport.seen


def test_duplicates_count_once_so_ten_distinct_addresses_pass():
    transport = _transport(200)
    _deliver(transport, to=[f"u{i}@x.co" for i in range(10)] + ["U1@x.co"])
    assert len(json.loads(transport.seen[0].content)["to"]) == 10


def test_subject_and_bodies_are_bounded():
    for subject, html, text in (("s" * 201, "h", "t"), ("s", "h" * 200_001, "t"), ("s", "h", "t" * 200_001), ("", "h", "t"), ("a\nb", "h", "t"), ("a\rb", "h", "t")):
        transport = _transport(200)
        with pytest.raises(ValueError):
            mailer.deliver(FAKE_SENDING_VALUE, SENDER, ["a@b.co"], subject, html, text, transport=transport)
        assert not transport.seen


@pytest.mark.parametrize("value", ["Nyra <nyra@example.com>", "nyra@example.com", "  Nyra  <nyra@example.com> "])
def test_a_valid_sender_is_kept(value):
    assert mailer.parse_sender(value) == value.strip()


@pytest.mark.parametrize("value", ["nyra", "Nyra <nyra>", "Nyra nyra@example.com", "<>", "a@b.co\nBcc: x@y.co", "Nyra <a@b.co> <c@d.co>"])
def test_a_bad_sender_gives_a_clear_message(value):
    with pytest.raises(ValueError, match="NYRA_MAIL_FROM"):
        mailer.parse_sender(value)


def _env(monkeypatch, key, sender):
    from nyra.cloud.api import settings_from_env

    for name, value in (("DATABASE_URL", "postgresql://x"), ("SUPABASE_URL", "https://x"), ("SUPABASE_SERVICE_ROLE_KEY", "k"),
                        ("RESEND_API_KEY", key), ("NYRA_MAIL_FROM", sender)):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("nyra.envfile.load_env_files", lambda *a, **k: None)
    return settings_from_env()


def test_both_variables_make_the_mail_ready(monkeypatch):
    settings = _env(monkeypatch, FAKE_SENDING_VALUE, SENDER)
    assert settings.resend_api_key == FAKE_SENDING_VALUE and settings.mail_from == SENDER and FAKE_SENDING_VALUE not in repr(settings)


def test_a_key_alone_starts_with_one_warning_and_the_mail_stays_unconfigured(monkeypatch, caplog):
    settings = _env(monkeypatch, FAKE_SENDING_VALUE, "")
    assert settings.mail_from == "" and caplog.text.count("NYRA_MAIL_FROM") == 1 and FAKE_SENDING_VALUE not in caplog.text
    with pytest.raises(mailer.MailNotConfigured):
        mailer.send(settings, None, ["a@b.co"], "S", "h", "t")


def test_a_sender_alone_starts_and_the_mail_stays_unconfigured(monkeypatch):
    settings = _env(monkeypatch, "", SENDER)
    assert settings.resend_api_key == ""
    with pytest.raises(mailer.MailNotConfigured):
        mailer.send(settings, None, ["a@b.co"], "S", "h", "t")


def test_a_mistyped_sender_stops_the_start_with_a_clear_message(monkeypatch):
    with pytest.raises(RuntimeError, match="NYRA_MAIL_FROM"):
        _env(monkeypatch, FAKE_SENDING_VALUE, "nyra")
    with pytest.raises(RuntimeError, match="NYRA_MAIL_FROM"):
        _env(monkeypatch, "", "nyra")


# --- idempotency, 409, total deadline -------------------------------------------------------

def test_a_given_idempotency_key_is_sent_as_is_and_is_the_same_on_every_try():
    transport = _transport(500, 500, 200)
    _deliver(transport, idempotency_key="monthly:org-1:2026-10")
    assert [r.headers["idempotency-key"] for r in transport.seen] == ["monthly:org-1:2026-10"] * 3


@pytest.mark.parametrize("key", ["", "x" * 257, "a\nb", "tab\there", "ééé"])
def test_a_bad_idempotency_key_is_refused_before_any_call(key):
    transport = _transport(200)
    with pytest.raises(ValueError, match="idempotency"):
        _deliver(transport, idempotency_key=key)
    assert not transport.seen


def test_the_longest_idempotency_key_passes():
    assert _deliver(_transport(200), idempotency_key="k" * 256) == "abc-123"


def test_a_concurrent_idempotent_request_is_retried_then_fails_explicitly():
    busy = (409, {"name": "concurrent_idempotent_requests", "message": "Same idempotency key is being processed"})
    assert _deliver(_transport(busy, 200)) == "abc-123"
    transport = _transport(busy)
    with pytest.raises(mailer.MailError, match="409"):
        _deliver(transport)
    assert len(transport.seen) == 3


def test_another_409_is_not_retried():
    transport = _transport((409, {"name": "invalid_idempotent_request", "message": "different payload"}))
    with pytest.raises(mailer.MailError, match="different payload"):
        _deliver(transport)
    assert len(transport.seen) == 1


def _drip(chunks, pause):
    """An answer whose body arrives in `chunks` small pieces, `pause` seconds apart (each read is quicker than any timeout)."""
    def body():
        for _ in range(chunks):
            time.sleep(pause)
            yield b" "
    return httpx.MockTransport(lambda request: httpx.Response(200, content=body()))


def test_a_slowly_dripped_answer_is_cut_by_the_total_deadline_not_only_by_the_per_read_timeout():
    start = time.monotonic()
    with pytest.raises(mailer.MailError):
        _deliver(_drip(100, 0.05), deadline_seconds=0.5)  # 5 s of dripping, 3 tries
    assert time.monotonic() - start < 2.0


def test_the_deadline_also_stops_the_retries():
    transport = _transport(500)
    with pytest.raises(mailer.MailError):
        _deliver(transport, deadline_seconds=0.5)  # the wait of 1 s before the 2nd try would pass the deadline
    assert len(transport.seen) == 1


def test_a_refusal_that_repeats_the_key_is_masked():
    with pytest.raises(mailer.MailError) as caught:
        _deliver(_transport((401, {"message": f"API key {FAKE_SENDING_VALUE} is invalid"})))
    assert FAKE_SENDING_VALUE not in str(caught.value) and "***" in str(caught.value)


# --- the template --------------------------------------------------------------------------

def test_render_escapes_every_value_in_the_html_and_leaves_the_text_plain():
    html, text = mailer.render("Alerte <b>Hennessy</b>", ["Fichier \"<script>alert(1)</script>\".png & co"])
    assert "<script>" not in html and "&lt;script&gt;" in html and "&amp; co" in html and "<b>Hennessy" not in html
    assert text == "Alerte <b>Hennessy</b>\n\nFichier \"<script>alert(1)</script>\".png & co"


# --- the daily limit (needs the database) --------------------------------------------------

def _sent_today(url, org_id):
    with cloud_db.connect(url) as conn:
        row = conn.execute("SELECT amount FROM usage_counters WHERE org_id = %s AND period = %s AND kind = 'emails'", (org_id, usage.today())).fetchone()
    return row["amount"] if row else 0


def _limit(url, org_id, per_day):
    with cloud_db.connect(url) as conn:
        usage.set_overrides(conn, org_id, {"emails_per_day": per_day})


def test_not_configured_is_an_explicit_error_and_touches_nothing(cloud_database_url, cloud_org):
    transport = _transport(200)
    for settings in (_settings(cloud_database_url, key=""), _settings(cloud_database_url, sender="")):
        with pytest.raises(mailer.MailNotConfigured):
            _send(settings, cloud_org, transport)
    assert not transport.seen and _sent_today(cloud_database_url, cloud_org) == 0


def test_a_success_uses_one_place_of_the_day(cloud_database_url, cloud_org):
    assert _send(_settings(cloud_database_url), cloud_org, _transport(200), to=["a@b.co", "c@d.co"]) == "abc-123"
    assert _sent_today(cloud_database_url, cloud_org) == 1


def test_the_limit_stops_the_send_before_any_network_call(cloud_database_url, cloud_org):
    _limit(cloud_database_url, cloud_org, 1)
    transport = _transport(200)
    settings = _settings(cloud_database_url)
    _send(settings, cloud_org, transport)
    with pytest.raises(EmailLimit):
        _send(settings, cloud_org, transport)
    assert len(transport.seen) == 1 and _sent_today(cloud_database_url, cloud_org) == 1


def test_a_kill_signal_between_the_claim_and_the_end_gives_the_place_back_too(cloud_database_url, cloud_org):
    with pytest.raises(SystemExit):
        _send(_settings(cloud_database_url), cloud_org, _transport(SystemExit()))
    assert _sent_today(cloud_database_url, cloud_org) == 0


def test_a_bad_idempotency_key_never_takes_a_place(cloud_database_url, cloud_org):
    with pytest.raises(ValueError):
        _send(_settings(cloud_database_url), cloud_org, _transport(200), idempotency_key="")
    assert _sent_today(cloud_database_url, cloud_org) == 0


@pytest.mark.parametrize("answers", [(422,), (500,), (httpx.ConnectError("down"),)])
def test_a_failure_gives_the_place_back(cloud_database_url, cloud_org, answers):
    with pytest.raises(mailer.MailError):
        _send(_settings(cloud_database_url), cloud_org, _transport(*answers))
    assert _sent_today(cloud_database_url, cloud_org) == 0


def test_invalid_input_never_takes_a_place(cloud_database_url, cloud_org):
    with pytest.raises(ValueError):
        _send(_settings(cloud_database_url), cloud_org, _transport(200), to=["nope"])
    assert _sent_today(cloud_database_url, cloud_org) == 0
