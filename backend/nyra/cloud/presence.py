"""Where a visual of the library is still online, page by page, from what the last finished read of each site saw.

For each page that shows a file matched to the reference (false positives left out):
`still`  the last read saw the image on this page;
`gone`   the page was read again with success and the image was not on it;
`unknown` the page was not read again (a read that resumed, the page cap, an error) or has not been since page tracking
         exists (`pages.links_tracked`, `image_pages.last_seen`): never taken for "gone".
Nothing is stored: the images and links are never deleted when they disappear from a site, so the state is worked out
from the dates of the last read.
"""

from __future__ import annotations

import uuid
from typing import Optional

import psycopg

Row = dict
STATES = ("still", "gone", "unknown")
MAX_PAGES_PER_REQUEST = 100

# One row per page; `section` is the first segment of its path ("/blogs"), "/" for the home page.
_PAGES = """
    SELECT p.url, s.id AS site_id,
           '/' || split_part(split_part(regexp_replace(p.url, '^https?://[^/]+', ''), '?', 1), '/', 2) AS section,
           CASE WHEN COALESCE(bool_or(ip.last_seen >= rd.read_at), false) THEN 'still'
                WHEN p.links_tracked AND p.crawled_at >= rd.read_at THEN 'gone'
                ELSE 'unknown' END AS state
    FROM matches m
    JOIN site_images si ON si.id = m.site_image_id
    JOIN sites s ON s.id = si.site_id
    JOIN image_pages ip ON ip.image_id = si.id
    JOIN pages p ON p.id = ip.page_id
    LEFT JOIN reviews v ON v.reference_id = m.reference_id AND v.site_image_id = m.site_image_id
    LEFT JOIN LATERAL (SELECT max(c.started_at) AS read_at FROM crawl_runs c WHERE c.site_id = s.id AND c.status = 'done') rd ON true
    WHERE m.reference_id = %(ref)s AND s.brand_id = %(brand)s AND v.decision IS DISTINCT FROM 'ecarte'
    GROUP BY p.id, p.url, s.id, p.links_tracked, p.crawled_at, rd.read_at
"""


def counts(conn: psycopg.Connection, brand_id: uuid.UUID, reference_id: uuid.UUID) -> list[Row]:
    """How many pages are in each state, per site version and section: (site_id, label, host, section, state, pages)."""
    return conn.execute(
        f"""SELECT g.site_id, COALESCE(NULLIF(st.label, ''), '') AS label, st.url AS site_url, g.section, g.state, count(*) AS pages
            FROM ({_PAGES}) g JOIN sites st ON st.id = g.site_id
            GROUP BY g.site_id, st.label, st.url, g.section, g.state""",
        {"ref": reference_id, "brand": brand_id},
    ).fetchall()


def pages(conn: psycopg.Connection, brand_id: uuid.UUID, reference_id: uuid.UUID, *, site: Optional[uuid.UUID] = None,
          section: Optional[str] = None, state: Optional[str] = None, limit: int = 50, offset: int = 0) -> list[Row]:
    """`limit` pages from `offset` (at most `MAX_PAGES_PER_REQUEST`), by address, narrowed to a site version, a section and a state."""
    return conn.execute(
        f"""SELECT g.url, g.site_id, g.section, g.state FROM ({_PAGES}) g
            WHERE (%(site)s::uuid IS NULL OR g.site_id = %(site)s) AND (%(section)s::text IS NULL OR g.section = %(section)s)
              AND (%(state)s::text IS NULL OR g.state = %(state)s)
            ORDER BY g.url LIMIT %(limit)s OFFSET %(offset)s""",
        {"ref": reference_id, "brand": brand_id, "site": site, "section": section, "state": state,
         "limit": min(limit, MAX_PAGES_PER_REQUEST), "offset": offset},
    ).fetchall()


def summary(rows: list[Row]) -> dict:
    """The counts of `counts`, as totals and per site version (each with its sections, the most pages still online first)."""
    totals = dict.fromkeys(STATES, 0)
    versions: dict[uuid.UUID, dict] = {}
    for row in rows:
        totals[row["state"]] += row["pages"]
        version = versions.setdefault(row["site_id"], {"site_id": str(row["site_id"]), "label": row["label"], "site_url": row["site_url"],
                                                       **dict.fromkeys(STATES, 0), "sections": {}})
        version[row["state"]] += row["pages"]
        section = version["sections"].setdefault(row["section"], {"section": row["section"], **dict.fromkeys(STATES, 0)})
        section[row["state"]] += row["pages"]
    for version in versions.values():
        version["sections"] = sorted(version["sections"].values(), key=lambda item: (-item["still"], item["section"]))
    return {"totals": {**totals, "pages": sum(totals.values())},
            "versions": sorted(versions.values(), key=lambda item: (-item["still"], item["site_url"]))}


def total(rows: list[Row], *, site: Optional[uuid.UUID] = None, section: Optional[str] = None, state: Optional[str] = None) -> int:
    """How many pages `pages` can list for these narrowings, to page through them."""
    return sum(row["pages"] for row in rows if (site is None or row["site_id"] == site)
               and (section is None or row["section"] == section) and (state is None or row["state"] == state))
