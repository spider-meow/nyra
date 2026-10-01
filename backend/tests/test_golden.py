"""Pins on exact outputs: the match list of `run_matching`, the call pattern of
`verify_hits` and the bytes of `build_report`.

They were recorded on the code before `run_matching`, `verify_hits` and
`build_report` were split into steps, and must stay identical: a refactor
changes no result. No network, no CLIP model and no OpenCV: embeddings are
generated vectors stored the way the crawler stores them, and the keypoint
check is replaced by a deterministic stand-in (the real one has its own
tests in test_verify.py, which need OpenCV).
"""

from __future__ import annotations

import hashlib
import io
import random
import re
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageOps

from nyra import db, report, verify
from nyra.config import MatchConfig, load_config
from nyra.match import MatchStopped, compute_flip_hashes, compute_hashes, run_matching


def scene(seed: int, size: int = 192) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", (size, size), tuple(rng.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    for _ in range(30):
        x, y = rng.randrange(size), rng.randrange(size)
        draw.rectangle([x, y, x + rng.randrange(10, 90), y + rng.randrange(10, 90)],
                       fill=tuple(rng.randrange(256) for _ in range(3)))
    return img


def jpeg(img: Image.Image, quality: int = 60) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def banner(img: Image.Image) -> Image.Image:
    img = img.copy()
    ImageDraw.Draw(img).rectangle([0, 0, 192, 60], fill=(255, 255, 255))
    return img


def near(vector: np.ndarray, sigma: float, seed: int) -> np.ndarray:
    noisy = vector + np.random.default_rng(seed).normal(0, sigma, vector.shape)
    return (noisy / np.linalg.norm(noisy)).astype(np.float32)


def unit(seed: int) -> np.ndarray:
    vector = np.random.default_rng(seed).normal(0, 1, 16)
    return (vector / np.linalg.norm(vector)).astype(np.float32)


# --- run_matching ---------------------------------------------------------------

def _add_ref(conn, tmp_path: Path, name: str, image: Image.Image, embedding, expiry) -> int:
    path = tmp_path / f"ref_{name}.png"
    image.save(path)
    (phash, dhash), (pflip, dflip) = compute_hashes(image), compute_flip_hashes(image)
    return db.upsert_reference_image(conn, filename=f"{name}.jpg", path=str(path), expiry_date=expiry, credit=None,
                                     notes=None, phash=phash, dhash=dhash, phash_flip=pflip, dhash_flip=dflip,
                                     embedding=embedding)


def _add_site(conn, tmp_path: Path, name: str, image: Image.Image, embedding) -> int:
    path = tmp_path / f"site_{name}.png"
    image.save(path)
    phash, dhash = compute_hashes(image)
    return db.upsert_site_image(conn, url=f"https://ex.com/{name}.jpg", local_path=str(path), phash=phash,
                                dhash=dhash, embedding=embedding)


def _dump(db_path: Path) -> list[tuple]:
    with db.connect(db_path) as conn:
        rows = [(m["filename"], m["site_url"], m["level"], round(m["score"], 6), m["confidence"])
                for m in db.get_matches(conn)]
    return sorted(rows)


def _pass(db_path: Path, config, label: str, out: list, **kwargs) -> None:
    stats: dict = {}
    steps: list = []
    count = run_matching(db_path, config, progress=lambda done, total: steps.append((done, total)), stats=stats, **kwargs)
    stats = {key: value for key, value in stats.items() if not key.endswith("_seconds")}
    out.append((label, count, stats, steps, _dump(db_path)))


