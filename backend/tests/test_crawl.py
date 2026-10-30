"""Unit tests for the pure HTML/sitemap parsing helpers in nyra.crawl.

These don't touch a browser or the network: they operate on HTML strings,
exactly what Playwright would hand back from page.content().
"""

from __future__ import annotations

import asyncio

import pytest

from nyra.crawl import (
    click_load_more,
    dismiss_overlays,
    extract_images_from_html,
    extract_internal_links,
    normalize_url,
    parse_srcset,
    pick_largest_srcset_candidate,
    same_site,
)

BASE_URL = "https://www.remymartin.com/en-us/collection"


def test_normalize_url_strips_fragment():
    assert normalize_url("https://example.com/page#section") == "https://example.com/page"


def test_same_site():
    assert same_site("https://www.remymartin.com/foo", "www.remymartin.com")
    assert not same_site("https://other.com/foo", "www.remymartin.com")


def test_parse_srcset_picks_widest():
    srcset = "img-320.jpg 320w, img-640.jpg 640w, img-1280.jpg 1280w"
    candidates = parse_srcset(srcset)
    assert len(candidates) == 3
    best = pick_largest_srcset_candidate(srcset, BASE_URL)
    assert best == "https://www.remymartin.com/en-us/img-1280.jpg"


def test_parse_srcset_density_descriptor():
    srcset = "img@1x.jpg 1x, img@2x.jpg 2x"
    best = pick_largest_srcset_candidate(srcset, BASE_URL)
    assert best.endswith("img@2x.jpg")


def test_extract_images_img_src():
    html = '<html><body><img src="/media/bottle.jpg" width="800"></body></html>'
    urls = extract_images_from_html(html, BASE_URL)
    assert "https://www.remymartin.com/media/bottle.jpg" in urls


def test_extract_images_img_srcset_over_src():
    html = (
        '<img src="/media/small.jpg" '
        'srcset="/media/small.jpg 320w, /media/large.jpg 1600w">'
    )
    urls = extract_images_from_html(html, BASE_URL)
    assert "https://www.remymartin.com/media/large.jpg" in urls
    assert "https://www.remymartin.com/media/small.jpg" not in urls


def test_extract_images_data_src_lazy_load():
    html = '<img data-src="/media/lazy.jpg" class="lazyload">'
    urls = extract_images_from_html(html, BASE_URL)
    assert "https://www.remymartin.com/media/lazy.jpg" in urls


def test_extract_images_picture_source():
    html = """
    <picture>
      <source srcset="/media/cognac-800.webp 800w, /media/cognac-1600.webp 1600w" type="image/webp">
      <img src="/media/cognac-800.jpg">
    </picture>
    """
    urls = extract_images_from_html(html, BASE_URL)
    assert "https://www.remymartin.com/media/cognac-1600.webp" in urls


def test_extract_images_og_and_twitter_meta():
    html = """
    <head>
      <meta property="og:image" content="/social/share.jpg">
      <meta name="twitter:image" content="https://cdn.remymartin.com/twitter.jpg">
    </head>
    """
    urls = extract_images_from_html(html, BASE_URL)
    assert "https://www.remymartin.com/social/share.jpg" in urls
    assert "https://cdn.remymartin.com/twitter.jpg" in urls


def test_extract_internal_links_filters_external_and_anchors():
    html = """
    <a href="/en-us/products">Products</a>
    <a href="https://www.remymartin.com/en-us/heritage">Heritage</a>
    <a href="https://facebook.com/remymartin">Facebook</a>
    <a href="#top">Top</a>
    <a href="mailto:hello@remymartin.com">Mail</a>
    <a href="javascript:void(0)">JS</a>
    """
    links = extract_internal_links(html, BASE_URL, "www.remymartin.com")
    assert "https://www.remymartin.com/en-us/products" in links
    assert "https://www.remymartin.com/en-us/heritage" in links
    assert not any("facebook" in link for link in links)
    assert not any(link.startswith("mailto:") for link in links)
    assert not any(link.startswith("javascript:") for link in links)


class _FakePage:
    """Duck-types just enough of Playwright's async Page for click_load_more's control flow."""

    def __init__(self, click_results: list[bool]):
        self._results = list(click_results)
        self.evaluate_calls = 0
        self.waited = 0

    async def evaluate(self, _script: str) -> bool:
        self.evaluate_calls += 1
        return self._results.pop(0) if self._results else False

    async def wait_for_timeout(self, _ms: int) -> None:
        self.waited += 1


