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
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from nyra.config import Config
from nyra.match import CONFIDENCE_HIGH, CONFIDENCE_TO_VERIFY, describe_signature
from nyra.pdf import marker as pdf_marker
from nyra.report_text import LANGS, translator

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

STATUS_EXPIRED = "expire"
STATUS_URGENT = "<30j"
STATUS_SOON = "<90j"
STATUS_OK = "ok"
STATUS_UNKNOWN = "inconnue"
STATUS_UNLIMITED = "illimite"

DECISION_REMOVE = "retenu"
DECISION_FALSE_POSITIVE = "ecarte"
DECISION_REMOVED = "traite"
DECISIONS = {DECISION_REMOVE, DECISION_FALSE_POSITIVE, DECISION_REMOVED}

SOON_DAYS, LATER_DAYS = 30, 90  # the only place of the two thresholds: "sous 30 jours" and "sous 90 jours" include the day itself

PRESENCE_STILL, PRESENCE_GONE, PRESENCE_UNKNOWN = "still", "gone", "unknown"  # the states of `cloud/presence.py`

PRESENCE_LABELS = {PRESENCE_STILL: "En ligne", PRESENCE_GONE: "Retiré", PRESENCE_UNKNOWN: "Inconnue"}

REDIRECT_REASONS = {  # why a page that answered with another page is "Inconnue" (`crawl.redirect_kind`); same meaning as `lib/presence.ts`
    "clear": "Redirection permanente, règle non activée", "temporary": "Redirection temporaire", "script": "Redirigée par un script",
    "home": "Renvoyée vers l'accueil ou une page parente", "incomplete": "Image de la page d'arrivée non lue",
    "other_section": "Renvoyée vers une autre rubrique ou un autre pays", "other_site": "Renvoyée vers un autre site",
    "barrier": "Renvoyée vers une barrière (consentement, connexion, âge, pays)", "error": "La page d'arrivée a répondu en erreur",
}

ACTION_REMOVE, ACTION_CHECK, ACTION_NONE = "À retirer", "À vérifier", "Rien à faire"

