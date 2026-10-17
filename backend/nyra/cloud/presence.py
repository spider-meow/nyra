"""Where a visual of the library is still online, page by page, from what the last finished read of each site saw.

For each page that shows a file matched to the reference (false positives left out):
`still`  the last read of the page (a full read of the site, or a check of that page alone) saw the image on it;
`gone`   the page was read again with success and the image was not on it, or the page itself is gone (404, 410);
`unknown` the page was not read again (its read began and did not end, a read that resumed, the page cap, an error) or has not been since page tracking
         exists (`pages.links_tracked`, `image_pages.last_seen`): never taken for "gone".
Nothing is stored: the images and links are never deleted when they disappear from a site, so the state is worked out
from the dates of the last read. A check of one page counts once a full read of its site has finished, and a later full read that does not read the page
again sets it back to unknown. A page that answered with another page (a redirection) is unknown too. Every photo not set aside as a false positive counts, confirmed or not. Limits: an image
a read does not see on a page that loaded (loaded late by a script) reads as gone; the list of visuals (`by_visual`)
works out every page of the brand at once: measured 31 ms for 592 matches and 14,210 (reference, page) pairs, and it grows with
them, so a few hundred thousand pairs is where it needs a cache.
"""

from __future__ import annotations

import uuid
from typing import Optional

import psycopg

Row = dict
STATES = ("still", "gone", "unknown")
MAX_PAGES_PER_REQUEST = 100

def _pages(only_reference: bool) -> str:
    """One row per page and reference (the brand's every reference when `only_reference` is False, so the index on
    `matches.reference_id` is used for one); `section` is the first segment of the path ("/blogs"), "/" for the home page."""
    return f"""
    SELECT m.reference_id, p.id AS page_id, p.url, s.id AS site_id, bool_or(pr.page_id IS NOT NULL) AS marked,
           COALESCE(bool_or(p.read_started_at > pr.marked_at), false) AS checked,
           '/' || split_part(split_part(regexp_replace(p.url, '^https?://[^/]+', ''), '?', 1), '/', 2) AS section,
           CASE WHEN p.status = 'done' AND p.links_tracked AND p.crawled_at >= rd.read_at AND COALESCE(bool_or(ip.last_seen >= p.read_started_at), false) THEN 'still'
                WHEN p.status = 'done' AND (p.links_tracked OR p.http_status IN (404, 410)) AND p.crawled_at >= rd.read_at THEN 'gone'
                ELSE 'unknown' END AS state
    FROM matches m
    JOIN site_images si ON si.id = m.site_image_id
    JOIN sites s ON s.id = si.site_id
    JOIN image_pages ip ON ip.image_id = si.id
    JOIN pages p ON p.id = ip.page_id
    LEFT JOIN reviews v ON v.reference_id = m.reference_id AND v.site_image_id = m.site_image_id
    LEFT JOIN page_replacements pr ON pr.reference_id = m.reference_id AND pr.page_id = p.id
    LEFT JOIN LATERAL (SELECT max(c.started_at) AS read_at FROM crawl_runs c WHERE c.site_id = s.id AND c.status = 'done') rd ON true
    WHERE {"m.reference_id = %(ref)s" if only_reference else "TRUE"} AND s.brand_id = %(brand)s AND v.decision IS DISTINCT FROM 'ecarte'
    GROUP BY m.reference_id, p.id, p.url, s.id, p.status, p.links_tracked, p.http_status, p.crawled_at, p.read_started_at, rd.read_at
"""


def counts(conn: psycopg.Connection, brand_id: uuid.UUID, reference_id: uuid.UUID) -> list[Row]:
    """How many pages are in each state, marked replaced or not, per site version and section:
    (site_id, label, host, section, state, marked, pages)."""
    return conn.execute(
        f"""SELECT g.site_id, COALESCE(NULLIF(st.label, ''), '') AS label, st.url AS site_url, g.section, g.state, g.marked, count(*) AS pages
            FROM ({_pages(True)}) g JOIN sites st ON st.id = g.site_id
            GROUP BY g.site_id, st.label, st.url, g.section, g.state, g.marked""",
        {"ref": reference_id, "brand": brand_id},
    ).fetchall()


def pages(conn: psycopg.Connection, brand_id: uuid.UUID, reference_id: uuid.UUID, *, site: Optional[uuid.UUID] = None,
          section: Optional[str] = None, state: Optional[str] = None, marked: Optional[bool] = None, limit: int = 50,
          offset: int = 0) -> list[Row]:
    """`limit` pages from `offset` (at most `MAX_PAGES_PER_REQUEST`), by address, narrowed to a site version, a section, a
    state and whether they were marked replaced. `checked`: the page was read again after it was marked, so its state is
    the answer to the mark (before that, the state is about the page as it was)."""
    return conn.execute(
        f"""SELECT g.url, g.site_id, g.section, g.state, g.marked, g.checked FROM ({_pages(True)}) g
            WHERE (%(site)s::uuid IS NULL OR g.site_id = %(site)s) AND (%(section)s::text IS NULL OR g.section = %(section)s)
              AND (%(state)s::text IS NULL OR g.state = %(state)s) AND (%(marked)s::boolean IS NULL OR g.marked = %(marked)s)
            ORDER BY g.url LIMIT %(limit)s OFFSET %(offset)s""",
        {"ref": reference_id, "brand": brand_id, "site": site, "section": section, "state": state, "marked": marked,
         "limit": min(limit, MAX_PAGES_PER_REQUEST), "offset": offset},
    ).fetchall()


