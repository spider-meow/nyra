"""Site crawling: page discovery (sitemaps first, internal links as fallback)
and image extraction (img/srcset/picture/background-image/og:image).

HTML parsing (`extract_images_from_html`, `extract_internal_links`,
`parse_srcset`, `normalize_url`) is deliberately pure — it takes a
rendered HTML string and a base URL, no browser required — so it's
unit-testable without Playwright or network access.

`crawl_site` drives a headless Chromium through Playwright's async API,
several pages at a time, and hands every image it finds to a
`CrawlStore`: the SQLite store for the CLI (`db.LocalStore`) or the
Postgres + Supabase Storage store for the hosted product
(`cloud.store.CloudCrawlStore`). There is one crawler; only persistence
differs.

Every request — robots.txt, sitemaps, images, and everything the browser
loads — goes through `netguard`, so a crawl can't be pointed at a private
address.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import random
import re
import threading
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Collection, Optional, Protocol, Sequence
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
import numpy as np
from bs4 import BeautifulSoup
from PIL import Image

from nyra import fetch, netguard
from nyra.config import Config

IMAGE_TAG_ATTRS = ("src", "data-src", "data-lazy-src", "data-original")
TRACKING_PARAMS = {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_ga", "_gl", "igshid", "yclid"}
IMAGE_DOWNLOADS_PER_PAGE = 6  # images of a page downloaded, processed and stored together: the bytes of a page never pile up (AXE-643)
# Chromium or its driver may die under a page (AXE-646): the browser is started again, up to this many times per read, then the read stops.
MAX_BROWSER_RESTARTS = 3
SHUTDOWN_TIMEOUT_SECONDS = 10  # closing a frozen Chromium is given up after this long
_BROWSER_GONE = ("target closed", "browser has been closed", "connection closed", "context or browser has been closed")

log = logging.getLogger("nyra.crawl")


class BrowserUnavailable(RuntimeError):
    """Chromium keeps dying: the read stops, what it read is kept."""


class _BrowserDied(Exception):
    """Chromium or Playwright's driver stopped under a page (the cause is attached)."""


def browser_died(exc: BaseException) -> bool:
    """Whether `exc` says the browser or Playwright's driver is gone, not that this page is bad. Read from the name and the text: the
    error class (`TargetClosedError`) is not part of Playwright's public API."""
    text = str(exc).lower()
    return type(exc).__name__ == "TargetClosedError" or any(marker in text for marker in _BROWSER_GONE)


# --- URLs -------------------------------------------------------------------

# What a page that answered with another page is recorded as (not 2xx: its links are not tracked).
REDIRECTED = 302
# What a page is recorded as when a consent or age notice still hid it after we tried to close it (not 2xx either).
CONSENT_WALL = -1
CONSENT_WORDS = re.compile(r"cookies?|consent|accept all|tout accepter|accepter et continuer|legal drinking age|âge légal|majeur", re.I)
CONSENT_WALL_MAX_CHARS = 1200


def is_consent_wall(text: str) -> bool:
    """Whether what the page shows, once we tried to close its overlays, is only a consent or age notice: little text, and
    it keeps talking about cookies or consent. A banner over a real page comes with the page's own text, so it is not one."""
    text = text.strip()
    return len(text) <= CONSENT_WALL_MAX_CHARS and len(CONSENT_WORDS.findall(text)) >= 2


def same_page(requested: str, final: str) -> bool:
    """Whether the browser ended on the page asked for (the host with or without `www.`, the path with or without a
    closing slash; the query and the fragment do not make another page)."""
    a, b = urlparse(requested), urlparse(final)

    def host(parsed) -> str:
        return parsed.netloc.lower().removeprefix("www.")

    return host(a) == host(b) and a.path.rstrip("/") == b.path.rstrip("/")


def normalize_url(url: str) -> str:
    """Canonical form used as the page key.

    Drops the fragment and tracking parameters (utm_*, gclid, ...),
    lowercases scheme and host, and gives an empty path a "/", so the same
    page reached through a campaign link isn't crawled twice.
    """
    parsed = urlparse(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    return urlunparse(
        parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            path=parsed.path or "/",
            query=urlencode(query, doseq=True),
            fragment="",
        )
    )


def _bare_host(netloc: str) -> str:
    host = netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, site: str) -> bool:
    """Whether `url` is inside the address `site` (a host, or a full address). Same host, with or without a leading
    www. When the address has a path, only that path counts, on whole segments: `/en-us/` takes `/en-us` and
    `/en-us/a`, never `/en-usa/`. The query and doubled slashes change nothing. No path: the whole domain."""
    base = urlparse(site if "//" in site else f"//{site}")
    if _bare_host(urlparse(url).netloc) != _bare_host(base.netloc):
        return False
    prefix = re.sub("/+", "/", base.path).rstrip("/")
    path = re.sub("/+", "/", urlparse(url).path).rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def overlap_warning(url: str, others: Sequence[str]) -> Optional[str]:
    """A sentence when `url` contains another address of the brand or is contained by one: the wider address still
    reads the narrower one's pages, which are then read twice (and counted twice in the month's pages)."""
    for other in others:
        if same_site(other, url) and not same_site(url, other):
            return f"Cette adresse contient déjà {other} : ses pages seront lues deux fois (et comptées deux fois dans le quota)."
        if same_site(url, other) and not same_site(other, url):
            return f"{other} contient déjà cette adresse : ses pages seront lues deux fois (et comptées deux fois dans le quota)."
    return None