def test_click_load_more_stops_when_button_disappears():
    page = _FakePage([True, True, False])
    clicked = asyncio.run(click_load_more(page, max_clicks=5))
    assert clicked == 2
    assert page.evaluate_calls == 3
    assert page.waited == 2


def test_click_load_more_respects_max_clicks():
    page = _FakePage([True, True, True, True, True, True])
    clicked = asyncio.run(click_load_more(page, max_clicks=3))
    assert clicked == 3
    assert page.evaluate_calls == 3


def test_click_load_more_never_raises_on_evaluate_failure():
    class ExplodingPage:
        async def evaluate(self, _script: str):
            raise RuntimeError("page closed")

        async def wait_for_timeout(self, _ms: int) -> None:
            pass

    assert asyncio.run(click_load_more(ExplodingPage(), max_clicks=3)) == 0


def test_dismiss_overlays_stops_when_nothing_left_and_never_raises():
    page = _FakePage([2, 0])
    assert asyncio.run(dismiss_overlays(page, rounds=3)) == 2

    class ExplodingPage(_FakePage):
        async def evaluate(self, _script: str):
            raise RuntimeError("closed")

        async def click(self, _selector: str, timeout: int = 0):
            raise RuntimeError("no such element")

    assert asyncio.run(dismiss_overlays(ExplodingPage([]), extra_selectors=["#age-yes"])) == 0


def test_normalize_url_drops_tracking_parameters_and_lowercases_host():
    assert normalize_url("HTTPS://WWW.Example.com/Page?utm_source=x&id=3&gclid=abc#top") == "https://www.example.com/Page?id=3"
    assert normalize_url("https://example.com") == "https://example.com/"


def test_same_site_ignores_www():
    assert same_site("https://remymartin.com/foo", "www.remymartin.com")
    assert same_site("https://www.remymartin.com/foo", "remymartin.com")
    assert not same_site("https://shop.remymartin.com/foo", "www.remymartin.com")


def test_extract_images_skips_data_uris():
    html = '<img src="data:image/png;base64,AAAA">'
    assert extract_images_from_html(html, BASE_URL) == set()
    html = '<img src="data:image/png;base64,AAAA"><img data-src="/real.jpg">'
    assert extract_images_from_html(html, BASE_URL) == {"https://www.remymartin.com/real.jpg"}


def test_netguard_refuses_private_and_local_addresses(monkeypatch):
    from nyra import netguard

    monkeypatch.delenv("NYRA_ALLOW_PRIVATE_HOSTS", raising=False)
    for url in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/x",
                "http://localhost:8000/", "file:///etc/passwd", "http://[::1]/", "http://192.168.1.1/"):
        try:
            netguard.check_url(url)
        except netguard.BlockedURL:
            continue
        raise AssertionError(f"{url} should be refused")
    assert netguard.is_public_ip("8.8.8.8")
    assert not netguard.is_public_ip("100.64.0.1")
    assert not netguard.is_public_ip("::ffff:10.0.0.1")


def test_a_malformed_image_address_is_skipped_not_fatal():
    import asyncio

    import httpx

    from nyra import fetch

    async def run():
        # A client holding a cookie (a crawler's, after its first responses) builds the request differently.
        async with httpx.AsyncClient(cookies={"a": "b"}) as client:
            return await fetch.adownload("/", client, timeout=1.0, max_bytes=1000)

    assert asyncio.run(run()) is None
    with httpx.Client(cookies={"a": "b"}) as client:
        assert fetch.download("/", client, timeout=1.0, max_bytes=1000) is None


def test_a_page_check_queues_only_the_pages_given_and_follows_no_link(monkeypatch):
    from nyra import crawl as crawl_module
    from nyra.config import load_config

    monkeypatch.setattr(crawl_module, "load_robots", lambda *_args, **_kwargs: None)
    config = load_config()
    reading = crawl_module._Crawl("https://t.test/", store=None, config=config, max_pages=3, embedder=None, resume=False,
                                  progress=None, should_stop=None, follow_links=False)
    reading.seed(["https://t.test/a?utm_source=x", "https://t.test/b", "https://other.test/c"])
    assert list(reading.queue) == ["https://t.test/a?utm_source=x", "https://t.test/b"]  # as stored (the page's key), the other site left out
    assert reading.follow_links is False


