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
import random
import threading
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Sequence
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
import numpy as np
from bs4 import BeautifulSoup
from PIL import Image

from nyra import fetch, netguard
from nyra.config import Config

IMAGE_TAG_ATTRS = ("src", "data-src", "data-lazy-src", "data-original")
TRACKING_PARAMS = {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_ga", "_gl", "igshid", "yclid"}
IMAGE_DOWNLOADS_PER_PAGE = 6


# --- URLs -------------------------------------------------------------------

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


def same_site(url: str, site_netloc: str) -> bool:
    """Same host, with or without a leading www."""
    return _bare_host(urlparse(url).netloc) == _bare_host(site_netloc)


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


def extract_internal_links(html: str, base_url: str, site_netloc: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = normalize_url(urljoin(base_url, href))
        if urlparse(absolute).scheme in {"http", "https"} and same_site(absolute, site_netloc):
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
    except Exception:
        return True


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
        except Exception:
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
        except Exception:
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
        except Exception:
            continue
    return total


# --- the crawl ---------------------------------------------------------------

@dataclass
class CrawlStats:
    pages_visited: int = 0
    images_found: int = 0
    images_stored: int = 0
    images_new: int = 0
    blocked_by_robots: int = 0
    blocked_by_guard: int = 0
    errors: list[str] = field(default_factory=list)


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
                 embedder: Optional[Embedder], resume: bool, progress, should_stop):
        self.site_url = normalize_url(site_url)
        self.netloc = urlparse(self.site_url).netloc
        self.store = store
        self.config = config
        self.max_pages = max_pages
        self.embedder = embedder
        self.resume = resume
        self.progress = progress
        self.should_stop = should_stop or (lambda: False)
        self.stats = CrawlStats()
        self.lock = threading.Lock()
        self.queue: deque[str] = deque()
        self.queued: set[str] = set()
        self.visited: set[str] = set()
        self.skipped_images: set[str] = set()
        self.started = 0
        self.in_flight = 0
        self.robots: Optional[urllib.robotparser.RobotFileParser] = None

    def _locked(self, fn, *args, **kwargs):
        with self.lock:
            return fn(*args, **kwargs)

    def discover(self) -> None:
        cfg = self.config.crawl
        with netguard.client(headers={"User-Agent": cfg.user_agent}) as client:
            robots = load_robots(self.site_url, client)
            self.robots = robots if cfg.respect_robots_txt else None
            sitemap_urls = fetch_sitemap_urls(self.site_url, client, max_urls=max(5000, self.max_pages * 4), robots=robots)
        done = self.store.crawled_urls() if self.resume else set()
        self.visited |= done
        seeds = [normalize_url(url) for url in sitemap_urls if same_site(url, self.netloc)]
        for url in [self.site_url, *seeds]:
            self.enqueue(url)

    def enqueue(self, url: str) -> None:
        if url not in self.visited and url not in self.queued:
            self.queued.add(url)
            self.queue.append(url)

    async def run(self) -> CrawlStats:
        from playwright.async_api import async_playwright

        cfg = self.config.crawl
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=cfg.user_agent)
            await context.route("**/*", self._route)
            async with netguard.async_client(headers={"User-Agent": cfg.user_agent}) as http:
                workers = [asyncio.create_task(self._worker(context, http)) for _ in range(max(1, cfg.concurrency))]
                await asyncio.gather(*workers)
            await context.close()
            await browser.close()
        return self.stats

    async def _route(self, route) -> None:
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

    async def _worker(self, context, http: httpx.AsyncClient) -> None:
        cfg = self.config.crawl
        page = await context.new_page()
        try:
            while True:
                url = await self._next_url()
                if url is None:
                    return
                self.in_flight += 1
                try:
                    await self._crawl_page(page, http, url)
                finally:
                    self.in_flight -= 1
                if self.progress:
                    self.progress(self.stats)
                if self.should_stop():
                    return
                await asyncio.sleep(random.uniform(cfg.delay_seconds_min, cfg.delay_seconds_max))
        finally:
            await page.close()

    async def _crawl_page(self, page, http: httpx.AsyncClient, url: str) -> None:
        cfg = self.config.crawl
        page_id = await asyncio.to_thread(self._locked, self.store.upsert_page, url)
        try:
            await netguard.check_url_async(url)
            response = await page.goto(url, timeout=cfg.page_load_timeout_ms, wait_until="load")
            if cfg.dismiss_overlays or cfg.pre_actions:
                await dismiss_overlays(page, extra_selectors=cfg.pre_actions, rounds=2 if cfg.dismiss_overlays else 0)
            await autoscroll(page)
            await click_load_more(page)
            await autoscroll(page)
            html = await page.content()
            try:
                background_urls = await page.evaluate(BACKGROUND_IMAGE_JS)
            except Exception:
                background_urls = []
            http_status = response.status if response else 0
            final_url = page.url
        except Exception as exc:  # noqa: BLE001 - one bad page must not kill the crawl
            self.stats.errors.append(f"{url}: {str(exc).splitlines()[0][:300]}")
            await asyncio.to_thread(self._locked, self.store.mark_page, page_id, 0)
            self.stats.pages_visited += 1
            return

        image_urls = extract_images_from_html(html, final_url)
        image_urls |= {urljoin(final_url, u) for u in background_urls if u and not u.startswith("data:")}
        for link in extract_internal_links(html, final_url, self.netloc):
            self.enqueue(link)

        await self._collect_images(http, sorted(image_urls), page_id)
        await asyncio.to_thread(self._locked, self.store.mark_page, page_id, http_status)
        self.stats.pages_visited += 1

    async def _collect_images(self, http: httpx.AsyncClient, image_urls: list[str], page_id: Any) -> None:
        cfg = self.config.crawl
        candidates = [url for url in image_urls if url not in self.skipped_images]
        self.stats.images_found += len(image_urls)
        known = await asyncio.to_thread(self._locked, self.store.known_images, candidates)
        for image_id in known.values():
            await asyncio.to_thread(self._locked, self.store.link, image_id, page_id)
            self.stats.images_stored += 1
        to_fetch = [url for url in candidates if url not in known]
        semaphore = asyncio.Semaphore(IMAGE_DOWNLOADS_PER_PAGE)

        async def grab(url: str):
            async with semaphore:
                result = await fetch.adownload(url, http, timeout=cfg.request_timeout_seconds, max_bytes=cfg.max_image_bytes)
            if result is None:
                self.skipped_images.add(url)
                return None
            return url, result[0], result[1]

        downloads = [item for item in await asyncio.gather(*(grab(url) for url in to_fetch)) if item]
        if downloads:
            await asyncio.to_thread(self._locked, self._store_downloads, downloads, page_id)

    def _store_downloads(self, downloads: list[tuple[str, bytes, Optional[str]]], page_id: Any) -> None:
        cfg = self.config.crawl
        fresh: list[tuple[str, bytes, Optional[str], fetch.ProcessedImage]] = []
        batch_by_hash: dict[str, str] = {}
        for url, data, content_type in downloads:
            digest = fetch.content_hash(data)
            existing = self.store.image_by_content_hash(digest)
            if existing is not None:
                self.store.save_duplicate(url=url, page_id=page_id, existing=existing)
                self.stats.images_stored += 1
                continue
            if digest in batch_by_hash:
                self.skipped_images.add(url)
                continue
            processed = fetch.process_image(data, content_type, min_side_px=cfg.min_image_side_px, max_pixels=cfg.max_image_pixels)
            if processed is None:
                self.skipped_images.add(url)
                continue
            batch_by_hash[digest] = url
            fresh.append((url, data, content_type, processed))

        embeddings: list[Optional[np.ndarray]] = [None] * len(fresh)
        if self.embedder and fresh:
            embeddings = list(self.embedder([item[3].rgb for item in fresh]))
        for (url, data, content_type, processed), embedding in zip(fresh, embeddings):
            self.store.save_image(url=url, page_id=page_id, data=data, content_type=content_type,
                                  processed=processed, embedding=embedding)
            self.stats.images_stored += 1
            self.stats.images_new += 1


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
) -> CrawlStats:
    """Crawl a site: sitemap seeds and the home page, then internal links, up to max_pages.

    Skips pages already crawled when resume=True. Raises netguard.BlockedURL
    if the site itself resolves to a non-public address.
    """
    netguard.check_url(site_url)
    limit = min(max_pages or config.crawl.max_pages, config.crawl.max_pages_limit)
    crawl = _Crawl(site_url, store, config, max_pages=limit, embedder=embedder, resume=resume,
                   progress=progress, should_stop=should_stop)
    crawl.discover()
    return asyncio.run(crawl.run())