def run_scenario(tmp_path: Path) -> list:
    """Full pass, incremental pass, no-op pass, tightened thresholds, an exclusion."""
    db_path = tmp_path / "g.db"
    db.init_db(db_path)
    e = {i: unit(i) for i in range(1, 5)}
    with db.connect(db_path) as conn:
        _add_ref(conn, tmp_path, "r1", scene(1), e[1], "2026-02-01")
        _add_ref(conn, tmp_path, "r2", scene(2), e[2], "2026-01-01")
        _add_ref(conn, tmp_path, "r3", scene(3), e[3], None)
        _add_site(conn, tmp_path, "copy1", jpeg(ImageOps.autocontrast(scene(1).resize((120, 120)), cutoff=10), 30),
                  near(e[1], 0.05, 10))
        _add_site(conn, tmp_path, "flip2", ImageOps.mirror(scene(2)), near(e[2], 0.05, 11))
        _add_site(conn, tmp_path, "crop1", scene(1).crop((10, 10, 180, 180)), near(e[1], 0.07, 12))
        _add_site(conn, tmp_path, "banner1", banner(scene(1)), near(e[1], 0.07, 12))
        _add_site(conn, tmp_path, "mid2", scene(6), near(e[2], 0.13, 13))
        _add_site(conn, tmp_path, "low3", scene(7), near(e[3], 0.17, 16))
        _add_site(conn, tmp_path, "far4", scene(4), near(e[4], 0.05, 15))
        _add_site(conn, tmp_path, "noembed", scene(8), None)
    out: list = []
    config = load_config()
    _pass(db_path, config, "full", out, use_clip=True)
    with db.connect(db_path) as conn:
        _add_ref(conn, tmp_path, "r4", scene(4), e[4], "2026-03-01")
        _add_site(conn, tmp_path, "new3", jpeg(scene(3).resize((100, 100))), near(e[3], 0.04, 16))
    _pass(db_path, config, "incremental", out, use_clip=True)
    _pass(db_path, config, "nothing new", out, use_clip=True)
    _pass(db_path, config, "hash only", out, use_clip=False)
    with db.connect(db_path) as conn:
        phash = conn.execute("SELECT phash FROM site_images WHERE url LIKE '%copy1%'").fetchone()["phash"]
        conn.execute("INSERT INTO excluded_hashes (hash, hash_type, created_at) VALUES (?, 'phash', 'now')", (phash,))
    _pass(db_path, config, "excluded", out, use_clip=True)
    return out


def test_run_matching_results_are_pinned(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "available", lambda: False)  # hash and CLIP levels only, whether OpenCV is installed or not
    out = run_scenario(tmp_path)
    assert [(label, count, stats["full"], stats["pairs"], stats["hits"], stats["excluded"])
            for label, count, stats, _steps, _rows in out] == EXPECTED_PASSES
    assert {row[2] for row in out[0][4]} == {"phash", "dhash", "clip"}
    assert {row[4] for row in out[0][4]} == {"haut", "moyen", "a_verifier"}  # every CLIP band is exercised
    assert out[0][4] == EXPECTED_FULL


def test_run_matching_stops_before_writing(tmp_path):
    db_path = tmp_path / "s.db"
    db.init_db(db_path)
    with db.connect(db_path) as conn:
        _add_ref(conn, tmp_path, "r1", scene(1), None, None)
        _add_site(conn, tmp_path, "s1", scene(1), None)
    with pytest.raises(MatchStopped):
        run_matching(db_path, load_config(), use_clip=False, should_stop=lambda: True)
    assert _dump(db_path) == []


# --- run_matching + the keypoint check ---------------------------------------------

def _fake_extract(gray, *, mirror=False):
    return int(np.asarray(gray).mean()), mirror


def _fake_compare(a, b, config):
    tier = (None, verify.SAME, verify.REVIEW, None)[(a[0] * 7 + b[0] * 3 + a[1]) % 4]
    return verify.Verdict(tier, 30 if tier else 0, 0.3 if tier else 0.0)


class FileStore:
    """The SQLite store plus `load_image`, which reads the generated files (ids follow the insertion order)."""

    def __init__(self, db_path: Path, tmp_path: Path):
        self.inner, self.tmp_path = db.LocalStore(db_path), tmp_path

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def load_image(self, side: str, image_id: int) -> Image.Image:
        name = f"ref_r{image_id}" if side == "ref" else f"site_s{image_id}"
        return Image.open(self.tmp_path / f"{name}.png")


def run_verified(tmp_path: Path, monkeypatch) -> tuple:
    monkeypatch.setattr(verify, "available", lambda: True)
    monkeypatch.setattr(verify, "extract", _fake_extract)
    monkeypatch.setattr(verify, "compare", _fake_compare)
    db_path = tmp_path / "v.db"
    db.init_db(db_path)
    with db.connect(db_path) as conn:
        for i in range(1, 4):
            _add_ref(conn, tmp_path, f"r{i}", scene(i), unit(i), None)
        for i in range(1, 10):
            _add_site(conn, tmp_path, f"s{i}", scene(20 + i), near(unit(1 + i % 3), 0.04, i))
    steps: list = []
    stats: dict = {}
    count = run_matching(FileStore(db_path, tmp_path), load_config(), use_clip=True, verify_progress=lambda *a: steps.append(a), stats=stats)
    return count, stats["verified_candidates"], stats["hits_by_level"], steps, _dump(db_path)


