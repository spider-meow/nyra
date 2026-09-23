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