def test_a_crawl_delay_in_robots_txt_is_read_for_our_name_and_capped():
    import urllib.robotparser

    from nyra.crawl import MAX_ROBOTS_DELAY_SECONDS, robots_delay

    ua = "NyraBot/0.1 (+https://t.test/robot; contact: a@t.test)"

    def parsed(*lines: str) -> urllib.robotparser.RobotFileParser:
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(list(lines))
        return parser

    assert robots_delay(parsed("User-agent: NyraBot", "Crawl-delay: 4"), ua) == 4
    assert robots_delay(parsed("User-agent: *", "Crawl-delay: 3"), ua) == 3  # the general rule applies to us
    assert robots_delay(parsed("User-agent: OtherBot", "Crawl-delay: 9"), ua) == 0  # another robot's rule does not
    assert robots_delay(parsed("User-agent: *", "Crawl-delay: 600"), ua) == MAX_ROBOTS_DELAY_SECONDS
    assert robots_delay(parsed("User-agent: *", "Disallow: /private"), ua) == 0  # no Crawl-delay: our own pace


def test_a_page_that_answered_with_another_page_is_not_the_same_page():
    from nyra.crawl import same_page

    assert same_page("https://t.test/blogs/x", "https://t.test/blogs/x/")  # a closing slash
    assert same_page("https://t.test/blogs/x", "https://www.t.test/blogs/x?lang=fr")  # www, and a query
    assert not same_page("https://t.test/collections", "https://t.test/collections/all")  # another page
    assert not same_page("https://t.test/blogs/x", "https://t.test/")  # sent to the home page
    assert not same_page("https://t.test/blogs/x", "https://other.test/blogs/x")


def test_a_read_is_suspect_when_it_met_errors_or_lost_the_images_it_knew():
    from nyra.crawl import CrawlStats, read_health, suspect_reason

    def reason(known_seen, baseline, **counts):
        stats = CrawlStats(**{"pages_visited": 10, "pages_ok": 10, "http_statuses": {"200": 10}, **counts})
        return suspect_reason(read_health(stats), known_seen, baseline)

    assert reason(95, 100) is None  # a normal read: no false alert
    assert reason(100, None) is None and reason(0, 0) is None  # no earlier figure to compare with
    assert reason(69, 100) and reason(70, 100) is None  # under 70 % of the known images seen again
    assert reason(100, 100, pages_ok=7, http_statuses={"200": 7, "403": 3})  # blocked: 30 % of the pages in error
    assert reason(100, 100, pages_ok=8, http_statuses={"200": 8, "502": 1}, pages_failed=1) is None  # 20 % is the limit
    assert reason(100, 100, pages_ok=5, http_statuses={"200": 5, "429": 1}, pages_failed=4)  # timeouts count as errors
    assert reason(100, 100, pages_ok=0, http_statuses={"403": 10}) == "aucune page lue avec succès"
    assert reason(100, 100, pages_ok=0, pages_walled=10) == "aucune page lue avec succès"  # nothing but consent walls
    assert read_health(CrawlStats(pages_visited=2, pages_failed=2, pages_walled=2, http_statuses={"403": 2}))["error_share"] == 1.0  # bounded


def test_a_page_that_is_only_a_consent_notice_is_a_consent_wall():
    from nyra.crawl import is_consent_wall

    assert is_consent_wall("Nous utilisons des cookies pour améliorer votre expérience. Gérer mes cookies. Tout accepter")
    assert not is_consent_wall("Cookies")  # a footer link alone
    assert not is_consent_wall("Notre carafe. " * 200 + "cookies cookies")  # a banner over a real page comes with its text


def test_an_address_with_a_path_only_takes_the_pages_under_that_path():
    site = "https://Remymartin.com/en-us/"
    for url in ("https://www.remymartin.com/en-us/", "https://remymartin.com/en-us", "https://remymartin.com:443/en-us/a/b",
                "https://remymartin.com/en-us?x=1", "https://remymartin.com//en-us//vins/"):
        assert same_site(url, site), url
    for url in ("https://remymartin.com/en-usa/", "https://remymartin.com/fr/", "https://remymartin.com/", "https://other.com/en-us/"):
        assert not same_site(url, site), url
    assert same_site("https://remymartin.com/fr/", "https://remymartin.com") and same_site("https://remymartin.com/fr/", "https://remymartin.com/")