STATUS_LABELS = {
    STATUS_EXPIRED: "Expiré",
    STATUS_URGENT: f"Sous {SOON_DAYS} jours",
    STATUS_SOON: f"Sous {LATER_DAYS} jours",
    STATUS_OK: "Dans les délais",
    STATUS_UNKNOWN: "Date inconnue",
    STATUS_UNLIMITED: "Droits illimités",
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
    if days_left <= SOON_DAYS:
        return STATUS_URGENT
    if days_left <= LATER_DAYS:
        return STATUS_SOON
    return STATUS_OK


def rights_status(row: dict, days_left: Optional[int]) -> str:
    """The status of a reference: unlimited rights never expire, whatever the date says."""
    return STATUS_UNLIMITED if row.get("unlimited_rights") else urgency_status(days_left)


def page_state(*, days_left: Optional[int], unlimited: bool, confidence: str, decision: Optional[str] = None,
               presence: str = PRESENCE_UNKNOWN, replaced: bool = False) -> tuple[str, str]:
    """The one rule: where a visual stands on a page, as (rights status, action).

    Rights (`rights_status`) x presence x what a person decided. A visual a person dismissed or marked removed, a
    replaced one and one no longer on the page are never "à retirer": nothing is left to do. A weak match stays "à vérifier"
    until a person confirms it; an unknown presence (page not read again) does not hide an expired visual."""
    status = STATUS_UNLIMITED if unlimited else urgency_status(days_left)
    if decision in (DECISION_FALSE_POSITIVE, DECISION_REMOVED) or replaced or presence == PRESENCE_GONE:
        return status, ACTION_NONE
    if confidence == CONFIDENCE_TO_VERIFY and decision != DECISION_REMOVE:
        return status, ACTION_CHECK
    if status == STATUS_EXPIRED:
        return status, ACTION_REMOVE
    if status in (STATUS_URGENT, STATUS_SOON):
        return status, "Expire aujourd'hui" if days_left == 0 else f"Expire dans {days_left} j"
    return status, ACTION_NONE


def _is_free_to_use(group: dict) -> bool:
    """Unlimited rights found with certainty (a confirmed match nobody dismissed): nothing to treat. A weaker
    match stays to be checked, because a wrong match would hide an image whose rights nobody knows."""
    return group["status"] == STATUS_UNLIMITED and any(
        hit["confidence"] == CONFIDENCE_HIGH and hit.get("decision") != DECISION_FALSE_POSITIVE for hit in group["hits"]
    )


def _urgency_key(days_left: Optional[int]) -> tuple[int, int]:
    # Unknown expiry sorts last; among known, most urgent (most negative / smallest) first.
    return (1, 0) if days_left is None else (0, days_left)


def in_window(days_left: Optional[int], window: int) -> bool:
    return days_left is None or days_left <= window


# --- grouping (pure) -----------------------------------------------------------

def _removal(files: list[dict]) -> str:
    """Where the removal of a photo (marked removed) stands, from what the last read of its sites showed:
    `online` a read since the mark saw a file of it again, `pending` a site was not read since the mark,
    `unread` a page that showed it was not read again, `suspect` the last read of a site looks incomplete (so it proves nothing),
    `gone` every page was read again and it is not there."""
    if any(row.get("read_after") and row.get("seen") for row in files):
        return "online"
    if not all(row.get("read_after") for row in files):
        return "pending"
    if any(row.get("suspect_read") for row in files):
        return "suspect"
    return "unread" if any(row.get("unread_pages") for row in files) else "gone"


def _collapse_hits(hits: list[dict]) -> list[dict]:
    """One entry per photo: the same bytes under several addresses (CDN variants) and the crops and resizes of one
    photo (`variant_group`) are merged. The entry shows the best-scoring file; its decision is the common one, or none
    while the photo's files are not all decided the same way. `versions` are the site versions it is on (the label of the site,
    or its host when it has none)."""
    group_of = {hit["content_hash"]: hit["variant_group"] for hit in hits if hit.get("variant_group") and hit.get("content_hash")}
    merged: dict[str, dict] = {}
    files: dict[str, set] = {}
    decisions: dict[str, set] = {}
    members: dict[str, list[dict]] = {}
    versions: dict[str, set] = {}
    for hit in hits:
        key = str(hit.get("variant_group") or group_of.get(hit.get("content_hash")) or hit.get("content_hash") or f"id:{hit['site_image_id']}")
        files.setdefault(key, set()).add(hit.get("content_hash") or hit["site_image_id"])
        decisions.setdefault(key, set()).add(hit.get("decision"))
        members.setdefault(key, []).append(hit)
        versions.setdefault(key, set()).add(hit.get("site_label") or urlparse(hit["site_url"]).netloc)
        current = merged.get(key)
        if current is None:
            hit = dict(hit)
            hit["site_image_ids"] = [hit["site_image_id"]]
            hit["pages"] = list(dict.fromkeys(hit.get("pages") or []))
            merged[key] = hit
            continue
        current["site_image_ids"].append(hit["site_image_id"])
        current["pages"] += [page for page in dict.fromkeys(hit.get("pages") or []) if page not in current["pages"]]
        if hit["score"] > current["score"]:
            for field_name in ("score", "level", "confidence", "site_image_id", "site_url", "site_image", "site_thumb"):
                if field_name in hit:
                    current[field_name] = hit[field_name]
    for key, row in merged.items():
        row["decision"] = next(iter(decisions[key])) if len(decisions[key]) == 1 else None
        row["photo_key"], row["file_count"], row["page_count"] = key, len(files[key]), len(row["pages"])
        row["removal"] = _removal(members[key]) if row["decision"] == DECISION_REMOVED else None
        row["versions"] = sorted(versions[key])
    return sorted(merged.values(), key=lambda item: item["score"], reverse=True)


def group_matches(rows: list[dict], window: int, today: Optional[date] = None) -> dict[str, Any]:
    """Group match rows by reference and split them by urgency and confidence.

    Returns confirmed (at least one confident occurrence, in the window),
    to_verify (only low-confidence occurrences, in the window), later
    (outside the window), unlimited (unlimited rights found with certainty:
    nothing to treat) and the number of rows outside the window.
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
                "status": rights_status(row, left),
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
    free = [item for item in groups if _is_free_to_use(item)]
    free_ids = {item["reference_id"] for item in free}
    groups = [item for item in groups if item["reference_id"] not in free_ids]
    current = [item for item in groups if item["in_window"]]
    confirmed = [item for item in current if any(hit["confidence"] != CONFIDENCE_TO_VERIFY for hit in item["hits"])]
    confirmed_ids = {item["reference_id"] for item in confirmed}
    return {
        "within_days": window,
        "confirmed": confirmed,
        "to_verify": [item for item in current if item["reference_id"] not in confirmed_ids],
        "later": [item for item in groups if not item["in_window"]],
        "unlimited": free,
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
        items.append({**row, "days_left": left, "status": rights_status(row, left)})
    items.sort(key=lambda item: (_urgency_key(item["days_left"]), item["filename"].lower()))
    return items


def dashboard(match_rows: list[dict], unmatched_rows: list[dict], today: Optional[date] = None,
              replaced: frozenset = frozenset()) -> dict[str, Any]:
    """The numbers the home screen leads with.

    "Online" means at least one occurrence that nobody marked as a false
    positive or as removed, of a reference not `replaced` (every page that showed it was read again, with a read that
    can be trusted, and none shows it any more: `cloud.presence.replaced_references`).
    """
    today = today or datetime.now(timezone.utc).date()
    live: dict[Any, dict] = {}
    pending_review: set = set()
    free = {row["reference_id"] for row in match_rows if row.get("unlimited_rights") and row["confidence"] == CONFIDENCE_HIGH
            and row.get("decision") != DECISION_FALSE_POSITIVE}  # listed as "Libre d'usage", not to review
    for row in match_rows:
        if row.get("decision") in {DECISION_FALSE_POSITIVE, DECISION_REMOVED} or row["reference_id"] in replaced:
            continue
        live.setdefault(row["reference_id"], row)
        if not row.get("decision") and row["reference_id"] not in free:
            pending_review.add(row["reference_id"])

    def status_of(row: dict) -> str:
        return rights_status(row, days_until(row.get("expiry_date"), today))

    expired_online = [row for row in live.values() if status_of(row) == STATUS_EXPIRED]
    urgent_online = [row for row in live.values() if status_of(row) == STATUS_URGENT]
    upcoming = []
    for row in [*{r["reference_id"]: r for r in match_rows}.values(), *unmatched_rows]:
        left = days_until(row.get("expiry_date"), today)
        if left is not None and 0 <= left <= LATER_DAYS and not row.get("unlimited_rights"):
            upcoming.append({"reference_id": row["reference_id"], "filename": row["filename"],
                             "expiry_date": row.get("expiry_date"), "days_left": left,
                             "online": row["reference_id"] in live})
    upcoming.sort(key=lambda item: item["days_left"])
    return {
        "expired_online": len(expired_online),
        "urgent_online": len(urgent_online),
        "pending_review": len(pending_review),
        "pending_expired": sum(status_of(live[ref]) == STATUS_EXPIRED for ref in pending_review),
        "pending_urgent": sum(status_of(live[ref]) == STATUS_URGENT for ref in pending_review),
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
    site_read_on: str = ""  # when the sites were read ("" when no read is recorded), kept with the report for the list
    print_html: bytes = b""  # the same report laid out for the PDF (`nyra.pdf`): page numbers to fill, markers to find them


# What a report can hold (`include`); the first three are sections of `page_rows`, the last the images of « À qualifier ».
INCLUDE = {"expired": "remove", "soon": "soon", "check": "check", "unreferenced": None}
EVERYWHERE_PAGES = 10  # an image not in the library on more pages than this (a banner, a footer) is shown once per version, not on each page
PRIORITY_SHOWN = 8  # rubriques in « À traiter d'abord »
NO_PAGE = "Page inconnue"


def rubric_of(page_url: str, site_url: str) -> str:
    """The rubrique of a page: the first segment of its path below the path of its site version (`/fr/collections/x` on
    `https://ex.com/fr` gives "/collections"), "/" for the version's home page. The same rule as `cloud.presence._rubric_path`."""
    path = urlparse(page_url).path or "/"
    base = urlparse(site_url).path.rstrip("/") if site_url else ""
    if base and (path == base or path.startswith(base + "/")):
        path = path[len(base):]
    return "/" + path.strip("/").split("/")[0]


def _rubric_key(name: str) -> tuple[int, str]:
    return (0 if name == "/" else 2 if name == NO_PAGE else 1, name.lower())


def by_place(rows: list[dict], unreferenced: Sequence[dict], sites: Sequence[dict]) -> list[dict]:
    """The report's body: site versions (in the order of `sites`, then the unknown ones), each with its rubriques ("/" first,
    then alphabetical) and their pages (alphabetical), each page with its rows (`page_rows`) and its images not in the library.
    `unreferenced`: one dict per image and page (`site_id`, `page`, or no page for an image shown on many pages). A page's
    version is its row's `site_id`; without one (the offline CLI), its host."""
    known = {str(site["id"]): site for site in sites}
    order = {key: i for i, key in enumerate(known)}
    versions: dict[str, dict] = {}

    def version(site_id: Any, page: str) -> dict:
        site = known.get(str(site_id)) if site_id else None
        if site is None:
            host = urlparse(page).netloc if page else ""
            site = {"id": host or "?", "label": host or "Site inconnu", "url": f"https://{host}/" if host else ""}
        label = site.get("label") or (urlparse(site["url"]).netloc + urlparse(site["url"]).path).rstrip("/")
        return versions.setdefault(str(site["id"]), {"key": str(site["id"]), "label": label, "url": site["url"], "rubrics": {}, "everywhere": []})

    def entry(owner: dict, page: str) -> dict:
        name = rubric_of(page, owner["url"]) if page else NO_PAGE
        pages = owner["rubrics"].setdefault(name, {"name": name, "pages": {}})["pages"]
        parsed = urlparse(page)
        path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        return pages.setdefault(page, {"url": page, "path": path if page else NO_PAGE, "rows": [], "unreferenced": [], "read_on": ""})

    for row in rows:
        target = entry(version(row.get("site_id"), row["page"]), row["page"])
        target["rows"].append(row)
        target["read_on"] = target["read_on"] or row["read_on"]
    for item in unreferenced:
        owner = version(item.get("site_id"), item.get("page") or "")
        if item.get("page"):
            entry(owner, item["page"])["unreferenced"].append(item)
        else:
            owner["everywhere"].append(item)
    out = sorted(versions.values(), key=lambda v: (order.get(v["key"], len(order)), v["label"].lower()))
    for i, owner in enumerate(out):
        owner["anchor"] = f"v{i}"
        rubrics = sorted(owner["rubrics"].values(), key=lambda r: _rubric_key(r["name"]))
        for j, rubric in enumerate(rubrics):
            rubric["anchor"] = f"v{i}-{j}"
            rubric["pages"] = sorted(rubric["pages"].values(), key=lambda pg: pg["path"].lower())
            for pg in rubric["pages"]:
                pg["rows"] = _one_per_photo(pg["rows"])
            for name in ("remove", "soon", "check"):
                rubric[name] = sum(row["section"] == name for pg in rubric["pages"] for row in pg["rows"])
            rubric["unreferenced"] = sum(len(pg["unreferenced"]) for pg in rubric["pages"])
        owner["rubrics"] = rubrics
        owner["everywhere"] = sorted(owner["everywhere"], key=lambda item: -item.get("page_count", 0))
    return out


def _one_per_photo(rows: list[dict]) -> list[dict]:
    """The images of a page, each once: the image as the site shows it, under its most urgent visual (to remove, then
    expiring, then to check; the nearest date first). `also`: how many other visuals of the library the same image matches."""
    order_of = {"remove": 0, "soon": 1, "check": 2}
    rows = sorted(rows, key=lambda row: (order_of.get(row["section"], 3), _urgency_key(row["group"]["days_left"])))
    kept: dict[str, dict] = {}
    for row in rows:
        key = str(row["hit"].get("photo_key") or row["hit"].get("site_image_id"))
        if key in kept:
            kept[key]["also"] += row["group"]["reference_id"] != kept[key]["group"]["reference_id"]
        else:
            kept[key] = {**row, "also": 0}
    return list(kept.values())


def priorities(versions: list[dict]) -> list[dict]:
    """The rubriques with something expired or expiring, the most expired first: the first page of the report."""
    found = [{"version": owner["label"], **rubric} for owner in versions for rubric in owner["rubrics"] if rubric["remove"] or rubric["soon"]]
    return sorted(found, key=lambda r: (-r["remove"], -r["soon"]))[:PRIORITY_SHOWN]


def _data_uri(data: Optional[bytes], max_size: int = 160) -> Optional[str]:
    if not data:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGB")
            img.thumbnail((max_size, max_size))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75)  # 160 px: a thumbnail is about 22 mm wide on paper
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 - an unreadable thumbnail leaves a gap in the report, not a failed report
        return None