def test_run_matching_with_the_keypoint_check_is_pinned(tmp_path, monkeypatch):
    assert run_verified(tmp_path, monkeypatch) == EXPECTED_VERIFIED


# --- verify_hits ------------------------------------------------------------------------

def run_verify_hits(monkeypatch) -> tuple:
    """A reference and a site image are loaded and their keypoints read once while they stay in their LRU (the
    reference mirror once); an unreadable image is remembered; results keep their order."""
    log: list = []

    def load(side, image_id):
        log.append(("load", side, image_id))
        return None if image_id == 9 else Image.new("L", (10 + (side == "site") * 100 + image_id, 8))

    def extract(gray, *, mirror=False):
        log.append(("extract", gray.size[0], mirror))
        return gray.size[0], mirror

    def compare(a, b, config):
        log.append(("compare", a, b))
        return _fake_compare(a, b, config)

    monkeypatch.setattr(verify, "extract", extract)
    monkeypatch.setattr(verify, "compare", compare)
    hits = [(1, 1, "clip", 0.9, "haut"), (1, 2, "phash", 0.99, "haut"), (2, 1, "clip", 0.8, "a_verifier"),
            (2, 2, "clip", 0.8, "moyen"), (1, 9, "clip", 0.77, "haut"), (9, 1, "clip", 0.76, "haut"),
            (3, 3, "clip", 0.95, "haut"), (1, 3, "clip", 0.85, "moyen")]
    names = dict(level_clip="clip", level_verified="geo", confidence_high="haut", confidence_to_verify="a_verifier")
    steps: list = []
    kept = verify.verify_hits(hits, load, MatchConfig(), progress=lambda *a: steps.append(a), **names)
    calls = {"count": 0}

    def stop_second():
        calls["count"] += 1
        return calls["count"] > 1

    stopped = verify.verify_hits(hits, load, MatchConfig(), should_stop=stop_second, **names)
    only_level_one = verify.verify_hits([(1, 2, "phash", 0.9, "haut")], load, MatchConfig(), **names)
    return kept, steps, log, stopped, only_level_one


def test_verify_hits_calls_and_results_are_pinned(monkeypatch):
    assert run_verify_hits(monkeypatch) == EXPECTED_VERIFY_HITS


# --- build_report ------------------------------------------------------------------------

def _png(color, mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (400, 300), color).save(buf, format="PNG")
    return buf.getvalue()


def _thumbs(key):
    if key == "boom":
        raise OSError("disk gone")
    return {"missing": None, "garbage": b"not an image", "alpha": _png((10, 20, 30, 128), "RGBA")}.get(key) or _png((200, 30, 30))


def _hit(site_id, *, score=0.95, confidence="haut", decision=None, content_hash=None, pages=(), thumb="s"):
    return {"site_image_id": site_id, "site_url": f"https://ex.com/{site_id}.jpg", "content_hash": content_hash,
            "level": "phash", "score": score, "confidence": confidence, "decision": decision, "pages": list(pages),
            "site_thumb": thumb}


def _row(ref_id, hit, *, expiry, credit="", notes="", thumb="r"):
    return {"reference_id": ref_id, "filename": f"{ref_id}.jpg", "expiry_date": expiry, "credit": credit, "notes": notes,
            "ref_thumb": thumb, **hit}


REPORT_ROWS = [
    _row("a-expired", _hit(1, score=0.91, content_hash="h", pages=[f"https://ex.com/p{i}" for i in range(8)]), expiry="2025-12-01"),
    _row("a-expired", _hit(2, score=0.97, content_hash="h", pages=["https://ex.com/p0", "https://ex.com/extra"]), expiry="2025-12-01"),
    _row("a-expired", _hit(3, score=0.5, confidence="a_verifier", thumb="alpha"), expiry="2025-12-01"),
    _row("b-soon", _hit(4, score=0.88, confidence="moyen", thumb="missing"), expiry="2026-01-20", thumb="garbage"),
    _row("c-verify", _hit(5, score=0.8, confidence="a_verifier", thumb="boom"), expiry="2026-03-01"),
    _row("d-later", _hit(6), expiry="2027-06-01"),
    _row("e-none", _hit(7, decision="traite", pages=["https://ex.com/é?a=1&b=2"]), expiry=None,
         credit='Photo "X", Paris\nline2', notes="a;b <i>"),
    _row("f-dismissed", _hit(8, decision="ecarte"), expiry="2025-11-01"),
    _row("É-accent", _hit(9, decision="retenu", score=0.123456), expiry="2026-01-15", credit="Zoë"),
]
REPORT_UNMATCHED = [
    {"reference_id": "g", "filename": "g.jpg", "expiry_date": "2026-01-05", "credit": "", "notes": "x", "compared": True, "ref_thumb": "r"},
    {"reference_id": "h", "filename": "h.jpg", "expiry_date": None, "credit": None, "notes": None, "compared": False, "ref_thumb": None},
    {"reference_id": "i", "filename": "i.jpg", "expiry_date": "2030-01-01", "credit": "", "notes": "", "compared": True, "ref_thumb": "r"},
]


