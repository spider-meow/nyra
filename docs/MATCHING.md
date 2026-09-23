# Matching algorithm

Deciding whether a reference image is "the same image" as something found
on a site. Everything lives in `backend/nyra/match.py`; the classification
functions are pure, so they're the easiest place to reason about the
algorithm and to test it (`backend/tests/test_match.py`,
`test_units.py`).

## Two levels, cheapest first

**Level 1 — perceptual hashing.** `imagehash.phash` and `dhash` reduce an
image to 64-bit fingerprints tolerant of resizing and re-compression.
Two images match when the Hamming distance is within `phash_threshold` /
`dhash_threshold` (default 8 of 64 bits). References also store the
hashes of their horizontal mirror (`phash_flip`, `dhash_flip`), and the
distance used is the smaller of the two, so a flipped reuse is caught
here too. Level-1 hits are always *confirmed* (`haut`).

Level 1 is deliberately blunt about anything that changes the overall
structure — a significant crop, a large overlay. Those fall to level 2.

**Level 2 — CLIP embeddings.** Only for pairs level 1 rejected. Each image
gets a normalized embedding (`open_clip`, `ViT-B-32` /
`laion2b_s34b_b79k` by default, computed in batches of
`embedding_batch_size`). Cosine similarity maps to a confidence band:

| Similarity | Confidence | On screen |
|---|---|---|
| ≥ `clip_similarity_high` (0.92) | `haut` | Confirmé |
| ≥ `clip_similarity_medium` (0.85) | `moyen` | Probable |
| ≥ `clip_similarity_floor` (0.75) | `a_verifier` | À vérifier |
| below | no match | — |

The comparison runs as matrices: a Hamming matrix (refs × site images,
2048 site images per chunk) and a dot product for CLIP.

## Exclusions

Recurring false positives — a logo, a generic visual reused on every
page — can be excluded from the comparison screen. The site image's
pHash and dHash go into `excluded_hashes`, and every site image within the
usual Hamming threshold of them is left out of matching, copies included.
The exclusion list is part of the match signature: adding or removing an
exclusion makes the next pass a full one, which also brings back the
matches of an image that is re-included. Adding one also deletes the
matches it already produced right away.

## Incremental passes

`run_matching` remembers a signature of the thresholds, the model and the
CLIP switch. With an unchanged signature it only compares new references
against every site image and older references against new site images;
otherwise it recomputes everything. Results are written in one
transaction at the end. The same function runs on SQLite (CLI) and
Postgres (worker) through the `MatchStore` interface.

Organizations can tune the thresholds from Settings; saving a change
alters the signature, so the next comparison is a full one.

## Calibrating thresholds

Thresholds are guesses until checked against labeled pairs. Two ways to
get labels:

- **From the interface (hosted product).** Every decision is a label:
  *to remove* and *removed* are matches, *false positive* is a
  non-match.

  ```bash
  nyra cloud-calibrate --org remy-martin --out-csv sweep.csv
  ```

  These labels only cover pairs the matcher proposed, so they measure
  precision well and recall only relative to what was found. To measure
  misses, add known pairs by hand with the CSV method below.

- **From a hand-made CSV (CLI, SQLite).**

  ```csv
  ref_filename,site_url,label
  bottle-hero-2023.jpg,https://example.com/media/bottle-hero.jpg,match
  bottle-hero-2023.jpg,https://example.com/media/other-product.jpg,no_match
  ```

  ```bash
  nyra calibrate --ground-truth ground_truth.csv --out-csv sweep.csv
  ```

Both print precision, recall and F1 for Hamming thresholds 0–20 and
cosine thresholds 0.50–0.99. Pick values that keep recall high without
flooding "À vérifier", then set them in `config.yaml` or per organization
in Settings.

## Known limits, and what to try next

- **CLIP measures semantic similarity**, so two photos from the same shoot
  can score high. Models trained for copy detection (SSCD, DINOv2) should
  separate "same image, edited" from "same scene" better. Changing the
  model means re-embedding every image and recalibrating; do it once the
  interface has accumulated enough decisions to compare models on real
  data (`cloud-calibrate` gives the baseline).
- **Heavy crops** of a small region can fall below the CLIP floor. A
  local-feature check (ORB/SIFT with geometric verification) on the
  "À vérifier" band would confirm or reject those automatically.
- **Rotations** other than a horizontal flip are not handled at level 1.