def _load_thumbs(keys: set, loader: Callable[[Any], Optional[bytes]]) -> dict[Any, Optional[str]]:
    keys = {key for key in keys if key}
    if not keys:
        return {}

    def one(key):
        try:
            return key, _data_uri(loader(key))
        except Exception:  # noqa: BLE001 - same: one missing image must not sink the report
            return key, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(one, keys))


def _day(value: Any) -> str:
    """A stored timestamp as a UTC day; "" when it is missing or malformed (the report is built all the same)."""
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return ""
    return (moment.astimezone(timezone.utc) if moment.tzinfo else moment).strftime("%d/%m/%Y")


def _read_on(bounds: Optional[Sequence[Any]], lang: str = "fr") -> str:
    """When the sites were read, from (oldest, newest) of the last finished read of each site: one day, or
    "du X au Y" ("from X to Y") when the sites were not read the same day. "" when no read is recorded."""
    days = list(dict.fromkeys(day for day in map(_day, bounds or ()) if day))
    if len(days) < 2:
        return "".join(days)
    return translator(lang)("between", first=days[0], last=days[-1])


def _csv_bytes(fieldnames: Sequence[str], rows: list[dict], stamp: dict[str, str]) -> bytes:
    """`stamp` (the settings and the read date) goes in two columns, not in comment lines: Excel keeps them when the
    rows are sorted or filtered, and a CSV reader still finds its header on the first line."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=(*fieldnames, *stamp), extrasaction="ignore")
    writer.writeheader()
    writer.writerows({**row, **stamp} for row in rows)
    # BOM so Excel opens accents correctly.
    return ("﻿" + buf.getvalue()).encode("utf-8")


MATCHES_COLUMNS = ("filename", "page", "site_url", "status", "expiry_date", "days_left", "presence", "read_on", "decision",
                   "confidence", "action", "credit", "notes", "score", "level")
NOT_FOUND_COLUMNS = ("filename", "expiry_date", "days_left", "status", "credit", "notes", "verification")


def _thumb_keys(versions: list[dict], loaded: Optional[dict] = None) -> list:
    """The thumbnails the report shows, once each, in the order they appear: the image as the site shows it, or the visual's
    own (the library's) when the site's could not be read (`loaded`: the thumbnails read so far; None: not read yet)."""
    keys = [item["thumb"] for owner in versions for item in owner["everywhere"]]
    for owner in versions:
        for rubric in owner["rubrics"]:
            for pg in rubric["pages"]:
                for row in pg["rows"]:
                    seen = row["hit"].get("site_thumb")
                    keys.append(seen if loaded is None or loaded.get(seen) else row["group"].get("ref_thumb"))
                keys += [item["thumb"] for item in pg["unreferenced"]]
    return list(dict.fromkeys(key for key in keys if key))


def _render_html(*, versions: list[dict], within_days: int, stats: dict, summary: dict, thumbs: dict, organization: str,
                 stamp: dict[str, str], suspect_sites: Sequence[str], include: Sequence[str], scope: Sequence[str],
                 lang: str = "fr", read_at: Optional[Sequence[Any]] = None) -> tuple[str, str]:
    """The report for the screen, and the same one for the PDF (`for_print`: markers on each rubrique and its page number
    left to fill, see `nyra.pdf`), in `lang` (`report_text.LANGS`)."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html", "j2"]))
    # each distinct thumbnail is written once, as a CSS class: a visual shown on 50 pages does not repeat its bytes 50 times
    # (numbered in the order they appear, so the same data gives the same bytes)
    thumb_class = {key: f"t{i}" for i, key in enumerate(key for key in _thumb_keys(versions, thumbs) if thumbs.get(key))}
    values = dict(
        organization=organization,
        versions=versions,
        priorities=priorities(versions),
        everywhere_pages=EVERYWHERE_PAGES,
        marker=pdf_marker,
        include=set(include),
        scope=list(scope),
        suspect_sites=list(suspect_sites),
        t=translator(lang),
        lang=lang if lang in LANGS else "fr",
        within_days=within_days,
        generated_on=f"{datetime.now(timezone.utc):%d/%m/%Y}",
        generated_time=f"{datetime.now(timezone.utc):%H:%M}",
        stats=stats,
        summary=summary,
        thumb_class=thumb_class,
        thumb_css=Markup("".join(f".{name}{{background-image:url({thumbs[key]})}}" for key, name in thumb_class.items())),
        settings=stamp["detection_settings"],
        site_read_on=_read_on(read_at, lang) if read_at else stamp["site_read_on"],
    )
    template = env.get_template("report.html.j2")
    return template.render(**values, for_print=False), template.render(**values, for_print=True)


