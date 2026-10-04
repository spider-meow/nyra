"""How long a read will take, from how long the last ones took. Pure functions over plain dicts."""

from __future__ import annotations

from statistics import median
from typing import Optional

# A run shorter than this is mostly fixed cost (sitemap, browser start): it says nothing about seconds per page.
MIN_PAGES = 5


def crawl_estimate(runs: list[dict], limit: int, fresh: bool) -> Optional[dict]:
    """`runs`: the site's last finished reads, newest first, each with `pages_visited` and `seconds`.

    Seconds per page comes from the runs long enough to measure it. The pages to expect are the most pages a
    past read reached (a full re-read) or what the latest read found (a resumed one only reads new pages),
    never more than `limit`. None when no run is usable.

    ponytail: a resumed read of very few pages is under-estimated (its fixed cost is not modelled); add a
    fixed-cost term from the shortest runs if that matters.
    """
    usable = [run for run in runs if run["pages_visited"] >= MIN_PAGES and run["seconds"]]
    if not usable:
        return None
    rate = sum(run["seconds"] for run in usable) / sum(run["pages_visited"] for run in usable)
    pages = min(limit, max(run["pages_visited"] for run in runs) if fresh else runs[0]["pages_visited"])
    if pages == 0:  # the latest resumed read found nothing new: "0 seconds" would be a claim, not an estimate
        return None
    return {"pages": pages, "seconds": round(pages * rate), "runs_used": len(usable)}


def match_estimate(durations: list[float]) -> Optional[int]:
    """The typical comparison time, or None without history."""
    return round(median(durations)) if durations else None


def remaining_seconds(done: int, expected: int, elapsed: float) -> Optional[int]:
    """Time left in a running read, at the pace seen so far (`elapsed` covers `done` pages). None until a few
    pages give a pace."""
    if done < MIN_PAGES or elapsed <= 0:
        return None
    return round(max(0, expected - done) * elapsed / done)
