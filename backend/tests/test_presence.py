"""Where a visual is still online, page by page: the last read of each site, the pages read again, the links stamped."""

from datetime import datetime, timedelta, timezone

from nyra.cloud import db as cloud_db
from nyra.cloud import presence
from nyra.cloud.store import CloudCrawlStore

READ = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
HASH = "ffff0000ffff0000"


def _setup(conn, org, brand):
    site = cloud_db.create_site(conn, org_id=org, brand_id=brand, url="https://a.test/", label="US")
    ref = cloud_db.upsert_reference_image(conn, org_id=org, brand_id=brand, filename="v.jpg", storage_path=f"{org}/v.jpg",
                                          expiry_date=None, credit=None, notes=None, phash=HASH, dhash=HASH)
    conn.execute("INSERT INTO crawl_runs (org_id, site_id, status, started_at) VALUES (%s, %s, 'done', %s)", (org, site, READ))
    return site, ref


def _image(conn, org, site, ref, name):
    image = conn.execute("INSERT INTO site_images (org_id, site_id, url, storage_path, phash, dhash) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                         (org, site, f"https://a.test/{name}.jpg", f"{org}/{name}.jpg", HASH, HASH)).fetchone()["id"]
    cloud_db.write_matches(conn, org, [(ref, image, "phash", 1.0, "haut")])
    return image


def _page(conn, org, site, image, path, *, tracked=True, crawled=READ + timedelta(minutes=1), seen=None):
    page = conn.execute("INSERT INTO pages (org_id, site_id, url, status, crawled_at, links_tracked, read_started_at) VALUES (%s, %s, %s, 'done', %s, %s, %s) ON CONFLICT (site_id, url) DO UPDATE SET url = excluded.url RETURNING id",
                        (org, site, f"https://a.test{path}", crawled, tracked, crawled - timedelta(minutes=1))).fetchone()["id"]
    conn.execute("INSERT INTO image_pages (image_id, page_id, last_seen) VALUES (%s, %s, %s)", (image, page, seen))


def test_a_page_is_still_gone_or_unknown_from_the_last_read(cloud_database_url, cloud_org, cloud_brand):
    after, before = READ + timedelta(minutes=2), READ - timedelta(days=1)
    with cloud_db.connect(cloud_database_url) as conn:
        site, ref = _setup(conn, cloud_org, cloud_brand)
        image, other = _image(conn, cloud_org, site, ref, "a"), _image(conn, cloud_org, site, ref, "b")
        _page(conn, cloud_org, site, image, "/blogs/seen", seen=after)  # read again, image there
        _page(conn, cloud_org, site, image, "/blogs/removed", seen=before)  # read again, image no longer there
        _page(conn, cloud_org, site, image, "/blogs/other-file", seen=before)
        _page(conn, cloud_org, site, other, "/blogs/other-file", seen=after)  # another file of the photo is there: still
        _page(conn, cloud_org, site, image, "/collections/untracked", tracked=False, seen=before)  # read before page tracking
        _page(conn, cloud_org, site, image, "/collections/skipped", crawled=before, seen=before)  # not read again in this read
        _page(conn, cloud_org, site, image, "/", seen=before)
        false_positive = _image(conn, cloud_org, site, ref, "fp")
        _page(conn, cloud_org, site, false_positive, "/products/fp", seen=after)
        cloud_db.set_reviews(conn, brand_id=cloud_brand, reference_id=ref, site_image_ids=[false_positive], decision="ecarte", reviewed_by=None)
        rows = presence.counts(conn, cloud_brand, ref)
        listed = presence.pages(conn, cloud_brand, ref, state="still")
        sections = presence.pages(conn, cloud_brand, ref, section="/collections")

    result = presence.summary(rows)
    assert result["totals"] == {"still": 2, "gone": 2, "unknown": 2, "marked": 0, "pages": 6}  # the false positive's page is not counted
    assert [item["section"] for item in result["versions"][0]["sections"]] == ["/blogs", "/", "/collections"]  # most still online first
    assert sorted(row["url"] for row in listed) == ["https://a.test/blogs/other-file", "https://a.test/blogs/seen"]
    assert {row["state"] for row in sections} == {"unknown"} and len(sections) == 2
    assert presence.total(rows, section="/blogs", state="gone") == 1


def test_a_site_never_read_has_only_unknown_pages(cloud_database_url, cloud_org, cloud_brand):
    with cloud_db.connect(cloud_database_url) as conn:
        site, ref = _setup(conn, cloud_org, cloud_brand)
        conn.execute("DELETE FROM crawl_runs WHERE site_id = %s", (site,))
        _page(conn, cloud_org, site, _image(conn, cloud_org, site, ref, "a"), "/blogs/x", seen=READ)
        assert presence.summary(presence.counts(conn, cloud_brand, ref))["totals"] == {"still": 0, "gone": 0, "unknown": 1, "marked": 0, "pages": 1}


def test_a_read_stamps_the_links_and_only_a_successful_page_is_tracked(cloud_database_url, cloud_org, cloud_brand):
    with cloud_db.connect(cloud_database_url) as conn:
        site, ref = _setup(conn, cloud_org, cloud_brand)
        image = _image(conn, cloud_org, site, ref, "a")
    store = CloudCrawlStore(org_id=cloud_org, site_id=site, database_url=cloud_database_url, storage_client=None)
    page = store.upsert_page("https://a.test/blogs/x")
    store.link(image, page)
    store.link(image, page)  # seen again: the same link, stamped again
    store.mark_page(page, 200)
    with cloud_db.connect(cloud_database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM image_pages WHERE page_id = %s AND last_seen IS NOT NULL", (page,)).fetchone()["n"] == 1
        assert conn.execute("SELECT links_tracked FROM pages WHERE id = %s", (page,)).fetchone()["links_tracked"] is True
    for failure in (0, 404, 500):
        store.mark_page(page, failure)  # a page that failed to load says nothing about what it shows
        with cloud_db.connect(cloud_database_url) as conn:
            assert conn.execute("SELECT links_tracked FROM pages WHERE id = %s", (page,)).fetchone()["links_tracked"] is False


def _visual(conn, org, brand, site, name, *, decision, pages, unlimited=False):
    """A reference matching one image shown on `pages` (path, seen, read_again), decided `decision` (or not)."""
    ref = cloud_db.upsert_reference_image(conn, org_id=org, brand_id=brand, filename=name, storage_path=f"{org}/{name}",
                                          expiry_date=None if unlimited else "2026-12-01", credit=None, notes=None, phash=HASH, dhash=HASH, unlimited_rights=unlimited)
    image = _image(conn, org, site, ref, name)
    for path, seen, read_again in pages:
        _page(conn, org, site, image, f"/{name}{path}", tracked=read_again, seen=READ + timedelta(minutes=2) if seen else READ - timedelta(days=1))
    if decision:
        cloud_db.set_reviews(conn, brand_id=brand, reference_id=ref, site_image_ids=[image], decision=decision, reviewed_by=None)
    return ref


def test_visuals_to_replace_are_the_confirmed_ones_and_leave_once_no_page_shows_them(cloud_database_url, cloud_org, cloud_brand):
    with cloud_db.connect(cloud_database_url) as conn:
        site, _ = _setup(conn, cloud_org, cloud_brand)
        still = _visual(conn, cloud_org, cloud_brand, site, "still", decision="retenu", pages=[("/a", True, True), ("/b", False, True)])
        gone = _visual(conn, cloud_org, cloud_brand, site, "gone", decision="traite", pages=[("/a", False, True), ("/b", False, True)])
        unmeasured = _visual(conn, cloud_org, cloud_brand, site, "unmeasured", decision="retenu", pages=[("/a", False, False)])
        _visual(conn, cloud_org, cloud_brand, site, "undecided", decision=None, pages=[("/a", True, True)])
        _visual(conn, cloud_org, cloud_brand, site, "dismissed", decision="ecarte", pages=[("/a", True, True)])
        _visual(conn, cloud_org, cloud_brand, site, "free", decision="retenu", pages=[("/a", True, True)], unlimited=True)
        items = {item["row"]["reference_id"]: item for item in presence.visuals(presence.by_visual(conn, cloud_brand))}

    assert set(items) == {still, gone, unmeasured}  # only what a person confirmed, and never unlimited rights
    assert (items[still]["still"], items[still]["gone"], items[still]["replaced"]) == (1, 1, False)
    assert (items[gone]["still"], items[gone]["gone"], items[gone]["replaced"]) == (0, 2, True)  # no page shows it: replaced
    assert (items[unmeasured]["unknown"], items[unmeasured]["replaced"]) == (1, False)  # not read again: not replaced


def test_a_page_that_is_gone_counts_as_gone_and_a_visual_with_no_known_page_is_kept_but_not_replaced(cloud_database_url, cloud_org, cloud_brand):
    with cloud_db.connect(cloud_database_url) as conn:
        site, _ = _setup(conn, cloud_org, cloud_brand)
        removed = _visual(conn, cloud_org, cloud_brand, site, "removed", decision="traite", pages=[("/a", False, False)])
        # The page itself no longer exists: read again after the last start, answered 404 (a failed read is not "tracked").
        conn.execute("UPDATE pages SET http_status = 404, crawled_at = %s WHERE site_id = %s AND url LIKE %s", (READ + timedelta(minutes=1), site, "%/removed/a"))
        failed = _visual(conn, cloud_org, cloud_brand, site, "failed", decision="traite", pages=[("/a", False, False)])
        conn.execute("UPDATE pages SET http_status = 500, crawled_at = %s WHERE site_id = %s AND url LIKE %s", (READ + timedelta(minutes=1), site, "%/failed/a"))
        unlinked = _visual(conn, cloud_org, cloud_brand, site, "unlinked", decision="retenu", pages=[])
        items = {item["row"]["reference_id"]: item for item in presence.visuals(presence.by_visual(conn, cloud_brand))}

    assert (items[removed]["gone"], items[removed]["replaced"]) == (1, True)  # a page that answers 404 shows nothing any more
    assert (items[failed]["unknown"], items[failed]["replaced"]) == (1, False)  # a server error says nothing
    assert (items[unlinked]["pages"], items[unlinked]["replaced"]) == (0, False)  # confirmed, no page known: listed, not replaced


def test_the_files_of_one_photo_on_a_page_count_the_page_once(cloud_database_url, cloud_org, cloud_brand):
    with cloud_db.connect(cloud_database_url) as conn:
        site, ref = _setup(conn, cloud_org, cloud_brand)
        first, second = _image(conn, cloud_org, site, ref, "first"), _image(conn, cloud_org, site, ref, "second")
        _page(conn, cloud_org, site, first, "/blogs/x", seen=READ + timedelta(minutes=2))
        page = conn.execute("SELECT id FROM pages WHERE site_id = %s AND url = 'https://a.test/blogs/x'", (site,)).fetchone()["id"]
        conn.execute("INSERT INTO image_pages (image_id, page_id, last_seen) VALUES (%s, %s, %s)", (second, page, READ + timedelta(minutes=2)))
        assert presence.summary(presence.counts(conn, cloud_brand, ref))["totals"] == {"still": 1, "gone": 0, "unknown": 0, "marked": 0, "pages": 1}


def test_a_page_checked_on_its_own_says_what_it_shows_whatever_the_last_full_read_saw(cloud_database_url, cloud_org, cloud_brand):
    """The last full read saw the image on the page; a person changed the visual and checked that page alone: it is gone."""
    after_full_read = READ + timedelta(minutes=2)
    with cloud_db.connect(cloud_database_url) as conn:
        site, ref = _setup(conn, cloud_org, cloud_brand)
        image = _image(conn, cloud_org, site, ref, "a")
        _page(conn, cloud_org, site, image, "/blogs/changed", seen=after_full_read)  # seen in the full read
        _page(conn, cloud_org, site, image, "/blogs/unchanged", seen=after_full_read)
        assert presence.summary(presence.counts(conn, cloud_brand, ref))["totals"]["still"] == 2
        # The check of /blogs/changed alone, an hour later: the page read again, the image no longer on it (its link not stamped).
        later = READ + timedelta(hours=1)
        conn.execute("UPDATE pages SET read_started_at = %s, crawled_at = %s, links_tracked = true WHERE url LIKE %s",
                     (later - timedelta(minutes=1), later, "%/blogs/changed"))
        totals = presence.summary(presence.counts(conn, cloud_brand, ref))["totals"]
    assert (totals["still"], totals["gone"]) == (1, 1)


def test_a_page_whose_read_began_and_did_not_end_is_unknown(cloud_database_url, cloud_org, cloud_brand):
    """A page read before (gone, last time) is opened again and the read is cut short: its links were not stamped, so it must not read as gone."""
    with cloud_db.connect(cloud_database_url) as conn:
        site, ref = _setup(conn, cloud_org, cloud_brand)
        image = _image(conn, cloud_org, site, ref, "a")
        _page(conn, cloud_org, site, image, "/blogs/x", seen=READ - timedelta(days=1))
        assert presence.summary(presence.counts(conn, cloud_brand, ref))["totals"]["gone"] == 1
        conn.execute("UPDATE pages SET status = 'pending', read_started_at = %s WHERE site_id = %s", (READ + timedelta(hours=1), site))
        assert presence.summary(presence.counts(conn, cloud_brand, ref))["totals"] == {"still": 0, "gone": 0, "unknown": 1, "marked": 0, "pages": 1}


def test_a_page_marked_replaced_says_where_the_person_is_and_only_for_its_visual(cloud_database_url, cloud_org, cloud_brand):
    after = READ + timedelta(minutes=2)
    with cloud_db.connect(cloud_database_url) as conn:
        site, ref = _setup(conn, cloud_org, cloud_brand)
        image = _image(conn, cloud_org, site, ref, "a")
        other = _visual(conn, cloud_org, cloud_brand, site, "other", decision="retenu", pages=[])
        for path in ("/blogs/a", "/blogs/b", "/blogs/c"):
            _page(conn, cloud_org, site, image, path, seen=after)
        found = presence.pages_showing(conn, cloud_brand, ref, ["https://a.test/blogs/a", "https://a.test/blogs/b", "https://a.test/nowhere"])
        assert sorted(found) == ["https://a.test/blogs/a", "https://a.test/blogs/b"]  # a page that does not show the visual is not one of its pages
        presence.set_replaced(conn, cloud_org, ref, [found["https://a.test/blogs/a"]], replaced=True, user_id=None)
        presence.set_replaced(conn, cloud_org, ref, [found["https://a.test/blogs/a"]], replaced=True, user_id=None)  # twice: still once
        rows = presence.counts(conn, cloud_brand, ref)
        marked_only = presence.pages(conn, cloud_brand, ref, marked=True)
        unmarked = presence.pages(conn, cloud_brand, ref, marked=False)
        elsewhere = presence.counts(conn, cloud_brand, other)  # the mark belongs to one visual
        assert presence.summary(rows)["totals"]["marked"] == 1
        assert [row["url"] for row in marked_only] == ["https://a.test/blogs/a"] and len(unmarked) == 2
        assert presence.total(rows, marked=False) == 2 and presence.total(rows, marked=True) == 1
        assert presence.summary(elsewhere)["totals"]["marked"] == 0
        assert [row["checked"] for row in marked_only] == [False]  # marked now, last read long before: the state is not yet the answer
        conn.execute("UPDATE pages SET read_started_at = now() + interval '1 minute' WHERE site_id = %s AND url LIKE %s", (site, "%/blogs/a"))
        assert [row["checked"] for row in presence.pages(conn, cloud_brand, ref, marked=True)] == [True]  # read again after the mark
        assert {row["checked"] for row in presence.pages(conn, cloud_brand, ref, marked=False)} == {False}  # never marked: nothing to answer
        presence.set_replaced(conn, cloud_org, ref, [found["https://a.test/blogs/a"]], replaced=False, user_id=None)
        assert presence.summary(presence.counts(conn, cloud_brand, ref))["totals"]["marked"] == 0


def test_the_list_of_visuals_counts_the_pages_marked_replaced(cloud_database_url, cloud_org, cloud_brand):
    with cloud_db.connect(cloud_database_url) as conn:
        site, _ = _setup(conn, cloud_org, cloud_brand)
        ref = _visual(conn, cloud_org, cloud_brand, site, "marked", decision="retenu", pages=[("/a", True, True), ("/b", True, True)])
        found = presence.pages_showing(conn, cloud_brand, ref, ["https://a.test/marked/a"])
        presence.set_replaced(conn, cloud_org, ref, list(found.values()), replaced=True, user_id=None)
        items = {item["row"]["reference_id"]: item for item in presence.visuals(presence.by_visual(conn, cloud_brand))}
    assert (items[ref]["marked"], items[ref]["pages"], items[ref]["replaced"]) == (1, 2, False)  # marked is not verified: still listed


def test_a_visual_with_a_match_no_page_is_known_for_is_not_replaced_and_the_home_screen_agrees(cloud_database_url, cloud_org, cloud_brand):
    from nyra import report

    with cloud_db.connect(cloud_database_url) as conn:
        site, _ = _setup(conn, cloud_org, cloud_brand)
        shown = _visual(conn, cloud_org, cloud_brand, site, "shown", decision="retenu", pages=[("/a", False, True)])  # read again, not there: gone
        lone = _visual(conn, cloud_org, cloud_brand, site, "lone", decision="retenu", pages=[("/a", False, True)])
        other = _image(conn, cloud_org, site, lone, "lone-other")  # a second match of "lone", on no known page
        gone = _visual(conn, cloud_org, cloud_brand, site, "wall", decision="retenu", pages=[("/a", False, True)])
        conn.execute("UPDATE pages SET http_status = -1, links_tracked = false WHERE url LIKE '%/wall/a'")  # a consent wall hid it
        items = {item["row"]["reference_id"]: item for item in presence.visuals(presence.by_visual(conn, cloud_brand))}
        replaced = presence.replaced_references(conn, cloud_brand)
    assert other and items[shown]["replaced"] and not items[lone]["replaced"] and not items[gone]["replaced"]
    assert items[gone]["gone"] == 0 and items[gone]["unknown"] == 1  # a page hidden by a consent wall is never gone
    assert replaced == {shown}
    rows = [{"reference_id": ref, "filename": str(ref), "expiry_date": "2020-01-01", "confidence": "haut"} for ref in (shown, lone, gone)]
    assert report.dashboard(rows, [], replaced=replaced)["expired_online"] == 2  # the replaced one is no longer online
