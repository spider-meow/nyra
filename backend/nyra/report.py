"""Report generation: a self-contained report.html (thumbnails inlined as
base64) plus matches.csv, both sorted by expiry urgency.

Matches are split into a main section (confidence "haut"/"moyen") and a
separate "a verifier" section (confidence "a_verifier"), per the spec.
"""

from __future__ import annotations

import base64
import csv
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from nyra import db
from nyra.config import Config
from nyra.match import CONFIDENCE_TO_VERIFY

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

STATUS_EXPIRED = "expire"
STATUS_URGENT = "<30j"
STATUS_SOON = "<90j"
STATUS_OK = "ok"
STATUS_UNKNOWN = "inconnue"


def days_until(expiry_date: Optional[str], today: date) -> Optional[int]:
    if not expiry_date:
        return None
    return (date.fromisoformat(expiry_date) - today).days


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


def _sort_key(days_left: Optional[int]) -> tuple[int, int]:
    # Unknown expiry sorts last; among known, most urgent (most negative / smallest) first.
    if days_left is None:
        return (1, 0)
    return (0, days_left)


def image_to_data_uri(path: Optional[str], max_size: int = 220) -> Optional[str]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        from PIL import Image
        import io

        with Image.open(p) as img:
            img = img.convert("RGB")
            img.thumbnail((max_size, max_size))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{encoded}"
    except Exception:
        return None


@dataclass
class ReportRow:
    filename: str
    expiry_date: Optional[str]
    days_left: Optional[int]
    status: str
    credit: Optional[str]
    notes: Optional[str]
    pages: list[str]
    site_url: str
    level: str
    score: float
    confidence: str
    ref_thumbnail: Optional[str]
    site_thumbnail: Optional[str]


@dataclass
class NotFoundRow:
    filename: str
    expiry_date: Optional[str]
    days_left: Optional[int]
    status: str
    credit: Optional[str]
    notes: Optional[str]
    compared: bool
    ref_thumbnail: Optional[str]


def build_not_found_rows(
    db_path: Path | str, within_days: int, today: Optional[date] = None
) -> list[NotFoundRow]:
    """References with zero matches — checked and not seen, or not checked yet.

    Filtered by the same `within_days` window as `build_rows` (unknown
    expiry always included) so a client reading the report gets a
    consistent picture: everything urgent, found or not.
    """
    today = today or datetime.now(timezone.utc).date()
    rows: list[NotFoundRow] = []

    with db.connect(db_path) as conn:
        for r in db.get_unmatched_references(conn):
            days_left = days_until(r["expiry_date"], today)
            status = urgency_status(days_left)
            if days_left is not None and days_left > within_days:
                continue

            rows.append(
                NotFoundRow(
                    filename=r["filename"],
                    expiry_date=r["expiry_date"],
                    days_left=days_left,
                    status=status,
                    credit=r["credit"],
                    notes=r["notes"],
                    compared=r["compared_at"] is not None,
                    ref_thumbnail=image_to_data_uri(r["ref_path"]),
                )
            )

    rows.sort(key=lambda r: _sort_key(r.days_left))
    return rows


def build_rows(
    db_path: Path | str,
    within_days: int,
    today: Optional[date] = None,
    *,
    review_status: str = "pending",
) -> list[ReportRow]:
    """`review_status` filters by the match traceability status (see MATCH_STATUSES).

    `"all"` includes every match regardless of status; anything else must be
    one of `pending`/`confirmed`/`rejected` and keeps only that status. The
    CLI/API default is `"pending"` — a report is a to-do list of matches not
    yet reviewed, not a permanent log of everything ever found.
    """
    today = today or datetime.now(timezone.utc).date()
    rows: list[ReportRow] = []

    with db.connect(db_path) as conn:
        for m in db.get_matches(conn):
            if review_status != "all" and m["match_status"] != review_status:
                continue
            days_left = days_until(m["expiry_date"], today)
            status = urgency_status(days_left)
            if days_left is not None and days_left > within_days:
                continue

            pages = db.get_pages_for_image(conn, m["site_image_id"])
            rows.append(
                ReportRow(
                    filename=m["filename"],
                    expiry_date=m["expiry_date"],
                    days_left=days_left,
                    status=status,
                    credit=m["credit"],
                    notes=m["notes"],
                    pages=pages,
                    site_url=m["site_url"],
                    level=m["level"],
                    score=m["score"],
                    confidence=m["confidence"],
                    ref_thumbnail=image_to_data_uri(m["ref_path"]),
                    site_thumbnail=image_to_data_uri(m["site_local_path"]),
                )
            )

    rows.sort(key=lambda r: _sort_key(r.days_left))
    return rows


def render_html(
    rows: list[ReportRow],
    out_path: Path | str,
    *,
    within_days: int,
    site_stats: Optional[dict] = None,
    not_found: Optional[list[NotFoundRow]] = None,
) -> None:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html.j2")

    confirmed = [r for r in rows if r.confidence != CONFIDENCE_TO_VERIFY]
    to_verify = [r for r in rows if r.confidence == CONFIDENCE_TO_VERIFY]

    html = template.render(
        confirmed=confirmed,
        to_verify=to_verify,
        not_found=not_found or [],
        within_days=within_days,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        stats=site_stats or {},
    )
    Path(out_path).write_text(html, encoding="utf-8")


def write_csv(rows: list[ReportRow], out_path: Path | str) -> None:
    fieldnames = [
        "filename", "expiry_date", "days_left", "status", "credit", "notes",
        "pages", "site_url", "level", "score", "confidence",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            d = asdict(r)
            d["pages"] = " | ".join(r.pages)
            del d["ref_thumbnail"]
            del d["site_thumbnail"]
            writer.writerow(d)


def write_not_found_csv(rows: list[NotFoundRow], out_path: Path | str) -> None:
    fieldnames = ["filename", "expiry_date", "days_left", "status", "credit", "notes", "compared"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            d = asdict(r)
            del d["ref_thumbnail"]
            writer.writerow(d)


def generate_report(
    db_path: Path | str,
    out_dir: Path | str,
    config: Config,
    *,
    within_days: Optional[int] = None,
    review_status: str = "pending",
) -> tuple[Path, Path, Path]:
    within_days = within_days if within_days is not None else config.report.default_within_days
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = build_rows(db_path, within_days, review_status=review_status)
    not_found_rows = build_not_found_rows(db_path, within_days)

    with db.connect(db_path) as conn:
        stats = asdict(db.get_stats(conn))

    html_path = out_dir / "report.html"
    csv_path = out_dir / "matches.csv"
    not_found_csv_path = out_dir / "not_found.csv"
    render_html(rows, html_path, within_days=within_days, site_stats=stats, not_found=not_found_rows)
    write_csv(rows, csv_path)
    write_not_found_csv(not_found_rows, not_found_csv_path)
    return html_path, csv_path, not_found_csv_path