def test_the_sitemap_the_links_and_the_page_check_stay_under_the_path(monkeypatch):
    from nyra import crawl as crawl_module
    from nyra.config import load_config

    monkeypatch.setattr(crawl_module, "load_robots", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(crawl_module, "fetch_sitemap_urls", lambda *_args, **_kwargs: [
        "https://t.test/en-us/a", "https://t.test/fr/a", "https://t.test/en-usa/"])
    reading = crawl_module._Crawl("https://t.test/en-us/", store=None, config=load_config(), max_pages=9, embedder=None,
                                  resume=False, progress=None, should_stop=None)
    reading.discover()
    assert list(reading.queue) == ["https://t.test/en-us/", "https://t.test/en-us/a"] and reading.stats.sitemap_urls == 1
    html = '<a href="/en-us/b">b</a><a href="/fr/b">fr</a><a href="/en-usa/">usa</a>'
    assert extract_internal_links(html, "https://t.test/en-us/", reading.site_url) == {"https://t.test/en-us/b"}
    other = crawl_module._Crawl("https://t.test/en-us/", store=None, config=load_config(), max_pages=3, embedder=None,
                                resume=False, progress=None, should_stop=None, follow_links=False)
    other.seed(["https://t.test/en-us/x", "https://t.test/fr/x"])
    assert list(other.queue) == ["https://t.test/en-us/x"]


def test_a_page_of_the_path_that_redirects_outside_it_is_told_and_not_read(monkeypatch):
    import asyncio

    from nyra import crawl as crawl_module
    from nyra import netguard
    from nyra.config import load_config

    class Store:
        def upsert_page(self, url): return 1
        def mark_page(self, page_id, status, redirect=None): self.status = status
        def known_images(self, urls): return {}

    class Page:
        url = "https://t.test/fr/"
        async def goto(self, url, **_kw): return type("R", (), {"status": 200})()
        async def content(self): return '<img src="/a.jpg"><a href="/fr/b">b</a>'
        async def evaluate(self, _js): return ""

    async def no_check(_url): return None

    async def no_actions(*_a, **_kw): return None

    monkeypatch.setattr(netguard, "check_url_async", no_check)
    for name in ("dismiss_overlays", "autoscroll", "click_load_more"):
        monkeypatch.setattr(crawl_module, name, no_actions)
    store = Store()
    reading = crawl_module._Crawl("https://t.test/en-us/", store=store, config=load_config(), max_pages=3, embedder=None,
                                  resume=False, progress=None, should_stop=None)
    asyncio.run(reading._crawl_page(Page(), None, "https://t.test/en-us/"))
    assert reading.stats.pages_left_scope == 1 and reading.stats.images_found == 0 and not reading.queue
    assert store.status == crawl_module.REDIRECTED


def test_adding_an_address_warns_when_it_contains_or_is_contained_by_another():
    from nyra.crawl import overlap_warning

    assert overlap_warning("https://t.test/en-us/", ["https://t.test/"]) == (
        "https://t.test/ contient déjà cette adresse : ses pages seront lues deux fois (et comptées deux fois dans le quota).")
    assert "contient déjà https://t.test/en-us/" in overlap_warning("https://t.test/", ["https://t.test/en-us/"])
    assert overlap_warning("https://t.test/en-us/", ["https://t.test/fr/", "https://other.test/"]) is None
    assert overlap_warning("https://t.test/", []) is None


# --- AXE-643 and AXE-646: a page's images in waves, and a browser that dies ---------------------------------------------

def _crawler(**changes):
    from dataclasses import replace

    from nyra import crawl as crawl_module
    from nyra.config import load_config

    config = load_config()
    config = replace(config, crawl=replace(config.crawl, delay_seconds_min=0, delay_seconds_max=0))
    return crawl_module._Crawl("https://t.test/", store=changes.pop("store", None), config=config, max_pages=changes.pop("max_pages", 10),
                               embedder=None, resume=False, progress=None, should_stop=changes.pop("should_stop", None))


def test_the_images_of_a_page_are_downloaded_and_stored_in_waves_so_their_bytes_never_pile_up(monkeypatch):
    from types import SimpleNamespace

    from nyra import crawl as crawl_module

    store = SimpleNamespace(known_images=lambda urls: {}, link=lambda *_args: None)
    reading = _crawler(store=store)
    stored, in_memory = [], []

    async def download(url, _client, **_kwargs):
        in_memory.append(url)
        return b"x", "image/jpeg"

    monkeypatch.setattr(crawl_module.fetch, "adownload", download)

    def keep(downloads, _page_id):
        stored.append(len(downloads))
        in_memory.clear()  # saved: the wave's bytes are let go

    monkeypatch.setattr(reading, "_store_downloads", keep)
    urls = [f"https://t.test/{n}.jpg" for n in range(crawl_module.IMAGE_DOWNLOADS_PER_PAGE * 2 + 1)]
    asyncio.run(reading._collect_images(None, urls, page_id=1))
    assert stored == [crawl_module.IMAGE_DOWNLOADS_PER_PAGE, crawl_module.IMAGE_DOWNLOADS_PER_PAGE, 1] and not in_memory


def test_a_page_stops_downloading_between_waves_when_asked(monkeypatch):
    from types import SimpleNamespace

    from nyra import crawl as crawl_module

    reading = _crawler(store=SimpleNamespace(known_images=lambda urls: {}, link=lambda *_args: None), should_stop=lambda: True)

    async def download(*_args, **_kwargs):
        raise AssertionError("nothing is downloaded once the worker is stopping")

    monkeypatch.setattr(crawl_module.fetch, "adownload", download)
    assert asyncio.run(reading._collect_images(None, ["https://t.test/a.jpg"], page_id=1)) is False  # told: the page is not complete


def test_a_dead_browser_is_told_from_a_bad_page():
    from nyra.crawl import browser_died

    class TargetClosedError(Exception):
        pass

    assert browser_died(TargetClosedError("x"))
    assert browser_died(Exception("Page.goto: Target page, context or browser has been closed"))
    assert browser_died(Exception("Connection closed while reading from the driver"))
    assert not browser_died(Exception("Page.goto: Timeout 30000ms exceeded"))
    assert not browser_died(Exception("net::ERR_NAME_NOT_RESOLVED"))


class _MarkingStore:
    def __init__(self):
        self.marked = []

    def upsert_page(self, url):
        return url

    def mark_page(self, page_id, status, redirect=None):
        self.marked.append((page_id, status))


def test_a_page_that_kills_the_browser_is_left_out_and_the_browser_starts_again(monkeypatch):
    """AXE-646: the page is recorded as failed (never « gone »), the reader asks for a new browser and goes on with the next page."""
    from nyra import crawl as crawl_module

    store = _MarkingStore()
    reading = _crawler(store=store)
    reading.enqueue("https://t.test/kills-the-driver")
    reading.enqueue("https://t.test/fine")
    launches, read = [], []

    async def check(_url):
        return None

    class Page:
        async def goto(self, url, **_kwargs):
            if "kills" in url:
                raise Exception("Page.goto: Target page, context or browser has been closed")
            read.append(url)
            raise RuntimeError("stops the test here: the page after the dead browser was reached")

        async def close(self):
            return None

    class Context:
        async def new_page(self):
            return Page()

    async def launch():
        launches.append(1)
        reading._context = Context()
        reading.generation += 1

    async def shutdown():
        return None

    monkeypatch.setattr(crawl_module.netguard, "check_url_async", check)
    monkeypatch.setattr(reading, "_launch", launch)
    monkeypatch.setattr(reading, "_shutdown", shutdown)
    reading._launch_lock = asyncio.Lock()

    async def run():
        await reading._launch()
        await reading._worker(None)

    asyncio.run(run())
    assert read == ["https://t.test/fine"] and len(launches) == 2  # started once, and again after the death
    assert reading.stats.browser_restarts == 1 and reading.stats.pages_failed == 2  # the dead page, and the test's own stop on the next
    assert store.marked[0] == ("https://t.test/kills-the-driver", 0) and reading.stats.reading == []


def test_a_browser_that_keeps_dying_stops_the_read_with_a_message(monkeypatch):
    from nyra import crawl as crawl_module

    reading = _crawler()
    reading._launch_lock = asyncio.Lock()
    reading.stats.browser_restarts = crawl_module.MAX_BROWSER_RESTARTS
    with pytest.raises(crawl_module.BrowserUnavailable) as stopped:
        asyncio.run(reading._relaunch(reading.generation))
    assert "pages déjà lues sont conservées" in str(stopped.value)


def test_a_page_stopped_between_two_waves_is_not_marked_read(monkeypatch):
    """A stop in the middle of a page must leave it `pending`: marked done, a read that starts again would never read its other images."""
    from nyra import crawl as crawl_module

    store = _MarkingStore()
    reading = _crawler(store=store, should_stop=lambda: True)

    class Page:
        url = "https://t.test/p"

        async def goto(self, *_args, **_kwargs):
            return type("Response", (), {"status": 200})()

        async def content(self):
            return "<html><body>" + "".join(f'<img src="/{n}.jpg">' for n in range(3)) + "</body></html>"

        async def evaluate(self, script):
            return [] if "background" in script.lower() or "BACKGROUND" in script else ""

        async def wait_for_timeout(self, _ms):
            return None

    async def check(_url):
        return None

    async def collected(*_args):
        return False

    monkeypatch.setattr(crawl_module.netguard, "check_url_async", check)
    monkeypatch.setattr(reading, "_collect_images", collected)
    store.known_images = lambda urls: {}
    asyncio.run(reading._crawl_page(Page(), None, "https://t.test/p"))
    assert store.marked == [] and reading.stats.pages_visited == 0


def test_a_page_closed_by_the_site_does_not_restart_the_browser_until_it_happens_twice_in_a_row(monkeypatch):
    from nyra import crawl as crawl_module

    reading = _crawler(store=_MarkingStore())
    for n in range(3):
        reading.enqueue(f"https://t.test/{n}")
    launches = []

    class Browser:
        def is_connected(self):
            return True  # the browser is fine: only pages close

    class Page:
        async def close(self):
            return None

    class Context:
        async def new_page(self):
            return Page()

    async def check(_url):
        return None

    async def crawl_page(_page, _http, url):
        if not url.endswith("/2"):
            reading.stats.pages_failed += 1
            raise crawl_module._BrowserDied(url)

    async def launch():
        launches.append(1)
        reading._browser, reading._context = Browser(), Context()
        reading.generation += 1

    async def shutdown():
        return None

    monkeypatch.setattr(reading, "_crawl_page", crawl_page)
    monkeypatch.setattr(reading, "_launch", launch)
    monkeypatch.setattr(reading, "_shutdown", shutdown)
    reading._launch_lock = asyncio.Lock()

    async def run():
        await reading._launch()
        await reading._worker(None)

    asyncio.run(run())
    # /0 closed its page: new page, no restart; /1 died too in a row: restart; /2 read normally.
    assert len(launches) == 2 and reading.stats.browser_restarts == 1


def _recycling_reading(monkeypatch, pages, every, readers=1):
    """A crawler over a fake browser that counts its starts and records, for each page read, which browser it was opened in and whether it was closed."""
    from nyra import crawl as crawl_module

    reading = _crawler(store=_MarkingStore(), max_pages=pages)
    for n in range(pages):
        reading.enqueue(f"https://t.test/{n}")
    log = {"launches": 0, "opened": [], "closed": [], "stale": []}
    monkeypatch.setattr(crawl_module, "RECYCLE_AFTER_PAGES", every)

    class Page:
        def __init__(self, generation):
            self.generation = generation

        async def close(self):
            log["closed"].append(self)

    class Context:
        async def new_page(self):
            page = Page(reading.generation)
            log["opened"].append(page)
            return page

    async def launch():
        log["launches"] += 1
        reading._context = Context()
        reading.generation += 1
        reading.pages_since_launch = 0

    async def shutdown():
        reading._context = None

    async def crawl_page(page, _http, url):
        await asyncio.sleep(0.01)  # the readers overlap
        if page.generation != reading.generation:
            log["stale"].append(url)  # read in a browser that was already started again

    monkeypatch.setattr(reading, "_launch", launch)
    monkeypatch.setattr(reading, "_shutdown", shutdown)
    monkeypatch.setattr(reading, "_crawl_page", crawl_page)
    reading._launch_lock = asyncio.Lock()

    async def run():
        await reading._launch()
        await asyncio.gather(*[reading._worker(None) for _ in range(readers)])

    asyncio.run(run())
    return reading, log


def test_each_page_is_read_in_a_page_of_its_own_and_closed_after_it(monkeypatch):
    """A page kept for the whole read keeps growing (a heavy site took the worker past its memory limit): one page per address, closed after it."""
    reading, log = _recycling_reading(monkeypatch, pages=5, every=100)
    assert len(log["opened"]) == 5 and len(set(map(id, log["opened"]))) == 5
    assert set(map(id, log["closed"])) == set(map(id, log["opened"])) and reading.stats.reading == []


def test_the_browser_is_started_again_after_a_number_of_pages_and_that_is_not_a_death(monkeypatch):
    reading, log = _recycling_reading(monkeypatch, pages=7, every=3)
    assert log["launches"] == 3  # the first start, and again before the 4th page and before the 7th
    assert reading.stats.browser_restarts == 0 and reading.stats.pages_failed == 0 and not log["stale"]
    assert [page.generation for page in log["opened"]] == [1, 1, 1, 2, 2, 2, 3]


def test_with_several_readers_none_is_in_the_middle_of_a_page_when_the_browser_is_started_again(monkeypatch):
    reading, log = _recycling_reading(monkeypatch, pages=12, every=4, readers=3)
    assert log["launches"] >= 3 and not log["stale"]  # nobody read in a browser that was closed under it
    assert len(log["opened"]) == 12 and len(log["closed"]) == 12 and reading.stats.browser_restarts == 0


# --- AXE-641: where a page that ended elsewhere ended -------------------------------------------------------------------

SITE = "https://t.test/"


@pytest.mark.parametrize("requested, final, hops, status, walled, kind", [
    ("https://t.test/a", "https://t.test/a/", [], 200, False, None),  # the same page
    ("https://t.test/p/old", "https://t.test/p/new", [301], 200, False, "clear"),
    ("https://t.test/p/old", "https://www.t.test/p/new", [308, 301], 200, False, "clear"),  # www is the same host
    ("https://t.test/p/old", "https://t.test/p/new", [302], 200, False, "temporary"),
    ("https://t.test/p/old", "https://t.test/p/new", [301, 307], 200, False, "temporary"),  # one temporary hop is enough
    ("https://t.test/p/old", "https://t.test/p/new", [], 200, False, "script"),  # a script or a meta tag: nothing says it is lasting
    ("https://t.test/p/old", "https://t.test/", [301], 200, False, "home"),  # a deleted page often goes there: ambiguous
    ("https://t.test/p/old", "https://t.test/c/new", [301], 200, False, "other_section"),
    ("https://t.test/en-us/x", "https://t.test/fr/x", [301], 200, False, "other_section"),  # another country
    ("https://t.test/p/old", "https://t.test/p/login?next=/p/old", [302], 200, False, "barrier"),
    ("https://t.test/p/old", "https://t.test/age-gate", [302], 200, False, "barrier"),
    ("https://t.test/p/old", "https://t.test/p/new", [301], 200, True, "barrier"),  # a consent wall hides the target
    ("https://t.test/p/old", "https://t.test/p/new", [301], 404, False, "error"),
    ("https://t.test/p/old", "https://other.test/p/new", [301], 200, False, "other_site"),
    ("https://t.test/p/old", "https://t.test/p/new", [0], 200, False, "temporary"),  # a hop Playwright could not tell is never permanent
    ("https://t.test/fr/produit/x", "https://t.test/fr/", [301], 200, False, "home"),  # a parent: the locale's home, or a category
    ("https://t.test/blogs/news/old", "https://t.test/blogs/news", [301], 200, False, "home"),
    ("https://t.test/p/old", "https://t.test/my-account/orders", [301], 200, False, "barrier"),
    ("https://t.test/p/old", "https://t.test/sign_in", [301], 200, False, "barrier"),
    ("https://t.test/p/old", "https://t.test/select-country", [301], 200, False, "barrier"),
    ("https://t.test/p/old", "https://t.test/index.php?route=account/login", [301], 200, False, "barrier"),  # the query counts
    ("https://t.test/p/old", "https://t.test/pages/x", [301], 404, False, "error"),
])
def test_a_redirected_page_is_told_from_a_barrier_a_home_and_a_replacement(requested, final, hops, status, walled, kind):
    from nyra.crawl import redirect_kind

    assert redirect_kind(requested, final, hops, status, walled, SITE) == kind


def test_an_address_with_a_path_does_not_conclude_for_a_redirect_that_leaves_it():
    from nyra.crawl import redirect_kind

    assert redirect_kind("https://t.test/en-us/p/a", "https://t.test/fr/p/a", [301], 200, False, "https://t.test/en-us/") == "other_section"
    assert redirect_kind("https://t.test/en-us/p/a", "https://t.test/en-us/p/b", [301], 200, False, "https://t.test/en-us/") == "clear"


def test_the_redirects_that_led_to_a_response_are_read_first_hop_first_and_bounded():
    from nyra import crawl as crawl_module

    class Hop:
        def __init__(self, status, before=None):
            self.status, self.redirected_from = status, before

        async def response(self):
            return self if self.status is not None else None

    first = Hop(301)
    last = type("Response", (), {"request": Hop(None, Hop(302, first))})()  # final request <- 302 <- 301
    assert asyncio.run(crawl_module.redirect_hops(last)) == [301, 302]
    assert asyncio.run(crawl_module.redirect_hops(None)) == []
    loop = Hop(301)
    loop.redirected_from = loop  # never ends: the count is bounded
    assert len(asyncio.run(crawl_module.redirect_hops(type("Response", (), {"request": loop})()))) == crawl_module.MAX_REDIRECT_HOPS


def test_a_page_that_ended_elsewhere_is_recorded_with_where_it_ended(monkeypatch):
    from nyra import crawl as crawl_module

    store = _MarkingStore()
    store.mark_page = lambda page_id, status, redirect=None: store.marked.append((page_id, status, redirect))
    reading = _crawler(store=store)
    store.known_images = lambda urls: {}

    class Request:
        redirected_from = None

        def __init__(self, before=None):
            self.redirected_from = before

        async def response(self):
            return type("Hop", (), {"status": 301})()

    class Page:
        url = "https://t.test/p/new"

        async def goto(self, *_args, **_kwargs):
            return type("Response", (), {"status": 200, "request": Request(Request())})()

        async def content(self):
            return "<html><body></body></html>"

        async def evaluate(self, script):
            return [] if "background" in script.lower() else ""

        async def wait_for_timeout(self, _ms):
            return None

    async def check(_url):
        return None

    monkeypatch.setattr(crawl_module.netguard, "check_url_async", check)
    asyncio.run(reading._crawl_page(Page(), None, "https://t.test/p/old"))
    assert store.marked == [("https://t.test/p/old", crawl_module.REDIRECTED,
                             {"url": "https://t.test/p/new", "hops": 1, "status": 200, "kind": "clear"})]
    assert reading.stats.pages_redirected == 1 and reading.stats.pages_visited == 1


def _redirecting_page(monkeypatch, *, response_url="https://t.test/p/old/", page_url="https://t.test/p/new?token=secret#top", failed_images=False):
    """A page asked for as /p/old, answered by a 301 to `response_url` (the first document), where the reader ended on `page_url`."""
    from nyra import crawl as crawl_module

    store = _MarkingStore()
    store.mark_page = lambda page_id, status, redirect=None: store.marked.append((page_id, status, redirect))
    store.known_images = lambda urls: {}
    reading = _crawler(store=store)

    class Hop:
        async def response(self):
            return type("Hop", (), {"status": 301})()

    class Request:
        redirected_from = Hop()

    class Hop2:
        redirected_from = None

        async def response(self):
            return type("Hop", (), {"status": 301})()

    Request.redirected_from = Hop2()

    class Page:
        url = page_url

        async def goto(self, *_args, **_kwargs):
            return type("Response", (), {"status": 200, "url": response_url, "request": Request()})()

        async def content(self):
            return "<html><body></body></html>"

        async def evaluate(self, script):
            return [] if "background" in script.lower() else ""

        async def wait_for_timeout(self, _ms):
            return None

    async def check(_url):
        return None

    async def collected(*_args):
        if failed_images:
            reading.stats.downloads_failed += 1
        return True

    monkeypatch.setattr(crawl_module.netguard, "check_url_async", check)
    monkeypatch.setattr(reading, "_collect_images", collected)
    asyncio.run(reading._crawl_page(Page(), None, "https://t.test/p/old"))
    return store.marked[0][2]


def test_a_script_that_goes_on_after_the_http_redirects_is_not_a_clear_redirect(monkeypatch):
    """The status and the hops are those of the first document: when the reader ended elsewhere than that document, nothing is concluded."""
    assert _redirecting_page(monkeypatch)["kind"] == "script"
    assert _redirecting_page(monkeypatch, response_url="https://t.test/p/new?token=secret")["kind"] == "clear"  # the document that ended is the target


def test_the_target_is_kept_without_its_query_and_an_image_that_failed_to_download_concludes_nothing(monkeypatch):
    redirect = _redirecting_page(monkeypatch, response_url="https://t.test/p/new?token=secret")
    assert redirect["url"] == "https://t.test/p/new"  # no token, no return address, no fragment
    assert _redirecting_page(monkeypatch, response_url="https://t.test/p/new?token=secret", failed_images=True)["kind"] == "incomplete"
