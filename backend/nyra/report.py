"""Grouping matches by reference, and the deliverables built from them.

Two consumers share the same logic here:

- the interface (`group_matches`, `not_found_items`), which shows every
  occurrence, rejected ones included (it can hide them);
- the report (`build_report`): a self-contained, print-ready report.html
  (thumbnails inlined) plus matches.csv and not_found.csv. The report
  leaves out occurrences marked as false positives — the client should
  never receive a match someone already dismissed.

Input rows are plain dicts, so SQLite (CLI) and Postgres (hosted product)
feed the same functions. A match row carries: reference_id, filename,
expiry_date (ISO string or None), credit, notes, site_image_id, site_url,
content_hash, level, score, confidence, decision, pages, and optionally
ref_thumb / site_thumb keys understood by the caller's thumbnail loader.
"""

from __future__ import annotations

import base64
import csv
import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from nyra.config import Config
from nyra.match import CONFIDENCE_TO_VERIFY

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

STATUS_EXPIRED = "expire"
STATUS_URGENT = "<30j"
STATUS_SOON = "<90j"
STATUS_OK = "ok"
STATUS_UNKNOWN = "inconnue"

DECISION_REMOVE = "retenu"
DECISION_FALSE_POSITIVE = "ecarte"
DECISION_REMOVED = "traite"
DECISIONS = {DECISION_REMOVE, DECISION_FALSE_POSITIVE, DECISION_REMOVED}

STATUS_LABELS = {
    STATUS_EXPIRED: "Expiré",
    STATUS_URGENT: "Moins de 30 jours",
    STATUS_SOON: "Moins de 90 jours",
    STATUS_OK: "Dans les délais",
    STATUS_UNKNOWN: "Date inconnue",
}
CONFIDENCE_LABELS = {"haut": "Confirmé", "moyen": "Probable", "a_verifier": "À vérifier"}
DECISION_LABELS = {DECISION_REMOVE: "À retirer", DECISION_FALSE_POSITIVE: "Faux positif", DECISION_REMOVED: "Retiré"}


def days_until(expiry_date: Optional[str], today: date) -> Optional[int]:
    if not expiry_date:
        return None
    return (date.fromisoformat(str(expiry_date)[:10]) - today).days


def urgency_status(days_left: Optional[int]) -> str:
    if days_left is None:
        return STATUS_UNKNOWN
    if days_left < 0:
        return STATUS_EXPIRED
    if days_left < 30:
        return STATUS_URGENT
    if days_left < 90:
        return STATUS_SOON
    return STATUS_OK


def _urgency_key(days_left: Optional[int]) -> tuple[int, int]:
    # Unknown expiry sorts last; among known, most urgent (most negative / smallest) first.
    return (1, 0) if days_left is None else (0, days_left)


def in_window(days_left: Optional[int], window: int) -> bool:
    return days_left is None or days_left <= window


# --- grouping (pure) -----------------------------------------------------------

def _collapse_hits(hits: list[dict]) -> list[dict]:
    """One entry per distinct image: CDN variants of the same bytes are merged."""
    merged: dict[str, dict] = {}
    for hit in hits:
        key = hit.get("content_hash") or f"id:{hit['site_image_id']}"
        current = merged.get(key)
        if current is None:
            hit = dict(hit)
            hit["site_image_ids"] = [hit["site_image_id"]]
            hit["pages"] = list(dict.fromkeys(hit.get("pages") or []))
            merged[key] = hit
            continue
        current["site_image_ids"].append(hit["site_image_id"])
        for page in hit.get("pages") or []:
            if page not in current["pages"]:
                current["pages"].append(page)
        if hit["score"] > current["score"]:
            for field_name in ("score", "level", "confidence", "site_image_id", "site_url", "site_image", "site_thumb"):
                if field_name in hit:
                    current[field_name] = hit[field_name]
        if hit.get("decision") and not current.get("decision"):
            current["decision"] = hit["decision"]
    rows = sorted(merged.values(), key=lambda item: item["score"], reverse=True)
    for row in rows:
        row["page_count"] = len(row["pages"])
    return rows