def page_rows(groups: list[dict], presence: dict, replaced: frozenset) -> list[dict]:
    """One row per visual and page, the unit the report is read in: rights, presence on that page (`presence`:
    (reference, page address) -> state and read date, from `cloud.presence.page_states`; a page it does not know is
    unknown), what a person decided, and the action `page_state` gives. A hit with no known page gets one row with no page."""
    rows = []
    for group in groups:
        for hit in group["hits"]:
            for page in hit["pages"] or [""]:
                seen = presence.get((group["reference_id"], page)) or {}
                state = seen.get("state", PRESENCE_UNKNOWN)
                status, action = page_state(days_left=group["days_left"], unlimited=group["status"] == STATUS_UNLIMITED,
                                            confidence=hit["confidence"], decision=hit.get("decision"), presence=state,
                                            replaced=group["reference_id"] in replaced)
                rows.append({"group": group, "hit": hit, "page": page, "presence": state, "read_on": _day(seen.get("crawled_at")),
                             "site_id": seen.get("site_id") or hit.get("site_id"),
                             "reason": REDIRECT_REASONS.get(seen.get("redirect_kind") or "") if state == PRESENCE_UNKNOWN else None,
                             "status": status, "action": action, "section": _section(action, hit.get("decision"), state,
                                                                                  group["reference_id"] in replaced)})
    return rows


