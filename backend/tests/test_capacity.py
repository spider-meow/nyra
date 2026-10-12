import pytest

from nyra import capacity
from nyra.capacity import Brand


def test_egress_scales_with_brands_and_sources_add_up():
    one, three = capacity.estimate(Brand(), 1), capacity.estimate(Brand(), 3, cache_mb=10**9)
    assert one["egress"] == pytest.approx(sum(one["egress by source"].values()))
    assert three["users"] == 3 * Brand().users
    assert three["storage"] == pytest.approx(3 * capacity.estimate(Brand(), 1, cache_mb=10**9)["storage"])


def test_a_worker_without_disk_reads_everything_again():
    cached, bare = capacity.estimate(Brand(), 3), capacity.estimate(Brand(), 3, cache_mb=0)
    assert bare["cache miss rate"] == 1 and cached["cache miss rate"] < 1
    assert bare["egress"] > cached["egress"]


def test_measured_figures_replace_defaults_and_unknown_keys_fail():
    brand = capacity.measured({"references": 40.0, "stored_per_file": 114_000.0, "site_images": None})
    assert brand.references == 40 and brand.work_bytes == 100_000 and brand.site_images == Brand().site_images
    assert capacity.override(brand, ["users=9"]).users == 9
    with pytest.raises(ValueError):
        capacity.override(brand, ["nope=1"])
