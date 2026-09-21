"""Unit tests for the pure HTML/sitemap parsing helpers in rightswatch.crawl.

These don't touch a browser or the network: they operate on HTML strings,
exactly what Playwright would hand back from page.content().
"""

from __future__ import annotations

from rightswatch.crawl import (
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
    assert not any("facebook" in l for l in links)
    assert not any(l.startswith("mailto:") for l in links)
    assert not any(l.startswith("javascript:") for l in links)