def make_report():
    return report.build_report(REPORT_ROWS, REPORT_UNMATCHED, within_days=90, stats={"reference_images": 9, "site_images": 40},
                               thumb_loader=_thumbs, organization="Rémy & Co <test>", today=date(2026, 1, 1))


def normalized_html(files) -> str:
    """The generation time and the JPEG bytes of the thumbnails depend on the clock and the Pillow build."""
    html = files.html.decode("utf-8")
    html = re.sub(r"Généré le [^<]*<", "Généré le X<", html)
    return re.sub(r"data:image/jpeg;base64,[A-Za-z0-9+/=]+", "data:thumb", html)


def test_build_report_output_is_pinned():
    files = make_report()
    assert files.matches_csv.decode("utf-8") == EXPECTED_MATCHES_CSV
    assert files.not_found_csv.decode("utf-8") == EXPECTED_NOT_FOUND_CSV
    assert files.summary == EXPECTED_SUMMARY
    html = normalized_html(files)
    assert hashlib.sha256(html.encode("utf-8")).hexdigest() == EXPECTED_HTML_SHA256, "report.html changed"
    assert html.count("data:thumb") == EXPECTED_THUMBS  # failing, empty and garbage thumbnails leave a gap


# --- recorded values (taken before the refactor) ---------------------------------------

EXPECTED_PASSES = [('full', 6, True, 24, 6, 0),
 ('incremental', 8, False, 12, 2, 0),
 ('nothing new', 8, False, 0, 0, 0),
 ('hash only', 5, True, 36, 5, 0),
 ('excluded', 7, True, 32, 7, 1)]

EXPECTED_FULL = [('r1.jpg', 'https://ex.com/banner1.jpg', 'clip', 0.972691, 'haut'),
 ('r1.jpg', 'https://ex.com/copy1.jpg', 'phash', 0.96875, 'haut'),
 ('r1.jpg', 'https://ex.com/crop1.jpg', 'dhash', 0.875, 'haut'),
 ('r2.jpg', 'https://ex.com/flip2.jpg', 'phash', 1.0, 'haut'),
 ('r2.jpg', 'https://ex.com/mid2.jpg', 'clip', 0.88687, 'moyen'),
 ('r3.jpg', 'https://ex.com/low3.jpg', 'clip', 0.817776, 'a_verifier')]

EXPECTED_VERIFIED = (7,
 9,
 {'geo': 7},
 [(1, 9), (2, 9), (3, 9), (4, 9), (5, 9), (6, 9), (7, 9), (8, 9), (9, 9)],
 [('r1.jpg', 'https://ex.com/s3.jpg', 'geo', 0.978145, 'haut'),
  ('r1.jpg', 'https://ex.com/s6.jpg', 'geo', 0.988198, 'haut'),
  ('r1.jpg', 'https://ex.com/s9.jpg', 'geo', 0.984159, 'haut'),
  ('r2.jpg', 'https://ex.com/s4.jpg', 'geo', 0.985393, 'a_verifier'),
  ('r2.jpg', 'https://ex.com/s7.jpg', 'geo', 0.995543, 'haut'),
  ('r3.jpg', 'https://ex.com/s2.jpg', 'geo', 0.98906, 'haut'),
  ('r3.jpg', 'https://ex.com/s8.jpg', 'geo', 0.985475, 'haut')])

