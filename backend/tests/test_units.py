"""Pure-function tests for the pieces added around the pipeline:
strict expiry parsing, per-organization settings, mirror-image matching,
report grouping (false positives never reach the client), and the image
decoder's refusal of decompression bombs. No database, no network.
"""

from __future__ import annotations

import io
from datetime import date

import pytest
from PIL import Image, ImageDraw, ImageOps

from nyra import fetch, report
from nyra.config import Config, validate_overrides, with_overrides
from nyra.match import compare, compute_flip_hashes, compute_hashes, pack
from nyra.refs import RefValidationError, parse_expiry

# --- expiry dates -------------------------------------------------------------

def test_parse_expiry_accepts_iso_and_day_first():
    assert parse_expiry("2027-02-01") == "2027-02-01"
    assert parse_expiry("01/02/2027") == "2027-02-01"  # 1 February, never 2 January
    assert parse_expiry("1.2.2027") == "2027-02-01"
    assert parse_expiry("  ") is None


def test_parse_expiry_refuses_month_first_instead_of_guessing():
    with pytest.raises(RefValidationError, match="JJ/MM/AAAA"):
        parse_expiry("12/31/2026")
    with pytest.raises(RefValidationError):
        parse_expiry("next year")


# --- organization settings ----------------------------------------------------------

def test_overrides_are_whitelisted_and_capped():
    base = Config()
    config = with_overrides(base, {"crawl": {"max_pages": 99999, "user_agent": "evil"}, "match": {"phash_threshold": "6"}})
    assert config.crawl.max_pages == base.crawl.max_pages_limit
    assert config.crawl.user_agent == base.crawl.user_agent
    assert config.match.phash_threshold == 6


def test_overrides_reject_inconsistent_thresholds():
    with pytest.raises(ValueError):
        validate_overrides({"match": {"clip_similarity_floor": 0.95, "clip_similarity_high": 0.9}})
    with pytest.raises(ValueError):
        validate_overrides({"crawl": {"delay_seconds_min": 0.1}})


# --- mirror matching -------------------------------------------------------------------

def _photo() -> Image.Image:
    img = Image.new("RGB", (320, 240), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 140, 230], fill=(200, 30, 30))
    draw.ellipse([180, 60, 300, 180], fill=(20, 40, 200))
    draw.line([(0, 239), (319, 0)], fill=(0, 0, 0), width=6)
    return img


def test_flipped_reuse_matches_through_mirror_hashes():
    ref = _photo()
    flipped = ImageOps.mirror(ref)
    config = Config().match
    p, d = compute_hashes(ref)
    pf, df = compute_flip_hashes(ref)
    sp, sd = compute_hashes(flipped)
    site = pack([{"id": "s", "phash": sp, "dhash": sd, "embedding": None}], use_clip=False)

    without_flip = pack([{"id": "r", "phash": p, "dhash": d, "embedding": None}], use_clip=False, with_flip=True)
    assert compare(without_flip, site, config, use_clip=False) == []

    with_flip = pack([{"id": "r", "phash": p, "dhash": d, "phash_flip": pf, "dhash_flip": df, "embedding": None}],
                     use_clip=False, with_flip=True)
    hits = compare(with_flip, site, config, use_clip=False)
    assert [(h[0], h[1], h[4]) for h in hits] == [("r", "s", "haut")]


# --- report -------------------------------------------------------------------------

def _row(ref_id, site_id, decision=None, *, confidence="haut", expiry="2020-01-01", content_hash=None):
    return {
        "reference_id": ref_id, "filename": f"{ref_id}.jpg", "expiry_date": expiry, "credit": "", "notes": "",
        "site_image_id": site_id, "site_url": f"https://ex.com/{site_id}.jpg", "content_hash": content_hash,
        "level": "phash", "score": 0.95, "confidence": confidence, "decision": decision,
        "pages": [f"https://ex.com/page-{site_id}"],
    }


def test_group_matches_merges_cdn_variants_and_sorts_by_urgency():
    today = date(2026, 1, 1)
    rows = [
        _row("late", "a", content_hash="h1", expiry="2026-06-01"),
        _row("early", "b", content_hash="h2", expiry="2025-12-01"),
        _row("early", "c", content_hash="h2", expiry="2025-12-01"),
    ]
    grouped = report.group_matches(rows, 365, today)
    assert [g["reference_id"] for g in grouped["confirmed"]] == ["early", "late"]
    early = grouped["confirmed"][0]
    assert len(early["hits"]) == 1 and sorted(early["hits"][0]["site_image_ids"]) == ["b", "c"]
    assert early["status"] == report.STATUS_EXPIRED


