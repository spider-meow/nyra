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

**Level 3 — keypoint check of every CLIP candidate.** CLIP measures what an
image *shows*: two different shots of the same bottle, or two vineyards at
dusk, score 0.8–0.9. So no CLIP candidate is kept on its score alone
(`nyra/verify.py`, OpenCV, worker only):

1. SIFT keypoints on both images (grayscale, 1024 px on the long side);
2. ratio test and mutual nearest neighbors, so each keypoint is used once;
3. RANSAC fits one similarity transform (resize, crop, slight rotation),
   and the mirrored reference is tried too; a degenerate transform (scale
   outside 1/8–8) is rejected;
4. the agreeing keypoints (inliers) must be at least
   `geometric_min_inliers` (20) and span, as a convex hull, enough of
   **both** images:

| Coverage of both images | Result | What it usually is |
|---|---|---|
| < `geometric_review_coverage` (5 %) | dropped | another shot of the same product, a shared logo |
| 5–20 % | kept, `a_verifier` | the same product cut-out in another composition, a special edition shot on the same template |
| ≥ `geometric_confirm_coverage` (20 %) | kept, `haut` | the same photo: cropped, resized, recolored, mirrored, text over it, set in a banner |

Kept candidates get level `geo`. A candidate whose images can't be read
stays at level `clip`, `a_verifier`.

Calibration (1,057 real images of a brand site, September 2026): 560
edited copies (crop, resize + JPEG 50, color, text overlay, mirror, set in
a banner, square crop) were kept 97.5 % of the time (84 % confirmed); 300
pairs of different images, 0 %. Of 400 pairs CLIP scored ≥ 0.75 that level
1 didn't match, 42 % were dropped: different shots of the same bottles,
cocktails, cellars and vineyards, checked by eye.

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

- **A shared product cut-out** (the same bottle render on another
  background) lands in "À vérifier": geometrically it *is* the same
  pixels, and whether its rights are the picture's depends on the
  contract. Two editions shot on the same template (XO and a special XO)
  can land there too.
- **Heavy crops** of a small region can fall below the CLIP floor and are
  never proposed. A model trained for copy detection (SSCD, DINOv2) as the
  candidate finder would help; changing it means re-embedding every image.
- **Rotations** other than a horizontal flip are not handled at level 1.