def summary(rows: list[Row]) -> dict:
    """The counts of `counts`, as totals and per site version (each with its sections, the most pages still online first);
    `marked` is how many of the pages a person marked replaced, whatever their state."""
    totals = {**dict.fromkeys(STATES, 0), "marked": 0}
    versions: dict[uuid.UUID, dict] = {}
    for row in rows:
        version = versions.setdefault(row["site_id"], {"site_id": str(row["site_id"]), "label": row["label"], "site_url": row["site_url"],
                                                       **dict.fromkeys(STATES, 0), "marked": 0, "sections": {}})
        section = version["sections"].setdefault(row["section"], {"section": row["section"], **dict.fromkeys(STATES, 0), "marked": 0})
        for counter in (totals, version, section):
            counter[row["state"]] += row["pages"]
            counter["marked"] += row["pages"] if row["marked"] else 0
    for version in versions.values():
        version["sections"] = sorted(version["sections"].values(), key=lambda item: (-item["still"], item["section"]))
    return {"totals": {**totals, "pages": sum(totals[state] for state in STATES)},
            "versions": sorted(versions.values(), key=lambda item: (-item["still"], item["site_url"]))}


def total(rows: list[Row], *, site: Optional[uuid.UUID] = None, section: Optional[str] = None, state: Optional[str] = None,
          marked: Optional[bool] = None) -> int:
    """How many pages `pages` can list for these narrowings, to page through them."""
    return sum(row["pages"] for row in rows if (site is None or row["site_id"] == site) and (section is None or row["section"] == section)
               and (state is None or row["state"] == state) and (marked is None or row["marked"] == marked))


def by_visual(conn: psycopg.Connection, brand_id: uuid.UUID) -> list[Row]:
    """The visuals a person confirmed (a photo decided « à retirer » or « retiré »; unlimited rights are never replaced), with
    the pages in each state, marked replaced or not, and one row with no state for a visual no page is known for:
    (reference, filename, expiry_date, unlimited_rights, thumb_path, storage_path, state, marked, pages)."""
    return conn.execute(
        f"""SELECT r.id AS reference_id, r.filename, r.expiry_date, r.unlimited_rights, r.thumb_path, r.storage_path, g.state, g.marked, count(g.url) AS pages
            FROM reference_images r LEFT JOIN ({_pages(False)}) g ON g.reference_id = r.id
            WHERE r.brand_id = %(brand)s AND NOT r.unlimited_rights AND EXISTS (
                SELECT 1 FROM matches m JOIN reviews v ON v.reference_id = m.reference_id AND v.site_image_id = m.site_image_id
                WHERE m.reference_id = r.id AND v.decision IN ('retenu', 'traite'))
            GROUP BY r.id, r.filename, r.expiry_date, r.unlimited_rights, r.thumb_path, r.storage_path, g.state, g.marked""",
        {"brand": brand_id},
    ).fetchall()


def visuals(rows: list[Row]) -> list[dict]:
    """One entry per visual from `by_visual`: its pages per state, and whether it is replaced: every page was read again
    and none shows it (a page not read again is never taken for gone, so a visual not measured yet, or with no page known, is not replaced)."""
    found: dict[uuid.UUID, dict] = {}
    for row in rows:
        item = found.setdefault(row["reference_id"], {"row": row, **dict.fromkeys(STATES, 0), "marked": 0})
        if row["state"]:
            item[row["state"]] += row["pages"]
            item["marked"] += row["pages"] if row["marked"] else 0
    return [{"row": item["row"], **{state: item[state] for state in STATES}, "marked": item["marked"], "pages": sum(item[state] for state in STATES),
             "replaced": sum(item[state] for state in STATES) > 0 and item["still"] == 0 and item["unknown"] == 0} for item in found.values()]


def pages_showing(conn: psycopg.Connection, brand_id: uuid.UUID, reference_id: uuid.UUID, urls: list[str]) -> dict[str, uuid.UUID]:
    """Among `urls`, the pages that show this visual: address -> page."""
    rows = conn.execute(f"SELECT g.url, g.page_id FROM ({_pages(True)}) g WHERE g.url = ANY(%(urls)s)",
                        {"ref": reference_id, "brand": brand_id, "urls": urls}).fetchall()
    return {row["url"]: row["page_id"] for row in rows}


def set_replaced(conn: psycopg.Connection, org_id: uuid.UUID, reference_id: uuid.UUID, page_ids: list[uuid.UUID], *, replaced: bool,
                 user_id: Optional[uuid.UUID]) -> None:
    """Mark these pages as replaced for the visual, or take the mark off."""
    if not replaced:
        conn.execute("DELETE FROM page_replacements WHERE reference_id = %s AND page_id = ANY(%s)", (reference_id, page_ids))
        return
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO page_replacements (reference_id, page_id, org_id, marked_by) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        [(reference_id, page_id, org_id, user_id) for page_id in page_ids])