def group_matches(rows: list[dict], window: int, today: Optional[date] = None) -> dict[str, Any]:
    """Group match rows by reference and split them by urgency and confidence.

    Returns confirmed (at least one confident occurrence, in the window),
    to_verify (only low-confidence occurrences, in the window), later
    (outside the window) and the number of rows outside the window.
    """
    today = today or datetime.now(timezone.utc).date()
    grouped: dict[Any, dict] = {}
    outside = 0
    for row in rows:
        left = days_until(row.get("expiry_date"), today)
        within = in_window(left, window)
        if not within:
            outside += 1
        bucket = grouped.get(row["reference_id"])
        if bucket is None:
            bucket = {
                "reference_id": row["reference_id"],
                "filename": row["filename"],
                "expiry_date": row.get("expiry_date"),
                "days_left": left,
                "status": urgency_status(left),
                "credit": row.get("credit") or "",
                "notes": row.get("notes") or "",
                "ref_image": row.get("ref_image"),
                "ref_thumb": row.get("ref_thumb"),
                "in_window": within,
                "hits": [],
            }
            grouped[row["reference_id"]] = bucket
        bucket["hits"].append(row)
    groups = []
    for bucket in grouped.values():
        bucket["hits"] = _collapse_hits(bucket["hits"])
        groups.append(bucket)
    groups.sort(key=lambda item: (_urgency_key(item["days_left"]), item["filename"].lower()))
    current = [item for item in groups if item["in_window"]]
    confirmed = [item for item in current if any(hit["confidence"] != CONFIDENCE_TO_VERIFY for hit in item["hits"])]
    confirmed_ids = {item["reference_id"] for item in confirmed}
    return {
        "within_days": window,
        "confirmed": confirmed,
        "to_verify": [item for item in current if item["reference_id"] not in confirmed_ids],
        "later": [item for item in groups if not item["in_window"]],
        "outside_window": outside,
    }


def not_found_items(rows: list[dict], window: int, today: Optional[date] = None) -> list[dict]:
    """References with no match, same window rule as `group_matches`.

    `compared` distinguishes "checked, nothing found" from "not compared
    yet" — the latter must never be presented as a clean result.
    """
    today = today or datetime.now(timezone.utc).date()
    items = []
    for row in rows:
        left = days_until(row.get("expiry_date"), today)
        if not in_window(left, window):
            continue
        items.append({**row, "days_left": left, "status": urgency_status(left)})
    items.sort(key=lambda item: (_urgency_key(item["days_left"]), item["filename"].lower()))
    return items


def dashboard(match_rows: list[dict], unmatched_rows: list[dict], today: Optional[date] = None) -> dict[str, Any]:
    """The numbers the home screen leads with.

    "Online" means at least one occurrence that nobody marked as a false
    positive or as removed.
    """
    today = today or datetime.now(timezone.utc).date()
    live: dict[Any, dict] = {}
    pending_review: set = set()
    for row in match_rows:
        if row.get("decision") in {DECISION_FALSE_POSITIVE, DECISION_REMOVED}:
            continue
        live.setdefault(row["reference_id"], row)
        if not row.get("decision"):
            pending_review.add(row["reference_id"])

    def status_of(row: dict) -> str:
        return urgency_status(days_until(row.get("expiry_date"), today))

    expired_online = [row for row in live.values() if status_of(row) == STATUS_EXPIRED]
    urgent_online = [row for row in live.values() if status_of(row) == STATUS_URGENT]
    upcoming = []
    for row in [*{r["reference_id"]: r for r in match_rows}.values(), *unmatched_rows]:
        left = days_until(row.get("expiry_date"), today)
        if left is not None and 0 <= left <= 90:
            upcoming.append({"reference_id": row["reference_id"], "filename": row["filename"],
                             "expiry_date": row.get("expiry_date"), "days_left": left,
                             "online": row["reference_id"] in live})
    upcoming.sort(key=lambda item: item["days_left"])
    return {
        "expired_online": len(expired_online),
        "urgent_online": len(urgent_online),
        "pending_review": len(pending_review),
        "references_online": len(live),
        "upcoming": upcoming[:12],
    }


# --- the report ------------------------------------------------------------------

@dataclass
class ReportFiles:
    html: bytes
    matches_csv: bytes
    not_found_csv: bytes
    summary: dict[str, Any]


def _data_uri(data: Optional[bytes], max_size: int = 240) -> Optional[str]:
    if not data:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            img.thumbnail((max_size, max_size))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _load_thumbs(keys: set, loader: Callable[[Any], Optional[bytes]]) -> dict[Any, Optional[str]]:
    keys = {key for key in keys if key}
    if not keys:
        return {}

    def one(key):
        try:
            return key, _data_uri(loader(key))
        except Exception:
            return key, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(one, keys))


