"""Site crawling: page discovery (sitemap first, internal links as fallback)
and image extraction (img/srcset/picture/background-image/og:image).

HTML parsing (`extract_images_from_html`, `extract_internal_links`,
`parse_srcset`) is deliberately pure — it takes a rendered HTML string and a
base URL, no browser required — so it's unit-testable without Playwright or
network access. `crawl_site` is the only function that drives an actual
browser and touches the database.
"""

from __future__ import annotations

import random
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from rightswatch import db, fetch
from rightswatch.config import Config

IMAGE_TAG_ATTRS = ("src", "data-src", "data-lazy-src", "data-original")


def normalize_url(url: str) -> str:
    """Strip the fragment so #anchor variants of the same page aren't recrawled."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def same_site(url: str, site_netloc: str) -> bool:
    return urlparse(url).netloc.lower() == site_netloc.lower()


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
        if descriptor.endswith("w"):
            try:
                value = float(descriptor[:-1])
            except ValueError:
                value = 0.0
        elif descriptor.endswith("x"):
            try:
                value = float(descriptor[:-1]) * 1000  # density, rank after width
            except ValueError:
                value = 0.0
        else:
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
        srcset = img.get("srcset")
        if srcset:
            picked = pick_largest_srcset_candidate(srcset, base_url)
            if picked:
                urls.add(picked)
                continue
        for attr in IMAGE_TAG_ATTRS:
            value = img.get(attr)
            if value:
                urls.add(urljoin(base_url, value))
                break

    for source in soup.find_all("source"):
        srcset = source.get("srcset")
        if srcset:
            picked = pick_largest_srcset_candidate(srcset, base_url)
            if picked:
                urls.add(picked)

    for meta_name in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
        for meta in soup.find_all("meta", attrs={"property": meta_name}):
            content = meta.get("content")
            if content:
                urls.add(urljoin(base_url, content))
        for meta in soup.find_all("meta", attrs={"name": meta_name}):
            content = meta.get("content")
            if content:
                urls.add(urljoin(base_url, content))

    return urls


def extract_internal_links(html: str, base_url: str, site_netloc: str) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = normalize_url(urljoin(base_url, href))
        if same_site(absolute, site_netloc) and absolute.split("://", 1)[-1].split("/", 1)[0]:
            links.add(absolute)
    return links


# --- robots.txt -------------------------------------------------------------

def load_robots(site_url: str, client: httpx.Client) -> urllib.robotparser.RobotFileParser:
    parsed = urlparse(site_url)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    parser = urllib.robotparser.RobotFileParser()
    try:
        resp = client.get(robots_url, timeout=10, follow_redirects=True)
        if resp.status_code == 200:
            parser.parse(resp.text.splitlines())
        else:
            parser.parse([])
    except httpx.HTTPError:
        parser.parse([])
    return parser


def is_allowed(parser: urllib.robotparser.RobotFileParser, url: str, user_agent: str) -> bool:
    try:
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True


# --- sitemap discovery --------------------------------------------------

def fetch_sitemap_urls(site_url: str, client: httpx.Client, max_urls: int = 2000) -> list[str]:
    parsed = urlparse(site_url)
    sitemap_url = urlunparse((parsed.scheme, parsed.netloc, "/sitemap.xml", "", "", ""))
    return _fetch_sitemap_recursive(sitemap_url, client, max_urls, seen=set())


def _fetch_sitemap_recursive(
    sitemap_url: str, client: httpx.Client, max_urls: int, seen: set[str]
) -> list[str]:
    if sitemap_url in seen or len(seen) > 50:
        return []
    seen.add(sitemap_url)
    try:
        resp = client.get(sitemap_url, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
    except (httpx.HTTPError, ET.ParseError):
        return []

    urls: list[str] = []
    # Namespace-agnostic: match any tag named 'sitemap'/'url'/'loc' regardless of xmlns.
    for sitemap_tag in root.iter():
        if _local_name(sitemap_tag.tag) == "sitemap":
            loc = _find_child(sitemap_tag, "loc")
            if loc and loc.text:
                urls.extend(_fetch_sitemap_recursive(loc.text.strip(), client, max_urls, seen))
                if len(urls) >= max_urls:
                    return urls[:max_urls]

    for url_tag in root.iter():
        if _local_name(url_tag.tag) == "url":
            loc = _find_child(url_tag, "loc")
            if loc and loc.text:
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


# --- background-image extraction (needs a rendered page) -----------------

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


def autoscroll(page, steps: int = 8, pause_ms: int = 250) -> None:
    for _ in range(steps):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(pause_ms)


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


def click_load_more(page, max_clicks: int = 5, pause_ms: int = 400) -> int:
    """Best-effort: click paginated "load more" controls a bounded number of times.

    Catalog/grid pages often load additional products via a button rather
    than infinite scroll, so `autoscroll` alone misses them. This never
    raises and never blocks the crawl if no such button exists.
    """
    clicked = 0
    for _ in range(max_clicks):
        try:
            found = page.evaluate(CLICK_LOAD_MORE_JS)
        except Exception:
            break
        if not found:
            break
        clicked += 1
        page.wait_for_timeout(pause_ms)
    return clicked


@dataclass
class CrawlStats:
    pages_visited: int = 0
    images_found: int = 0
    images_stored: int = 0
    images_new: int = 0
    blocked_by_robots: int = 0
    errors: list[str] = field(default_factory=list)


def crawl_site(
    site_url: str,
    db_path: Path | str,
    cache_dir: Path | str,
    config: Config,
    *,
    max_pages: Optional[int] = None,
    compute_embeddings: bool = True,
    resume: bool = True,
    progress=None,
    should_stop=None,
) -> CrawlStats:
    """BFS-crawl a site: sitemap seeds first, then internal links, up to max_pages.

    Skips pages already marked 'done' in the DB when resume=True, so an
    interrupted crawl can continue without redoing work.
    """
    from playwright.sync_api import sync_playwright

    db.init_db(db_path)
    cache_dir = Path(cache_dir)
    max_pages = max_pages or config.crawl.max_pages
    site_netloc = urlparse(site_url).netloc
    stats = CrawlStats()

    headers = {"User-Agent": config.crawl.user_agent}
    with httpx.Client(headers=headers) as client:
        robots = load_robots(site_url, client) if config.crawl.respect_robots_txt else None
        sitemap_urls = fetch_sitemap_urls(site_url, client)

    with db.connect(db_path) as conn:
        already_done = db.get_crawled_urls(conn) if resume else set()

        seed_urls = [normalize_url(u) for u in sitemap_urls if same_site(u, site_netloc)]
        if not seed_urls:
            seed_urls = [normalize_url(site_url)]

        queue: list[str] = [u for u in seed_urls if u not in already_done]
        visited: set[str] = set(already_done)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=config.crawl.user_agent)

            with httpx.Client(headers=headers) as image_client:
                while queue and stats.pages_visited < max_pages:
                    url = queue.pop(0)
                    if url in visited:
                        continue
                    visited.add(url)

                    if robots is not None and not is_allowed(robots, url, config.crawl.user_agent):
                        stats.blocked_by_robots += 1
                        continue

                    page_id = db.upsert_page(conn, url, status="pending")

                    page = context.new_page()
                    try:
                        response = page.goto(
                            url, timeout=config.crawl.page_load_timeout_ms, wait_until="load"
                        )
                        autoscroll(page)
                        click_load_more(page)
                        autoscroll(page)
                        html = page.content()
                        try:
                            background_urls = page.evaluate(BACKGROUND_IMAGE_JS)
                        except Exception:
                            background_urls = []
                        http_status = response.status if response else 0
                    except Exception as exc:  # noqa: BLE001 - crawler must not die on one bad page
                        stats.errors.append(f"{url}: {exc}")
                        page.close()
                        db.mark_page_crawled(conn, url, http_status=0)
                        continue

                    image_urls = extract_images_from_html(html, url)
                    image_urls |= {urljoin(url, u) for u in background_urls}
                    internal_links = extract_internal_links(html, url, site_netloc)

                    for image_url in image_urls:
                        stats.images_found += 1
                        image_id, is_new = fetch.fetch_and_store(
                            conn,
                            url=image_url,
                            page_id=page_id,
                            cache_dir=cache_dir,
                            client=image_client,
                            config=config,
                            compute_embeddings=compute_embeddings,
                        )
                        if image_id is not None:
                            stats.images_stored += 1
                            if is_new:
                                stats.images_new += 1

                    for link in internal_links:
                        if link not in visited and link not in queue:
                            queue.append(link)

                    db.mark_page_crawled(conn, url, http_status=http_status)
                    page.close()
                    stats.pages_visited += 1

                    if progress:
                        progress(stats)
                    if should_stop and should_stop():
                        break

                    delay = random.uniform(
                        config.crawl.delay_seconds_min, config.crawl.delay_seconds_max
                    )
                    time.sleep(delay)

            browser.close()

    return stats