def _section(action: str, decision: Optional[str], presence: str, replaced: bool) -> str:
    """Where a row sits in the report, most urgent first: `remove`, `soon`, `check`, then `done` (removed, replaced or gone
    from the page: kept as proof) and `other` (rights in order, unlimited, or no date)."""
    if action == ACTION_REMOVE:
        return "remove"
    if action == ACTION_CHECK:
        return "check"
    if action != ACTION_NONE:
        return "soon"
    return "done" if decision == DECISION_REMOVED or replaced or presence == PRESENCE_GONE else "other"


def _occurrence_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "filename": row["group"]["filename"],
            "page": row["page"],
            "site_url": row["hit"]["site_url"],
            "status": STATUS_LABELS[row["status"]],
            "expiry_date": row["group"]["expiry_date"] or "",
            "days_left": "" if row["group"]["days_left"] is None else row["group"]["days_left"],
            "presence": PRESENCE_LABELS[row["presence"]],
            "read_on": row["read_on"],
            "decision": DECISION_LABELS.get(row["hit"].get("decision") or "", ""),
            "confidence": CONFIDENCE_LABELS.get(row["hit"]["confidence"], row["hit"]["confidence"]),
            "action": row["action"],
            "credit": row["group"]["credit"],
            "notes": row["group"]["notes"],
            "score": f"{row['hit']['score']:.2f}",
            "level": row["hit"]["level"],
        }
        for row in rows
    ]