EXPECTED_VERIFY_HITS = ([(1, 2, 'phash', 0.99, 'haut'),
  (1, 1, 'geo', 0.9, 'a_verifier'),
  (2, 1, 'geo', 0.8, 'haut'),
  (2, 2, 'geo', 0.8, 'haut'),
  (1, 9, 'clip', 0.77, 'a_verifier'),
  (9, 1, 'clip', 0.76, 'a_verifier'),
  (3, 3, 'geo', 0.95, 'a_verifier'),
  (1, 3, 'geo', 0.85, 'haut')],
 [(1, 7), (2, 7), (3, 7), (6, 7), (7, 7)],
 [('load', 'ref', 1),
  ('extract', 11, False),
  ('load', 'site', 1),
  ('extract', 111, False),
  ('compare', (11, False), (111, False)),
  ('extract', 11, True),
  ('compare', (11, True), (111, False)),
  ('load', 'ref', 2),
  ('extract', 12, False),
  ('compare', (12, False), (111, False)),
  ('load', 'site', 2),
  ('extract', 112, False),
  ('compare', (12, False), (112, False)),
  ('extract', 12, True),
  ('compare', (12, True), (112, False)),
  ('load', 'site', 9),
  ('load', 'ref', 9),
  ('load', 'ref', 3),
  ('extract', 13, False),
  ('load', 'site', 3),
  ('extract', 113, False),
  ('compare', (13, False), (113, False)),
  ('extract', 13, True),
  ('compare', (13, True), (113, False)),
  ('compare', (11, False), (113, False)),
  ('compare', (11, True), (113, False)),
  ('load', 'ref', 1),
  ('extract', 11, False),
  ('load', 'site', 1),
  ('extract', 111, False),
  ('compare', (11, False), (111, False)),
  ('extract', 11, True),
  ('compare', (11, True), (111, False))],
 [(1, 2, 'phash', 0.99, 'haut'), (1, 1, 'geo', 0.9, 'a_verifier')],
 [(1, 2, 'phash', 0.9, 'haut')])

EXPECTED_MATCHES_CSV = '\ufefffilename,expiry_date,days_left,status,credit,notes,confidence,decision,score,level,site_url,pages\r\na-expired.jpg,2025-12-01,-31,Expiré,,,Confirmé,,0.97,phash,https://ex.com/2.jpg,https://ex.com/p0 | https://ex.com/p1 | https://ex.com/p2 | https://ex.com/p3 | https://ex.com/p4 | https://ex.com/p5 | https://ex.com/p6 | https://ex.com/p7 | https://ex.com/extra\r\na-expired.jpg,2025-12-01,-31,Expiré,,,À vérifier,,0.50,phash,https://ex.com/3.jpg,\r\nÉ-accent.jpg,2026-01-15,14,Moins de 30 jours,Zoë,,Confirmé,À retirer,0.12,phash,https://ex.com/9.jpg,\r\nb-soon.jpg,2026-01-20,19,Moins de 30 jours,,,Probable,,0.88,phash,https://ex.com/4.jpg,\r\ne-none.jpg,,,Date inconnue,"Photo ""X"", Paris\nline2",a;b <i>,Confirmé,Retiré,0.95,phash,https://ex.com/7.jpg,https://ex.com/é?a=1&b=2\r\nc-verify.jpg,2026-03-01,59,Moins de 90 jours,,,À vérifier,,0.80,phash,https://ex.com/5.jpg,\r\n'

EXPECTED_NOT_FOUND_CSV = '\ufefffilename,expiry_date,days_left,status,credit,notes,verification\r\ng.jpg,2026-01-05,4,Moins de 30 jours,,x,"Comparée, rien trouvé"\r\nh.jpg,,,Date inconnue,,,Pas encore comparée\r\n'

EXPECTED_SUMMARY = {'confirmed': 4,
 'expired_online': 1,
 'not_found': 2,
 'pending_review': 4,
 'references_online': 5,
 'to_verify': 1,
 'upcoming': [{'days_left': 4,
               'expiry_date': '2026-01-05',
               'filename': 'g.jpg',
               'online': False,
               'reference_id': 'g'},
              {'days_left': 14,
               'expiry_date': '2026-01-15',
               'filename': 'É-accent.jpg',
               'online': True,
               'reference_id': 'É-accent'},
              {'days_left': 19,
               'expiry_date': '2026-01-20',
               'filename': 'b-soon.jpg',
               'online': True,
               'reference_id': 'b-soon'},
              {'days_left': 59,
               'expiry_date': '2026-03-01',
               'filename': 'c-verify.jpg',
               'online': True,
               'reference_id': 'c-verify'}],
 'urgent_online': 2}

EXPECTED_HTML_SHA256 = '4c508a67082c13e1d732f183294d470c87f0b9ffb151229d77c1d8288fa633bd'

EXPECTED_THUMBS = 10
