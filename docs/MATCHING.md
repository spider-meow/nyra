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

Each image is decoded and its keypoints extracted once while it stays in a small LRU cache (32 references, 64 site images, about 165 MB at most; an unreadable image is remembered, not retried), and the caches are freed at the end of the pass.

Kept candidates get level `geo`. A candidate whose images can't be read
stays at level `clip`, `a_verifier`.

Calibration (1,057 real images of a brand site, September 2026): 560
edited copies (crop, resize + JPEG 50, color, text overlay, mirror, set in
a banner, square crop) were kept 97.5 % of the time (84 % confirmed); 300
pairs of different images, 0 %. Of 400 pairs CLIP scored ≥ 0.75 that level
1 didn't match, 42 % were dropped: different shots of the same bottles,
cocktails, cellars and vineyards, checked by eye.

## Grouping the crops of a site photo

The same two steps also run between site images (`nyra/variants.py`,
`cloud/variants.py`), after each comparison, so "Droits non vérifiés" shows
a photo once however many crops or sizes the sites serve. For each image not
yet checked (`variants_checked_at` NULL): its 5 closest images by CLIP
(cosine ≥ `clip_similarity_floor`) are the candidates, the keypoint check
keeps only the confirmed tier (≥ 20 % of both images), and the pairs are
merged into groups (`site_images.variant_group`, at most 40 images per group,
a group keeps its id when another crop joins it). A mirrored copy is not
looked for. Thresholds changed in Settings do not regroup what is already
grouped. Ignoring a photo sets aside every one of its versions.

## Where is this picture used? (`locate`)

The "Où est-elle utilisée ?" panel of a library picture lists the pages of the
brand's sites that show it, crops included. A `locate` job (`verify.locate`,
`cloud/locate.py`) checks the reference against **every** image the crawls
already stored (no new read of a site, no CLIP pre-selection: a tight crop looks
nothing like its whole), so it costs roughly 0.1 to 0.5 s per image (an estimate, not measured on real sites: 10,000 images take between 15 minutes and 1.5 hours). It is one running job for the brand, so a started search delays the brand's other jobs until it ends; the queue starts any other kind first when both wait. The criterion
differs from a comparison's: `compare` judges by the *smaller* of the two
coverages, which drops a crop made of a sliver of the reference; here
(`verify.locate_tier`) the larger side counts too.

| Tier | Rule |
|---|---|
| same | ≥ `geometric_min_inliers` (20) and both coverages ≥ `geometric_confirm_coverage` (20 %) |
| review | either coverage ≥ 20 %, or ≥ 20 inliers and both coverages ≥ 5 %; from `geometric_locate_min_inliers` (10) inliers, so a very small crop is not lost |

The images grouped (`variant_group`) with a hit join it with its tier: a crop
too small to match on its own is still the same photo as a larger crop that did.
A mirrored copy is looked for. The result replaces the reference's previous one
(`reference_locations`, `reference_images.located_at`); the list merges it with
the comparison's matches and leaves out what a person set aside. **Ceiling:** a
crop under about 3 % of the reference's area (fewer than 10 keypoints agree)
is missed, as is one whose scale differs from the 1024 px working copy by more than 8× (the
reference keeps at most 2,000 keypoints, so a small crop of a very detailed picture has few to match); on generated pictures, unrelated images never reached the "review"
tier (0 of 300 pairs, at most 11 inliers) — **not measured on real images**: the low-inlier
"review" path (10 to 19 inliers) should be calibrated like the thresholds above before being
trusted. Images set aside by an exclusion are never searched, and a result is forgotten when the
reference is replaced (other hashes), when a site image's file changes, or when an image is excluded.
The result does not follow later crawls: the panel says when the last search ran and offers to relaunch it.

## Labels (types and contents)

Not part of the matching, which never reads them (`nyra/labels.py`). A person gives examples; an image's score for
a label is the mean cosine similarity of its 3 closest examples (a label with several looks is not averaged into a
blur). The four **types** of site image (logo, packshot, pictogramme, autre) take one per image, the best score,
once each has 3 examples. A **content** label (carafe, glasses…) is proposed above `CONTENT_THRESHOLD` (0.82, **a
guess, not calibrated on real images**). A person's answer always wins over a proposal, and "pas ça" keeps a
content label off an image. The hand-labeling mode reports how well the examples predict themselves (each one
classified by the others): an optimistic estimate, since examples are picked by hand. Zero-shot labels (a text
prompt instead of examples) are not done.

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