# --- HTML parsing (pure) ------------------------------------------------------

def parse_srcset(srcset: str) -> list[tuple[str, float]]:
    """Parse a srcset attribute into (url, width_or_density) pairs."""
    candidates = []
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        url = tokens[0]
        descriptor = tokens[1] if len(tokens) > 1 else ""
        try:
            if descriptor.endswith("w"):
                value = float(descriptor[:-1])
            elif descriptor.endswith("x"):
                value = float(descriptor[:-1]) * 1000  # density, rank after width
            else:
                value = 0.0
        except ValueError:
            value = 0.0
        candidates.append((url, value))
    return candidates


def pick_largest_srcset_candidate(srcset: str, base_url: str) -> Optional[str]:
    candidates = parse_srcset(srcset)
    if not candidates:
        return None
    best = max(candidates, key=lambda c: c[1])
    return urljoin(base_url, best[0])


def extract_images_from_html(html: str, base_url: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: set[str] = set()

    for img in soup.find_all("img"):
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            picked = pick_largest_srcset_candidate(srcset, base_url)
            if picked:
                urls.add(picked)
                continue
        for attr in IMAGE_TAG_ATTRS:
            value = img.get(attr)
            if value and not value.startswith("data:"):
                urls.add(urljoin(base_url, value))
                break

    for source in soup.find_all("source"):
        srcset = source.get("srcset") or source.get("data-srcset")
        if srcset:
            picked = pick_largest_srcset_candidate(srcset, base_url)
            if picked:
                urls.add(picked)

    for meta_name in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
        for attrs in ({"property": meta_name}, {"name": meta_name}):
            for meta in soup.find_all("meta", attrs=attrs):
                content = meta.get("content")
                if content:
                    urls.add(urljoin(base_url, content))

    return {url for url in urls if urlparse(url).scheme in {"http", "https"}}


def extract_internal_links(html: str, base_url: str, site: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = normalize_url(urljoin(base_url, href))
        if urlparse(absolute).scheme in {"http", "https"} and same_site(absolute, site):
            links.add(absolute)
    return links


# --- robots.txt and sitemaps -------------------------------------------------

def load_robots(site_url: str, client: httpx.Client) -> urllib.robotparser.RobotFileParser:
    parsed = urlparse(site_url)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    parser = urllib.robotparser.RobotFileParser()
    try:
        resp = client.get(robots_url, timeout=10, follow_redirects=True)
        parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
    except httpx.HTTPError:
        parser.parse([])
    return parser


def is_allowed(parser: urllib.robotparser.RobotFileParser, url: str, user_agent: str) -> bool:
    try:
        return parser.can_fetch(user_agent, url)
    except Exception:  # noqa: BLE001 - robots.txt unreadable or malformed: treated as "no restriction", like crawlers do
        return True


MAX_ROBOTS_DELAY_SECONDS = 10  # a site asking for more is read at this pace: one robots.txt cannot stall a read


def robots_delay(parser: urllib.robotparser.RobotFileParser, user_agent: str) -> float:
    """Seconds the site's robots.txt asks for between two requests (`Crawl-delay`), capped; 0 when it asks for none."""
    try:
        return min(float(parser.crawl_delay(user_agent) or 0), MAX_ROBOTS_DELAY_SECONDS)
    except Exception:  # noqa: BLE001 - a malformed value asks for nothing, like is_allowed
        return 0.0


def fetch_sitemap_urls(
    site_url: str,
    client: httpx.Client,
    max_urls: int = 5000,
    robots: Optional[urllib.robotparser.RobotFileParser] = None,
) -> list[str]:
    """Page URLs from the sitemaps robots.txt declares, else the usual locations."""
    parsed = urlparse(site_url)
    declared = list((robots.site_maps() if robots else None) or [])
    defaults = [urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")) for path in ("/sitemap.xml", "/sitemap_index.xml")]
    seen: set[str] = set()
    urls: list[str] = []
    for sitemap_url in declared or defaults:
        urls.extend(_fetch_sitemap_recursive(sitemap_url, client, max_urls - len(urls), seen))
        if len(urls) >= max_urls:
            break
    return list(dict.fromkeys(urls))[:max_urls]


def _fetch_sitemap_recursive(sitemap_url: str, client: httpx.Client, max_urls: int, seen: set[str]) -> list[str]:
    if max_urls <= 0 or sitemap_url in seen or len(seen) > 50:
        return []
    seen.add(sitemap_url)
    try:
        resp = client.get(sitemap_url, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return []
        body = resp.content
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        root = ET.fromstring(body)
    except (httpx.HTTPError, ET.ParseError, OSError, EOFError):
        return []

    urls: list[str] = []
    # Namespace-agnostic: match any tag named 'sitemap'/'url'/'loc' regardless of xmlns.
    for tag in root.iter():
        if _local_name(tag.tag) == "sitemap":
            loc = _find_child(tag, "loc")
            if loc is not None and loc.text:
                urls.extend(_fetch_sitemap_recursive(loc.text.strip(), client, max_urls - len(urls), seen))
                if len(urls) >= max_urls:
                    return urls[:max_urls]
    for tag in root.iter():
        if _local_name(tag.tag) == "url":
            loc = _find_child(tag, "loc")
            if loc is not None and loc.text:
                urls.append(loc.text.strip())
            if len(urls) >= max_urls:
                break
    return urls[:max_urls]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_child(element: ET.Element, local_name: str) -> Optional[ET.Element]:
    for child in element:
        if _local_name(child.tag) == local_name:
            return child
    return None


# --- in-page scripts ------------------------------------------------------------

BACKGROUND_IMAGE_JS = """
() => {
    const urls = new Set();
    const re = /url\\((['"]?)(.*?)\\1\\)/g;
    document.querySelectorAll('*').forEach(el => {
        const bg = getComputedStyle(el).backgroundImage;
        if (bg && bg !== 'none') {
            let match;
            while ((match = re.exec(bg)) !== null) {
                urls.add(match[2]);
            }
        }
    });
    return Array.from(urls);
}
"""

CLICK_LOAD_MORE_JS = r"""
() => {
    const pattern = /(voir plus|afficher plus|en voir plus|charger plus|load more|show more|see more)/i;
    const candidates = document.querySelectorAll('button, a, [role="button"]');
    for (const el of candidates) {
        const text = (el.textContent || '').trim();
        if (!text || !pattern.test(text)) continue;
        const rect = el.getBoundingClientRect();
        const visible = rect.width > 0 && rect.height > 0 && el.offsetParent !== null;
        if (!visible) continue;
        el.click();
        return true;
    }
    return false;
}
"""

# Cookie banners and age gates hide the page behind an overlay. This tries,
# in order: known consent-manager buttons (refusing when possible), then an
# age gate (fills birth-date fields, picks a country, ticks the box, clicks
# the confirm button), then any button in a cookie banner. Returns how many
# things it clicked; 0 means there was nothing to dismiss.
DISMISS_OVERLAYS_JS = r"""
() => {
    let clicks = 0;
    const visible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const label = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
    const setValue = (input, value) => {
        const proto = input.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
        setter.call(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
    };

    const known = [
        '#onetrust-reject-all-handler', '#didomi-notice-disagree-button', '.didomi-continue-without-agreeing',
        '#CybotCookiebotDialogBodyButtonDecline', '[data-testid="uc-deny-all-button"]', '#axeptio_btn_dismiss',
        '#onetrust-accept-btn-handler', '#didomi-notice-agree-button',
        '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll', '[data-testid="uc-accept-all-button"]',
    ];
    for (const selector of known) {
        const el = document.querySelector(selector);
        if (visible(el)) { el.click(); clicks++; break; }
    }

    const ageWords = /(\bâge\b|\bage\b|\b18\b|\b21\b|majeur|legal drinking|drinking age|date de naissance|date of birth|birth)/i;
    const containers = Array.from(document.querySelectorAll(
        'dialog, [role="dialog"], [aria-modal="true"], [class*="age"], [id*="age"], [class*="gate"], [id*="gate"], [class*="modal"], [class*="overlay"]'
    )).filter((el) => visible(el) && ageWords.test(el.innerText || ''));
    for (const box of containers) {
        for (const input of box.querySelectorAll('input, select')) {
            const hint = [input.name, input.id, input.placeholder, input.getAttribute('aria-label')].join(' ').toLowerCase();
            if (input.tagName === 'SELECT') {
                const options = Array.from(input.options).filter((o) => o.value);
                const pick = options.find((o) => /france|1980/i.test(o.text)) || options[0];
                if (pick) setValue(input, pick.value);
            } else if (input.type === 'checkbox') {
                if (!input.checked) input.click();
            } else if (/year|année|annee|yyyy|aaaa/.test(hint)) {
                setValue(input, '1980');
            } else if (/month|mois|\bmm\b/.test(hint)) {
                setValue(input, '01');
            } else if (/day|jour|\bdd\b|\bjj\b/.test(hint)) {
                setValue(input, '01');
            } else if (input.type === 'date') {
                setValue(input, '1980-01-01');
            }
        }
        const confirm = /^(oui|yes|entrer|enter|valider|confirmer|confirm|continuer|continue|submit|ok|j'ai|je suis|i am|i'm)|(plus de 1[89]|over 1[89]|of legal|âge légal|legal age)/i;
        const button = Array.from(box.querySelectorAll('button, a, [role="button"], input[type="submit"]'))
            .find((el) => visible(el) && confirm.test(label(el)));
        if (button) { button.click(); clicks++; break; }
    }

    const bannerWords = /(cookie|consent|confidentialit|privacy|traceurs)/i;
    const reject = /^(tout refuser|refuser|continuer sans accepter|reject all|reject|decline|deny)$/i;
    const accept = /^(tout accepter|accepter|j'accepte|accept all|accept|agree|i agree|ok|compris)$/i;
    const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]')).filter((el) => {
        if (!visible(el)) return false;
        const box = el.closest('dialog, [role="dialog"], [aria-modal="true"], [class*="cookie"], [id*="cookie"], [class*="consent"], [id*="consent"], [class*="banner"]');
        return box && bannerWords.test(box.innerText || '');
    });
    const choice = buttons.find((el) => reject.test(label(el))) || buttons.find((el) => accept.test(label(el)));
    if (choice) { choice.click(); clicks++; }
    return clicks;
}
"""


async def autoscroll(page, steps: int = 8, pause_ms: int = 250) -> None:
    for _ in range(steps):
        await page.evaluate("window.scrollBy(0, window.innerHeight)")
        await page.wait_for_timeout(pause_ms)


async def click_load_more(page, max_clicks: int = 5, pause_ms: int = 400) -> int:
    """Best-effort: click paginated "load more" controls a bounded number of times.

    Catalog/grid pages often load additional products via a button rather
    than infinite scroll, so `autoscroll` alone misses them. This never
    raises and never blocks the crawl if no such button exists.
    """
    clicked = 0
    for _ in range(max_clicks):
        try:
            found = await page.evaluate(CLICK_LOAD_MORE_JS)
        except Exception:  # noqa: BLE001 - the page navigated or closed under us: stop clicking, keep what was read
            break
        if not found:
            break
        clicked += 1
        await page.wait_for_timeout(pause_ms)
    return clicked


async def dismiss_overlays(page, *, extra_selectors: Sequence[str] = (), rounds: int = 2) -> int:
    """Click through cookie banners, age gates and configured selectors. Never raises."""
    total = 0
    for _ in range(rounds):
        try:
            clicked = await page.evaluate(DISMISS_OVERLAYS_JS)
        except Exception:  # noqa: BLE001 - same: the page is gone or navigating
            break
        total += int(clicked or 0)
        if not clicked:
            break
        await page.wait_for_timeout(600)
    for selector in extra_selectors:
        try:
            await page.click(selector, timeout=2000)
            total += 1
            await page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001 - a configured selector that is absent on this page is normal: next one (the list is finite)
            continue
    return total


# --- the crawl ---------------------------------------------------------------

@dataclass
class CrawlStats:
    pages_visited: int = 0
    pages_queued: int = 0          # known and still to read (not a measurement: grows as links are found)
    images_found: int = 0
    images_stored: int = 0
    images_new: int = 0
    blocked_by_robots: int = 0
    blocked_by_guard: int = 0
    errors: list[str] = field(default_factory=list)
    # Measurements for the insights pages. Times are wall-clock seconds
    # summed over pages or batches (pages render in parallel, so the sums
    # can exceed the crawl's duration).
    sitemap_urls: int = 0
    pages_failed: int = 0
    pages_redirected: int = 0      # the address answered with another page: read, but not recorded as itself
    pages_left_scope: int = 0      # redirected out of the address's path: neither its links nor its images are taken
    pages_ok: int = 0              # read with success: 2xx, the page asked for, no consent wall
    pages_walled: int = 0          # a consent notice still hid the page after we tried to close it (CONSENT_WALL)
    browser_restarts: int = 0      # Chromium (or its driver) died and was started again
    reading: list[str] = field(default_factory=list)  # the pages being read now: what a stopped worker's last heartbeat says (AXE-644)
    http_statuses: dict[str, int] = field(default_factory=dict)
    images_known: int = 0          # already stored under this URL: not downloaded again
    images_duplicate: int = 0      # same bytes already stored under another URL
    images_rejected: int = 0       # too small, undecodable or over the pixel cap
    downloads: int = 0
    downloads_failed: int = 0
    bytes_downloaded: int = 0
    bytes_new: int = 0             # originals stored by this crawl
    thumb_bytes_new: int = 0
    pixels_new: int = 0
    formats_new: dict[str, int] = field(default_factory=dict)
    discover_seconds: float = 0.0
    render_seconds: float = 0.0    # page load, overlays, scrolling, DOM read
    download_seconds: float = 0.0
    process_seconds: float = 0.0   # decode, hashes, thumbnail
    embed_seconds: float = 0.0
    embedded: int = 0
    store_seconds: float = 0.0     # uploads + database writes for new images
    model_load_seconds: float = 0.0  # CLIP weights loaded before the crawl (0 once the process has them)
    duration_seconds: float = 0.0

    def metrics(self) -> dict[str, Any]:
        """The measurements as a JSON-ready dict (everything but the counters crawl_runs already has)."""
        keys = (
            "sitemap_urls", "pages_failed", "http_statuses", "images_known", "images_duplicate", "images_rejected",
            "downloads", "downloads_failed", "bytes_downloaded", "bytes_new", "thumb_bytes_new", "pixels_new",
            "formats_new", "discover_seconds", "render_seconds", "download_seconds", "process_seconds",
            "embed_seconds", "embedded", "store_seconds", "model_load_seconds", "duration_seconds", "blocked_by_guard",
        )
        out = {key: getattr(self, key) for key in keys}
        for key, value in out.items():
            if isinstance(value, float):
                out[key] = round(value, 3)
        return out


# --- health of a read ---------------------------------------------------------

# A read is "suspect" when it looks incomplete: nothing is then marked removed from it (`cloud/presence.py`). Prudent on
# purpose: flagging a good read only keeps pages "unknown" until the next one; missing a bad one announces a removal that
# did not happen.
MIN_KNOWN_IMAGES_SHARE = 0.7   # of the known images the best of the last reads saw again, a read must see again at least this share
MAX_ERROR_SHARE = 0.2          # of the pages read, more than this share failed (403, 429, 5xx, timeout, consent wall)
BASELINE_READS = 3             # the last reads (with a page read) the comparison looks at: a real drop stops being suspect after this many


def read_health(stats: CrawlStats) -> dict[str, Any]:
    """The figures of a finished read: pages read with success (`pages_ok`, counted by the crawler: 2xx, the page asked for,
    no consent wall) and the share of pages in error: no answer, 403, 429, 5xx, or hidden by a consent wall (a wall is
    only recorded on a 2xx page, a failed page has no status: the three are disjoint). Bounded to 1."""
    refused = sum(count for code, count in stats.http_statuses.items() if int(code) in (0, 403, 429) or int(code) >= 500)
    return {"pages_ok": stats.pages_ok,
            "error_share": min(1.0, (stats.pages_failed + refused + stats.pages_walled) / max(stats.pages_visited, 1))}


def suspect_reason(health: dict[str, Any], known_seen: int, baseline_seen: Optional[int]) -> Optional[str]:
    """Why this read cannot be trusted to say an image is gone, or None. `known_seen`: images already known that it saw
    again; `baseline_seen`: the most known images one of the last reads saw again (None or 0: no figure yet, only the
    errors count)."""
    if health["pages_ok"] == 0:
        return "aucune page lue avec succès"
    if health["error_share"] > MAX_ERROR_SHARE:
        return f"{health['error_share']:.0%} des pages en erreur"
    if baseline_seen and known_seen < MIN_KNOWN_IMAGES_SHARE * baseline_seen:
        return f"{known_seen} images déjà connues revues sur {baseline_seen}"
    return None


class CrawlStore(Protocol):
    """What the crawler needs from a persistence layer. Called from one thread at a time."""

    def crawled_urls(self) -> set[str]: ...

    def upsert_page(self, url: str) -> Any: ...

    def mark_page(self, page_id: Any, http_status: int) -> None: ...

    def known_images(self, urls: Sequence[str]) -> dict[str, Any]:
        """url -> image id, for images already downloaded and hashed."""

    def link(self, image_id: Any, page_id: Any) -> None: ...

    def image_by_content_hash(self, digest: str) -> Optional[dict]:
        """An already stored image with the same bytes, to reuse instead of recomputing."""

    def save_duplicate(self, *, url: str, page_id: Any, existing: dict) -> Any: ...

    def save_image(
        self,
        *,
        url: str,
        page_id: Any,
        data: bytes,
        content_type: Optional[str],
        processed: fetch.ProcessedImage,
        embedding: Optional[np.ndarray],
    ) -> Any: ...


Embedder = Callable[[list[Image.Image]], list[np.ndarray]]


class _Crawl:
    def __init__(self, site_url: str, store: CrawlStore, config: Config, *, max_pages: int,
                 embedder: Optional[Embedder], resume: bool, progress, should_stop, follow_links: bool = True,
                 skip: Collection[str] = ()):
        self.site_url = normalize_url(site_url)
        self.netloc = urlparse(self.site_url).netloc
        self.store = store
        self.config = config
        self.max_pages = max_pages
        self.embedder = embedder
        self.resume = resume
        self.follow_links = follow_links
        self.progress = progress
        self.should_stop = should_stop or (lambda: False)
        self.stats = CrawlStats()
        self.lock = threading.Lock()
        self.queue: deque[str] = deque()
        self.queued: set[str] = set()
        self.visited: set[str] = set(skip)  # pages not to read: the ones a stopped read already did, or that killed the worker
        self.skipped_images: set[str] = set()
        self.started = 0
        self.in_flight = 0
        self.robots: Optional[urllib.robotparser.RobotFileParser] = None
        self.generation = 0  # counts the browsers started: a reader whose page belongs to an older one asks for a new page
        self._launch_lock: Optional[asyncio.Lock] = None
        self._playwright = self._browser = self._context = None

    def _locked(self, fn, *args, **kwargs):
        with self.lock:
            return fn(*args, **kwargs)

    def discover(self) -> None:
        cfg = self.config.crawl
        started = time.perf_counter()
        with netguard.client(headers={"User-Agent": cfg.user_agent}) as client:
            robots = load_robots(self.site_url, client)
            self.robots = robots if cfg.respect_robots_txt else None
            sitemap_urls = fetch_sitemap_urls(self.site_url, client, max_urls=max(5000, self.max_pages * 4), robots=robots)
        done = self.store.crawled_urls() if self.resume else set()
        self.visited |= done
        seeds = [normalize_url(url) for url in sitemap_urls if same_site(url, self.site_url)]
        for url in [self.site_url, *seeds]:
            self.enqueue(url)
        self.stats.sitemap_urls = len(seeds)
        self.stats.pages_queued = len(self.queue)
        self.stats.discover_seconds = time.perf_counter() - started

    def seed(self, urls: Sequence[str]) -> None:
        """Read these pages and nothing else: no sitemap, no link followed. An address outside the site is left out."""
        cfg = self.config.crawl
        with netguard.client(headers={"User-Agent": cfg.user_agent}) as client:
            robots = load_robots(self.site_url, client)
        self.robots = robots if cfg.respect_robots_txt else None
        for url in urls:
            if same_site(url, self.site_url):
                self.enqueue(url)  # as stored: it is the key of the page, so the read lands on the same row
        self.stats.pages_queued = len(self.queue)

    def enqueue(self, url: str) -> None:
        if url not in self.visited and url not in self.queued:
            self.queued.add(url)
            self.queue.append(url)

    async def run(self) -> CrawlStats:
        cfg = self.config.crawl
        self._launch_lock = asyncio.Lock()
        try:
            await self._launch()
            async with netguard.async_client(headers={"User-Agent": cfg.user_agent}) as http:
                # A declared Crawl-delay means one page at a time: three readers each waiting it out would still send three requests per delay.
                count = 1 if self._robots_delay() else max(1, cfg.concurrency)
                workers = [asyncio.create_task(self._worker(http)) for _ in range(count)]
                try:
                    await asyncio.gather(*workers)
                except Exception:
                    for task in workers:
                        task.cancel()
                    await asyncio.gather(*workers, return_exceptions=True)  # let them end before the browser is closed under them
                    raise
        finally:
            await self._shutdown()
        return self.stats

    async def _launch(self) -> None:
        """Start Playwright, Chromium and the browsing context (again after a death: the driver is a process of its own)."""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(user_agent=self.config.crawl.user_agent)
        await self._context.route("**/*", self._route)
        self.generation += 1

    async def _shutdown(self) -> None:
        for closer in (self._context and self._context.close, self._browser and self._browser.close,
                       self._playwright and self._playwright.stop):
            if closer is None:
                continue
            try:
                await asyncio.wait_for(closer(), SHUTDOWN_TIMEOUT_SECONDS)
            except Exception as exc:  # noqa: BLE001 - best effort (a timeout too): after a death the browser is already gone, and closing it must not hide why
                log.debug("closing the browser failed: %s", exc)
        self._context = self._browser = self._playwright = None

    async def _relaunch(self, seen: int) -> None:
        """Start the browser again after a death, once however many readers saw it (`seen` is the generation they used)."""
        async with self._launch_lock:
            if self.generation != seen:
                return  # another reader already did
            self.stats.browser_restarts += 1
            if self.stats.browser_restarts > MAX_BROWSER_RESTARTS:
                raise BrowserUnavailable(
                    f"Le navigateur de lecture s'est arrêté {self.stats.browser_restarts} fois pendant la lecture : les pages déjà lues "
                    "sont conservées. Relancez la lecture, ou prévenez l'équipe Nyra.")
            await self._shutdown()
            await self._launch()

    async def _route(self, route) -> None:
        try:
            await self._decide(route)
        except Exception as exc:  # noqa: BLE001 - the request is already gone (page closed, navigation): nothing is left to continue or abort
            log.debug("request %s no longer routable: %s", route.request.url, exc)

    async def _decide(self, route) -> None:
        request = route.request
        # The DOM already says which images exist; downloading them in the
        # browser too would double the traffic. Fonts and video never matter.
        if request.resource_type in {"image", "media", "font"}:
            await route.abort()
            return
        try:
            await netguard.check_url_async(request.url)
        except netguard.BlockedURL:
            self.stats.blocked_by_guard += 1
            await route.abort()
            return
        await route.continue_()

    async def _next_url(self) -> Optional[str]:
        while True:
            if self.should_stop() or self.started >= self.max_pages:
                return None
            if self.queue:
                url = self.queue.popleft()
                if url in self.visited:
                    continue
                self.visited.add(url)
                if self.robots is not None and not is_allowed(self.robots, url, self.config.crawl.user_agent):
                    self.stats.blocked_by_robots += 1
                    continue
                self.started += 1
                return url
            if self.in_flight == 0:
                return None
            await asyncio.sleep(0.2)

    async def _worker(self, http: httpx.AsyncClient) -> None:
        cfg = self.config.crawl
        page, seen, died_before = None, 0, False
        try:
            while True:
                url = await self._next_url()
                if url is None:
                    return
                self.in_flight += 1
                self.stats.reading.append(url)
                if self.progress:
                    self.progress(self.stats)  # the page being read is in the heartbeat before it can kill the worker
                try:
                    if page is None or seen != self.generation:
                        seen = self.generation
                        page = await self._new_page(url)
                    await self._crawl_page(page, http, url)
                    died_before = False
                except _BrowserDied as exc:
                    page = None  # a closed page is never used again
                    # The same message says a page was closed (a popup, `window.close()`): the browser is started again only when it
                    # is disconnected, or when a fresh page died too in a row.
                    if self._browser is None or not self._browser.is_connected() or died_before:
                        log.error("the browser stopped while reading %s (restart %d of %d allowed): %s", url,
                                  self.stats.browser_restarts + 1, MAX_BROWSER_RESTARTS, exc.__cause__)
                        await self._relaunch(seen)
                        died_before = False
                    else:
                        log.warning("the page of %s was closed under the reader: %s", url, exc.__cause__)
                        died_before = True
                finally:
                    self.in_flight -= 1
                    self.stats.reading.remove(url)
                self.stats.pages_queued = len(self.queue) + self.in_flight  # pages in other workers are still to finish
                if self.progress:
                    self.progress(self.stats)
                if self.should_stop():
                    return
                await asyncio.sleep(max(random.uniform(cfg.delay_seconds_min, cfg.delay_seconds_max), self._robots_delay()))
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception as exc:  # noqa: BLE001 - best effort: the browser may be gone, and closing a page must not hide why
                    log.debug("closing a page failed: %s", exc)

    async def _new_page(self, url: str):
        try:
            return await self._context.new_page()
        except Exception as exc:
            if not browser_died(exc):
                raise
            self.stats.errors.append(f"{url}: navigateur arrêté, page ignorée")
            self.stats.pages_failed += 1
            self.stats.pages_visited += 1
            raise _BrowserDied(url) from exc

    def _robots_delay(self) -> float:
        return robots_delay(self.robots, self.config.crawl.user_agent) if self.robots is not None else 0.0

    async def _crawl_page(self, page, http: httpx.AsyncClient, url: str) -> None:
        cfg = self.config.crawl
        page_id = await asyncio.to_thread(self._locked, self.store.upsert_page, url)
        render_started = time.perf_counter()
        try:
            await netguard.check_url_async(url)
            response = await page.goto(url, timeout=cfg.page_load_timeout_ms, wait_until="load")
            if cfg.dismiss_overlays or cfg.pre_actions:
                await dismiss_overlays(page, extra_selectors=cfg.pre_actions, rounds=2 if cfg.dismiss_overlays else 0)
            await autoscroll(page)
            await click_load_more(page)
            await autoscroll(page)
            html = await page.content()
            walled = is_consent_wall(await page.evaluate("document.body ? document.body.innerText : ''"))
            try:
                background_urls = await page.evaluate(BACKGROUND_IMAGE_JS)
            except Exception:  # noqa: BLE001 - CSS backgrounds are a bonus; the page's <img> tags were already read
                background_urls = []
            http_status = response.status if response else 0
            final_url = page.url
        except Exception as exc:  # noqa: BLE001 - one bad page must not kill the crawl
            self.stats.render_seconds += time.perf_counter() - render_started
            self.stats.errors.append(f"{url}: {str(exc).splitlines()[0][:300]}")
            self.stats.pages_failed += 1
            await asyncio.to_thread(self._locked, self.store.mark_page, page_id, 0)
            self.stats.pages_visited += 1
            if browser_died(exc):  # not this page's fault: the reader starts a new browser; the page is left out, not tried again
                raise _BrowserDied(url) from exc
            return
        self.stats.render_seconds += time.perf_counter() - render_started
        status_key = str(http_status)
        self.stats.http_statuses[status_key] = self.stats.http_statuses.get(status_key, 0) + 1

        # /en-us/ sent to /fr/ on the same host: another country's page, so neither its images nor its links are taken.
        left_scope = _bare_host(urlparse(final_url).netloc) == _bare_host(self.netloc) and not same_site(final_url, self.site_url)
        if left_scope:
            self.stats.pages_left_scope += 1
            image_urls = set()
        else:
            image_urls = extract_images_from_html(html, final_url)
            joined = {urljoin(final_url, u) for u in background_urls if u and not u.startswith("data:")}
            image_urls |= {u for u in joined if urlparse(u).scheme in {"http", "https"}}  # as for the HTML images above
        if self.follow_links and not left_scope:
            for link in extract_internal_links(html, final_url, self.site_url):
                self.enqueue(link)

        if not await self._collect_images(http, sorted(image_urls), page_id):
            return  # stopped between two waves: the page stays `pending`, so a read that starts again reads it whole
        # A page that sent the reader elsewhere says nothing about what the requested page shows: recorded as a redirection.
        if not same_page(url, final_url):
            self.stats.pages_redirected += 1
            http_status = REDIRECTED
        elif walled and 200 <= http_status < 300:
            self.stats.pages_walled += 1
            http_status = CONSENT_WALL
        elif 200 <= http_status < 300:
            self.stats.pages_ok += 1
        await asyncio.to_thread(self._locked, self.store.mark_page, page_id, http_status)
        self.stats.pages_visited += 1

    async def _collect_images(self, http: httpx.AsyncClient, image_urls: list[str], page_id: Any) -> bool:
        """Download and store the page's images; False when the read was stopped before all of them were."""
        cfg = self.config.crawl
        candidates = [url for url in image_urls if url not in self.skipped_images]
        self.stats.images_found += len(image_urls)
        known = await asyncio.to_thread(self._locked, self.store.known_images, candidates)
        self.stats.images_known += len(known)
        for image_id in known.values():
            await asyncio.to_thread(self._locked, self.store.link, image_id, page_id)
            self.stats.images_stored += 1
        to_fetch = [url for url in candidates if url not in known]

        async def grab(url: str):
            result = await fetch.adownload(url, http, timeout=cfg.request_timeout_seconds, max_bytes=cfg.max_image_bytes)
            if result is None:
                self.skipped_images.add(url)
                self.stats.downloads_failed += 1
                return None
            self.stats.bytes_downloaded += len(result[0])
            return url, result[0], result[1]

        # A wave at a time: downloaded, processed, embedded and stored before the next one, so the bytes and the decoded images of a
        # page never pile up (a page of large images was enough to kill the worker for lack of memory: AXE-643).
        for start in range(0, len(to_fetch), IMAGE_DOWNLOADS_PER_PAGE):
            if self.should_stop():
                return False
            wave = to_fetch[start:start + IMAGE_DOWNLOADS_PER_PAGE]
            download_started = time.perf_counter()
            downloads = [item for item in await asyncio.gather(*(grab(url) for url in wave)) if item]
            self.stats.downloads += len(wave)
            self.stats.download_seconds += time.perf_counter() - download_started
            if downloads:
                await asyncio.to_thread(self._locked, self._store_downloads, downloads, page_id)
        return True

    def _store_downloads(self, downloads: list[tuple[str, bytes, Optional[str]]], page_id: Any) -> None:
        cfg = self.config.crawl
        stats = self.stats
        fresh: list[tuple[str, bytes, Optional[str], fetch.ProcessedImage]] = []
        batch_by_hash: dict[str, str] = {}
        for url, data, content_type in downloads:
            digest = fetch.content_hash(data)
            existing = self.store.image_by_content_hash(digest)
            if existing is not None:
                self.store.save_duplicate(url=url, page_id=page_id, existing=existing)
                stats.images_stored += 1
                stats.images_duplicate += 1
                continue
            if digest in batch_by_hash:
                self.skipped_images.add(url)
                stats.images_duplicate += 1
                continue
            process_started = time.perf_counter()
            processed = fetch.process_image(data, content_type, min_side_px=cfg.min_image_side_px, max_pixels=cfg.max_image_pixels)
            stats.process_seconds += time.perf_counter() - process_started
            if processed is None:
                self.skipped_images.add(url)
                stats.images_rejected += 1
                continue
            batch_by_hash[digest] = url
            fresh.append((url, data, content_type, processed))

        embeddings: list[Optional[np.ndarray]] = [None] * len(fresh)
        if self.embedder and fresh:
            embed_started = time.perf_counter()
            embeddings = list(self.embedder([item[3].rgb for item in fresh]))
            stats.embed_seconds += time.perf_counter() - embed_started
            stats.embedded += len(fresh)
        store_started = time.perf_counter()
        for (url, data, content_type, processed), embedding in zip(fresh, embeddings):
            self.store.save_image(url=url, page_id=page_id, data=data, content_type=content_type,
                                  processed=processed, embedding=embedding)
            stats.images_stored += 1
            stats.images_new += 1
            stats.bytes_new += len(data)
            stats.thumb_bytes_new += len(processed.thumbnail)
            stats.pixels_new += processed.width * processed.height
            fmt = processed.extension.lstrip(".") or "autre"
            stats.formats_new[fmt] = stats.formats_new.get(fmt, 0) + 1
        if fresh:
            stats.store_seconds += time.perf_counter() - store_started


def crawl_site(
    site_url: str,
    store: CrawlStore,
    config: Config,
    *,
    max_pages: Optional[int] = None,
    embedder: Optional[Embedder] = None,
    resume: bool = True,
    progress: Optional[Callable[[CrawlStats], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    skip: Collection[str] = (),
) -> CrawlStats:
    """Crawl a site: sitemap seeds and the home page, then internal links, up to max_pages.

    Skips pages already crawled when resume=True, and the pages in `skip` (read by an earlier run of the same job, or that stopped
    the worker). Raises netguard.BlockedURL if the site itself resolves to a non-public address.
    """
    netguard.check_url(site_url)
    limit = min(max_pages or config.crawl.max_pages, config.crawl.max_pages_limit)
    crawl = _Crawl(site_url, store, config, max_pages=limit, embedder=embedder, resume=resume,
                   progress=progress, should_stop=should_stop, skip=skip)
    started = time.perf_counter()
    crawl.discover()
    stats = asyncio.run(crawl.run())
    stats.duration_seconds = time.perf_counter() - started
    return stats


def crawl_pages(
    site_url: str,
    urls: Sequence[str],
    store: CrawlStore,
    config: Config,
    *,
    embedder: Optional[Embedder] = None,
    progress: Optional[Callable[[CrawlStats], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    skip: Collection[str] = (),
) -> CrawlStats:
    """Read only these pages of a site again (a person changed a visual on them): the same read as `crawl_site`, with no
    sitemap and no link followed, and at most as many pages as addresses given."""
    netguard.check_url(site_url)
    crawl = _Crawl(site_url, store, config, max_pages=len(urls), embedder=embedder, resume=False, progress=progress,
                   should_stop=should_stop, follow_links=False, skip=skip)
    crawl.seed(urls)
    started = time.perf_counter()
    stats = asyncio.run(crawl.run())
    stats.duration_seconds = time.perf_counter() - started
    return stats
