# Matching algorithm

This is the core of Nyra: deciding whether a reference image is
"the same image" as something found on the crawled site. Everything here
lives in `backend/nyra/match.py`, and the classification functions
(`classify_level1`, `classify_level2`, `classify_pair`) are pure — no
database, no I/O — so they're the easiest place to reason about the
algorithm and the easiest to unit test (see `backend/tests/test_match.py`).

## Two levels, cheapest first

```
classify_pair(ref, site):
    result = classify_level1(ref.phash, ref.dhash, site.phash, site.dhash)
    if result is not None:
        return result                 # level 1 resolved it, stop here
    if use_clip:
        return classify_level2(ref.embedding, site.embedding)
    return None
```

**Level 1 — perceptual hashing.** `imagehash.phash` and `imagehash.dhash`
each reduce an image to a 64-bit fingerprint sensitive to overall structure
but tolerant of resizing and re-compression. Two hashes are compared by
Hamming distance (number of differing bits); `config.yaml`'s
`phash_threshold` / `dhash_threshold` (default `8`, i.e. at most 8 of 64
bits differ) decide whether that counts as a match. If either hash is
within threshold, level 1 returns a match — the better (lower-distance) of
the two if both qualify — with `confidence = "haut"`. This is what catches
the common cases: the exact same file, the same image re-encoded to a
different JPEG quality, or resized by a CDN.

Level 1 is deliberately blunt about anything that changes the image's
overall structure — a significant crop, a rotated variant, a large text
overlay. Those push the Hamming distance well past threshold, so level 1
correctly returns "no match" for them rather than guessing.

**Level 2 — CLIP embeddings.** Only runs on pairs level 1 rejected. Each
image gets a normalized embedding vector from a CLIP vision encoder
(`open_clip`, model configurable via `clip_model_name`/`clip_pretrained` in
`config.yaml`, default `ViT-B-32` / `laion2b_s34b_b79k`). Two embeddings are
compared by cosine similarity, a continuous 0-1 score. Three thresholds map
that score to a confidence band:

| Similarity | Confidence | Meaning |
|---|---|---|
| `>= clip_similarity_high` (default `0.92`) | `haut` | Same visual content with high confidence — crop, overlay, or heavy retouch of a known reference. |
| `>= clip_similarity_medium` (default `0.85`) | `moyen` | Likely the same, worth a quick glance in the report. |
| `>= clip_similarity_floor` (default `0.75`) | `a_verifier` | Plausible but uncertain — flagged for manual review, not treated as a confirmed match. |
| `< clip_similarity_floor` | *(no match)* | Not written to `matches` at all. |

## Why not just always run CLIP?

Two reasons. First, cost: CLIP inference needs a model load and (ideally) a
forward pass per image, versus a handful of bitwise operations for pHash/
dHash. Skipping it whenever a cheap hash already resolved the pair keeps
`match` fast on large sites. Second, precision: pHash/dHash hits are
"haut" confidence by construction — an 8-bit Hamming distance on a 64-bit
hash is a very strong signal — while CLIP similarity is a softer,
learned notion of "looks similar" that can occasionally rate a different
product shot from the same photoshoot as a close match. Reserving CLIP for
pairs the cheap check couldn't resolve keeps the two failure modes
separate instead of blending them into one score.

## Running match with/without CLIP

```bash
nyra match                # level 1 + level 2 (default)
nyra match --no-clip      # level 1 only, much faster, no torch/open_clip needed
```

`--no-clip` is also useful for the "commencer par... match niveau 1 seul"
bring-up path from the project brief, and for environments where installing
`torch`/`open_clip` isn't worth it yet (both `refs.ingest` and `crawl.crawl_site`
also take `--no-embeddings`/`compute_embeddings=False` for the same reason —
without embeddings in the database, level 2 has nothing to compare and
`classify_level2` just returns `None`).

## Calibrating thresholds

The three CLIP thresholds (and, less commonly, the two hash thresholds)
are guesses until validated against real data. `nyra calibrate` does
that validation:

1. Ingest refs and crawl the site as usual, so both `reference_images` and
   `site_images` have hashes/embeddings computed.
2. Hand-confirm a small set of pairs — genuine matches you can see with
   your own eyes, and a few genuine non-matches for contrast — as a CSV:

   ```csv
   ref_filename,site_url,label
   bottle-hero-2023.jpg,https://example.com/media/bottle-hero.jpg,match
   bottle-hero-2023.jpg,https://example.com/media/other-product.jpg,no_match
   ```

   See `ground_truth.example.csv` for a template.

3. Run the sweep:

   ```bash
   nyra calibrate --ground-truth ground_truth.csv --out-csv sweep.csv
   ```

   This computes, for every threshold in a reasonable range (Hamming
   distance 0-20 for pHash/dHash, cosine similarity 0.50-0.99 for CLIP),
   how many of your labeled pairs would be predicted as a match at that
   threshold, and reports precision/recall/F1 against your labels
   (`match.sweep_phash_thresholds`, `match.sweep_similarity_thresholds`).

4. Pick thresholds from the printed table (or `sweep.csv`) that hit the
   demo's success criterion — recall ≥ 90% on known matches, without
   flooding the report with false positives — and update `config.yaml`.

Calibration only touches thresholds; it never changes which pairs get
compared. It's meant to be re-run whenever the reference library or target
site changes meaningfully enough that the default thresholds stop feeling
right.

## Scores and confidence in the report

`report.html` shows both `level` (which check produced the match:
`phash`/`dhash`/`clip`) and `score` for every row, plus the confidence
band. Confirmed matches (`haut`/`moyen`) are the main table; `a_verifier`
matches get their own section at the bottom so a reviewer can triage the
uncertain ones separately without them cluttering the confirmed list.