def _csv_bytes(fieldnames: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    # BOM so Excel opens accents correctly.
    return ("﻿" + buf.getvalue()).encode("utf-8")


def build_report(
    match_rows: list[dict],
    unmatched_rows: list[dict],
    *,
    within_days: int,
    stats: dict[str, Any],
    thumb_loader: Callable[[Any], Optional[bytes]],
    organization: str = "",
    today: Optional[date] = None,
) -> ReportFiles:
    today = today or datetime.now(timezone.utc).date()
    kept = [row for row in match_rows if row.get("decision") != DECISION_FALSE_POSITIVE]
    grouped = group_matches(kept, within_days, today)
    missing = not_found_items(unmatched_rows, within_days, today)
    groups = grouped["confirmed"] + grouped["to_verify"]

    keys = {group.get("ref_thumb") for group in groups} | {item.get("ref_thumb") for item in missing}
    keys |= {hit.get("site_thumb") for group in groups for hit in group["hits"]}
    thumbs = _load_thumbs(keys, thumb_loader)

    summary = dashboard(kept, unmatched_rows, today)
    summary.update({
        "confirmed": len(grouped["confirmed"]),
        "to_verify": len(grouped["to_verify"]),
        "not_found": len(missing),
    })

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html", "j2"]))
    html = env.get_template("report.html.j2").render(
        organization=organization,
        confirmed=grouped["confirmed"],
        to_verify=grouped["to_verify"],
        not_found=missing,
        within_days=within_days,
        generated_at=datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M UTC"),
        stats=stats,
        summary=summary,
        thumbs=thumbs,
        status_labels=STATUS_LABELS,
        confidence_labels=CONFIDENCE_LABELS,
        decision_labels=DECISION_LABELS,
    )

    occurrence_rows = []
    for group in groups:
        for hit in group["hits"]:
            occurrence_rows.append({
                "filename": group["filename"],
                "expiry_date": group["expiry_date"] or "",
                "days_left": "" if group["days_left"] is None else group["days_left"],
                "status": STATUS_LABELS[group["status"]],
                "credit": group["credit"],
                "notes": group["notes"],
                "confidence": CONFIDENCE_LABELS.get(hit["confidence"], hit["confidence"]),
                "decision": DECISION_LABELS.get(hit.get("decision") or "", ""),
                "score": f"{hit['score']:.2f}",
                "level": hit["level"],
                "site_url": hit["site_url"],
                "pages": " | ".join(hit["pages"]),
            })
    not_found_rows = [
        {
            "filename": item["filename"],
            "expiry_date": item.get("expiry_date") or "",
            "days_left": "" if item["days_left"] is None else item["days_left"],
            "status": STATUS_LABELS[item["status"]],
            "credit": item.get("credit") or "",
            "notes": item.get("notes") or "",
            "verification": "Comparée, rien trouvé" if item.get("compared") else "Pas encore comparée",
        }
        for item in missing
    ]
    return ReportFiles(
        html=html.encode("utf-8"),
        matches_csv=_csv_bytes(
            ["filename", "expiry_date", "days_left", "status", "credit", "notes", "confidence", "decision",
             "score", "level", "site_url", "pages"],
            occurrence_rows,
        ),
        not_found_csv=_csv_bytes(
            ["filename", "expiry_date", "days_left", "status", "credit", "notes", "verification"], not_found_rows
        ),
        summary=summary,
    )


def generate_report(db_path: Path | str, out_dir: Path | str, config: Config, *, within_days: Optional[int] = None) -> tuple[Path, Path, Path]:
    """CLI entry point: build the report from a SQLite database into out_dir."""
    from nyra import db

    within_days = within_days if within_days is not None else config.report.default_within_days
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with db.connect(db_path) as conn:
        match_rows = db.match_rows(conn)
        unmatched = db.unmatched_rows(conn)
        stats = db.get_stats(conn).__dict__

    def load(path: str) -> Optional[bytes]:
        file = Path(path)
        return file.read_bytes() if file.is_file() else None

    files = build_report(match_rows, unmatched, within_days=within_days, stats=stats, thumb_loader=load)
    paths = (out_dir / "report.html", out_dir / "matches.csv", out_dir / "not_found.csv")
    for path, data in zip(paths, (files.html, files.matches_csv, files.not_found_csv)):
        path.write_bytes(data)
    return paths