def _not_found_rows(missing: list[dict]) -> list[dict]:
    return [
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


def build_report(
    match_rows: list[dict],
    unmatched_rows: list[dict],
    *,
    within_days: int,
    stats: dict[str, Any],
    thumb_loader: Callable[[Any], Optional[bytes]],
    organization: str = "",
    today: Optional[date] = None,
    provenance: Optional[dict[str, Any]] = None,
    replaced: frozenset = frozenset(),
    presence: Optional[dict] = None,
    sites: Sequence[dict] = (),
    include: Sequence[str] = tuple(INCLUDE),
    unreferenced: Sequence[dict] = (),
    lang: str = "fr",
) -> ReportFiles:
    """`provenance` is what the matches were produced with: `signature` (the one `run_matching` stored) and `read_at`
    ((oldest, newest) of the last finished read of each site). Both are printed in the HTML footer and in two columns of each CSV.
    `sites` (id, label, url) are the site versions the report covers, in their order; `include` what it shows (`INCLUDE`);
    `unreferenced` the images not in the library, one dict per image and page (`by_place`), with `thumb` and `name`;
    `lang` the language of the HTML and the PDF (`report_text.LANGS`; the CSV files stay in French).
    The caller narrows the rows to the versions asked for; the CSV of the matches keeps every row of those versions."""
    provenance = provenance or {}
    stamp = {"site_read_on": _read_on(provenance.get("read_at")),
             "detection_settings": describe_signature(provenance.get("signature"))}
    today = today or datetime.now(timezone.utc).date()
    include = [name for name in INCLUDE if name in include]
    kept = [row for row in match_rows if row.get("decision") != DECISION_FALSE_POSITIVE]
    grouped = group_matches(kept, within_days, today)
    missing = not_found_items(unmatched_rows, within_days, today)
    groups = grouped["confirmed"] + grouped["to_verify"]
    rows = page_rows(groups, presence or {}, replaced)
    shown = {INCLUDE[name] for name in include if INCLUDE[name]}
    unreferenced = list(unreferenced) if "unreferenced" in include else []
    versions = by_place([row for row in rows if row["section"] in shown], unreferenced, sites)

    thumbs = _load_thumbs(set(_thumb_keys(versions)), thumb_loader)
    thumbs |= _load_thumbs(set(_thumb_keys(versions, thumbs)) - thumbs.keys(), thumb_loader)  # the library's image where the site's failed

    summary = dashboard(kept, unmatched_rows, today, replaced)
    summary.update({
        "confirmed": len(grouped["confirmed"]),
        "to_verify": len(grouped["to_verify"]),
        "not_found": len(missing),
        "soon_online": len({row["group"]["reference_id"] for row in rows if row["section"] == "soon"}),
        "unreferenced": len({item.get("key", item["name"]) for item in unreferenced}),
        "pages": sum(len(rubric["pages"]) for owner in versions for rubric in owner["rubrics"]),
    })
    scope = [site.get("label") or site["url"] for site in sites]
    html, print_html = _render_html(versions=versions, within_days=within_days, stats=stats, summary=summary, thumbs=thumbs,
                                    organization=organization, stamp=stamp, suspect_sites=provenance.get("suspect_sites") or (),
                                    include=include, scope=scope, lang=lang, read_at=provenance.get("read_at"))
    return ReportFiles(
        html=html.encode("utf-8"),
        matches_csv=_csv_bytes(MATCHES_COLUMNS, _occurrence_rows(rows), stamp),
        not_found_csv=_csv_bytes(NOT_FOUND_COLUMNS, _not_found_rows(missing), stamp),
        summary=summary,
        site_read_on=stamp["site_read_on"],
        print_html=print_html.encode("utf-8"),
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
        provenance = db.report_provenance(conn)

    def load(path: str) -> Optional[bytes]:
        file = Path(path)
        return file.read_bytes() if file.is_file() else None

    files = build_report(match_rows, unmatched, within_days=within_days, stats=stats, thumb_loader=load, provenance=provenance)
    paths = (out_dir / "report.html", out_dir / "matches.csv", out_dir / "not_found.csv")
    for path, data in zip(paths, (files.html, files.matches_csv, files.not_found_csv)):
        path.write_bytes(data)
    return paths
