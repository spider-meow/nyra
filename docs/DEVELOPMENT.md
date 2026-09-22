# Development guide

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium   # only needed to actually run `crawl`
```

`requirements.txt` is equivalent to `pip install -e .` plus `pytest`, for
environments that prefer a plain requirements file over an editable
install.

Heavy, optional-at-dev-time dependencies:

- **Playwright's Chromium binary** — only needed to run `nyra crawl`
  for real. All the HTML-parsing logic it depends on
  (`crawl.extract_images_from_html`, `extract_internal_links`, `parse_srcset`)
  is unit-tested without a browser.
- **`torch` + `open_clip`** — only needed for CLIP embeddings (level 2
  matching). Every module that touches them does so with a function-local
  import (`match._load_clip`, `match.compute_clip_embedding`), so importing
  `nyra.cli`, `nyra.crawl`, `nyra.match`, etc. never
  requires them. Level-1-only workflows (`--no-clip`, `--no-embeddings`)
  don't need them installed at all.

## Running tests

```bash
pytest
```

The full suite runs in well under a second with **no network access, no
Playwright browser, and no CLIP model download** required:

- `backend/tests/test_match.py` — the matching algorithm. Generates its own test
  images with Pillow (original, recompressed, resized, heavily cropped,
  text-overlaid, and a genuinely different image) rather than using binary
  fixtures, so there's nothing to keep in sync or regenerate. One test
  (`test_clip_embedding_end_to_end`) is skipped automatically via
  `pytest.importorskip` if `torch`/`open_clip` aren't installed.
- `backend/tests/test_crawl.py` — HTML/sitemap parsing helpers, exercised against
  inline HTML strings that mimic what `page.content()` would return.
- `backend/tests/test_integration.py` — the full ingest → match → report pipeline
  wired together end to end, with a synthetic "crawl result" inserted
  directly into the database (bypassing Playwright and the network) to
  simulate what `fetch.fetch_and_store` would have written.

When you add a feature, prefer extending one of these patterns over adding
a fixture file or a live network call: keep the test hermetic.

## Project conventions

These come from the original project brief and are worth keeping as you
extend the codebase:

- **One module, one responsibility.** `crawl.py` discovers pages and
  extracts image URLs; it does not decide whether those images match
  anything. `match.py` classifies pairs; it does not know how images got
  into the database. If you find yourself importing `fetch.py` from
  `report.py`, that's a sign the boundary has blurred.
- **Pure functions where they can be pure.** Anything that takes hashes,
  vectors, or HTML strings in and returns a verdict out (see
  `classify_level1`, `classify_level2`, `extract_images_from_html`,
  `parse_srcset`) should stay free of database/network/filesystem access,
  so it stays trivially testable. Push I/O to the thin integration layer
  around it (`run_matching`, `crawl_site`, `fetch_and_store`).
- **Thresholds and behavior live in `config.yaml`, not in code.** If
  you're tempted to hard-code a number that affects crawl behavior or
  match sensitivity, add it to `config.py`'s dataclasses and `config.yaml`
  instead — that's what makes `nyra calibrate` (see `MATCHING.md`)
  meaningful, and what lets someone tune the tool for a new site without a
  code change.
- **Every DB write is an upsert on a natural key.** `filename` for
  references, `url` for pages and site images. This is what makes
  `ingest-refs`/`crawl` safe to re-run and crawls resumable — don't
  introduce a writer that only inserts.
- **No secrets, no client data in the repo.** `refs/` (the actual reference
  images) and `*.db` are gitignored on purpose — see `.gitignore`. Only
  `refs.csv.example` and `ground_truth.example.csv` (templates with no real
  data) are tracked.
- **Logs over exceptions-as-control-flow.** `crawl_site` catches per-page
  exceptions and records them in `CrawlStats.errors` rather than aborting
  the whole crawl — a single bad page shouldn't lose 299 pages of progress.
  Follow that pattern for new per-item operations in a loop (per-image
  fetch, per-pair match, etc.).

## Extending `RefSource` (e.g. for a Brandcenter adapter)

`refs.py`'s `RefSource` is the seam for phase 2's Brandcenter export. To
plug in a new source:

```python
from nyra.refs import RefSource, RefEntry

class BrandcenterRefSource(RefSource):
    def __init__(self, export_path):
        self.export_path = export_path

    def iter_refs(self):
        for item in load_brandcenter_export(self.export_path):
            yield RefEntry(
                filename=item.asset_id,
                image_path=download_or_locate(item),
                expiry_date=item.rights_expiry.isoformat() if item.rights_expiry else None,
                credit=item.photographer,
                notes=item.usage_notes,
            )
```

Then `refs.ingest(BrandcenterRefSource(...), db_path, config)` works exactly
like `refs.ingest(CsvRefSource(...), db_path, config)` — nothing downstream
(`match.py`, `report.py`, `cli.py`) needs to change. If the CLI should
expose it, add a new Typer command (or a `--source` flag on `ingest-refs`)
in `cli.py` that constructs the right `RefSource` and calls `refs.ingest`.

## Extending image extraction in `crawl.py`

New image-loading patterns (a new lazy-load attribute, a JS framework that
renders images a different way) go into `extract_images_from_html` for
anything present in the rendered DOM, or into the `BACKGROUND_IMAGE_JS`
snippet for anything that needs a live `getComputedStyle` pass. Add a test
in `backend/tests/test_crawl.py` with a minimal HTML snippet first — since the
function is pure, you don't need a browser to verify the parsing logic
before wiring it into the live crawl.

## Adding a new match signal

To add a third matching level (or replace CLIP), implement a pure
`classify_levelN(ref_feature, site_feature, config) -> Optional[MatchResult]`
following `classify_level1`/`classify_level2`'s shape, wire it into
`classify_pair`'s fallback chain, and extend `run_matching` to fetch/pass
the new feature from the database. Keep the "cheapest and most certain
first" ordering — a new level should only run on pairs every cheaper level
already rejected.

## Code style

No linter/formatter is currently pinned in `pyproject.toml`; match the
existing style (standard library `black`-ish formatting, type hints on
public functions, module-level docstrings explaining *why* a module is
structured the way it is rather than restating *what* each function does).
Avoid comments that explain obvious code — the codebase leans on clear
naming and short functions instead.
