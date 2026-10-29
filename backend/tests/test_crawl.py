"""Unit tests for the pure HTML/sitemap parsing helpers in nyra.crawl.

These don't touch a browser or the network: they operate on HTML strings,
exactly what Playwright would hand back from page.content().
"""

from __future__ import annotations

import asyncio

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
        def mark_page(self, page_id, status): self.status = status
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