def test_report_leaves_out_false_positives():
    rows = [_row("kept", "a"), _row("dismissed", "b", decision=report.DECISION_FALSE_POSITIVE)]
    files = report.build_report(rows, [], within_days=90, stats={}, thumb_loader=lambda key: None,
                                today=date(2026, 1, 1))
    html = files.html.decode("utf-8")
    assert "kept.jpg" in html and "dismissed.jpg" not in html
    assert b"dismissed.jpg" not in files.matches_csv
    assert "Expiré" in html  # accents survive the round trip


def test_dashboard_counts_only_live_occurrences():
    today = date(2026, 1, 1)
    rows = [
        _row("expired-live", "a"),
        _row("expired-removed", "b", decision=report.DECISION_REMOVED),
        _row("soon", "c", expiry="2026-01-10", decision=report.DECISION_REMOVE),
    ]
    summary = report.dashboard(rows, [], today)
    assert summary["expired_online"] == 1
    assert summary["urgent_online"] == 1
    assert summary["pending_review"] == 1
    assert summary["upcoming"][0]["reference_id"] == "soon"


# --- decoding -----------------------------------------------------------------------

def test_decode_refuses_decompression_bombs_and_garbage():
    buf = io.BytesIO()
    Image.new("L", (6000, 6000)).save(buf, format="PNG")
    assert fetch.decode(buf.getvalue(), max_pixels=1_000_000) is None
    assert fetch.decode(b"not an image", max_pixels=1_000_000) is None
    small = io.BytesIO()
    _photo().save(small, format="JPEG")
    processed = fetch.process_image(small.getvalue(), "image/jpeg", min_side_px=200, max_pixels=10_000_000)
    assert processed is not None and processed.extension == ".jpg"
    assert Image.open(io.BytesIO(processed.thumbnail)).size[0] <= fetch.THUMB_SIZE


# --- exclusions -----------------------------------------------------------------------

def test_exclusions_drop_near_copies_and_change_the_signature(tmp_path):
    from nyra import db
    from nyra.match import excluded_site_ids, run_matching, signature

    config = Config()
    logo = "ffff0000ffff0000"
    near = "ffff0000ffff0001"  # one bit away: a re-encoded copy
    other = "0f0f0f0f0f0f0f0f"
    sites = [{"id": 1, "phash": near, "dhash": other}, {"id": 2, "phash": other, "dhash": other}]
    assert excluded_site_ids(sites, [(logo, "phash")], config.match) == {1}
    assert signature(config.match, False, [(logo, "phash")]) != signature(config.match, False, [])

    db_path = tmp_path / "x.db"
    db.init_db(db_path)
    with db.connect(db_path) as conn:
        db.upsert_reference_image(conn, filename="logo.png", path="logo.png", expiry_date=None, credit=None,
                                  notes=None, phash=logo, dhash=logo)
        db.upsert_site_image(conn, url="https://ex.com/logo.png", phash=logo, dhash=logo)
    assert run_matching(db_path, Config(), use_clip=False) == 1
    with db.connect(db_path) as conn:
        conn.execute("INSERT INTO excluded_hashes (hash, hash_type, created_at) VALUES (?, 'phash', 'now')", (logo,))
        conn.execute("INSERT INTO excluded_hashes (hash, hash_type, created_at) VALUES (?, 'dhash', 'now')", (logo,))
    assert run_matching(db_path, Config(), use_clip=False) == 0
    with db.connect(db_path) as conn:
        conn.execute("DELETE FROM excluded_hashes")
    assert run_matching(db_path, Config(), use_clip=False) == 1


def test_reference_decoding_scales_big_jpegs_down_and_says_why_it_refuses():
    import io as _io

    from PIL import Image as _Image

    from nyra import fetch as _fetch

    def encoded(size, fmt):
        buf = _io.BytesIO()
        _Image.new("RGB", size, (200, 30, 30)).save(buf, format=fmt)
        return buf.getvalue()

    img, reason = _fetch.decode_reference(encoded((4000, 3000), "JPEG"), 6_000_000)
    assert reason == "" and img.info["original_size"] == (4000, 3000)
    assert img.size[0] * img.size[1] <= 6_000_000
    img, reason = _fetch.decode_reference(encoded((4000, 3000), "PNG"), 6_000_000)
    assert img is None and reason.startswith("Image trop grande : 4000 × 3000 px")
    img, reason = _fetch.decode_reference(encoded((400, 300), "JPEG")[:500], 6_000_000)
    assert img is None and reason.startswith("Fichier incomplet ou endommagé")
    img, reason = _fetch.decode_reference(b"not an image", 6_000_000)
    assert img is None and reason.startswith("Format non reconnu")


def test_a_pass_that_could_not_check_clip_candidates_does_not_look_like_one_that_did():
    from nyra.config import MatchConfig as _MatchConfig
    from nyra.match import signature as _signature

    config = _MatchConfig()
    assert _signature(config, True, verified=False) != _signature(config, True, verified=True)
