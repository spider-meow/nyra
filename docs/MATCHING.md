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
here too. Level-1 hits are *confirmed* (`haut`), unless both embeddings exist and their cosine is
below `clip_similarity_floor`: two flat, white-background pictures can collide on one 8-bit hash by
chance (a `dhash` at distance 8 matched a portrait with a packshot, CLIP 0.19), and CLIP overrules that.

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

These figures come from copies **made by us** from one brand's images, not from reuses found in the wild: they say
the check survives the edits we thought of, not how often it is right on a client's real reuses. Precision and recall on
real, hand-labelled pairs are still to be measured (`nyra calibrate`, below).

## Grouping the crops of a site photo

The same two steps also run between site images (`nyra/variants.py`,
`cloud/variants.py`), after each comparison, so the "À qualifier" tab of "Images en ligne" and the list of "À traiter" show
a photo once however many crops or sizes the sites serve (in "À traiter" a photo is one row, "N fichiers", and a decision covers every file). For each image not
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

## Spotting logos (`nyra/logos.py`)

Is a whole site image a logo, so that it can be set aside instead of compared? Without any per-brand setup. Wired in
behind a per-brand setting, **off by default** (Réglages > Général > Logos, administrators): when on, the images above the threshold
are left out of the comparison, of the "À qualifier" tab of "Images en ligne" and of the search for a reference's copies, and the match
signature changes (a full pass follows). They are listed in the settings, where one can be put back (`logo_kept`, for
every address serving the same bytes; an address of the same file read later is still set aside, and the list then shows the
file as not put back). Scores are filled in on demand: when the setting is turned on, at each comparison while it is on, and
when the list is opened; a brand that does not use it never pays for it. A change made while a comparison runs queues another
behind it, since the running one has read the old set.

- **Colour statistics do not work.** Two dominant colours, a small palette and flat pixels flagged 256 of 1,716
  images of one brand's site; the best scores were white porcelain plates and cups on a white background (a real product
  photo reads as one flat colour), and the brand's actual wordmarks were missed. Dropped.
- **CLIP zero-shot works.** The stored image embedding is compared with the embeddings of short descriptions ("a logo",
  "a photograph of a product"...); the logo probability is the share of the softmax on the logo descriptions. The
  descriptions' embeddings are kept in `resources/logo_text.npz` (regenerate: `python -m nyra.logos`), so the web
  process scores with numpy alone.
- **Measured on 1,716 distinct images of one brand's site (October 2026):** 10 above 0.95, every one a logo (wordmarks,
  the fleur-de-lis badge, red and white variants, file names containing "logo"); then 0.92 an institutional red graphic,
  and 0.83 to 0.86 a photograph of a château with a small emblem. Hence `THRESHOLD = 0.95`: setting a real photograph
  aside is the costly mistake.
- **Not known:** the recall (logos below 0.95 were looked for by eye, not counted), and how it holds on another brand:
  only one brand had images read. Check another brand's images before relying on it.

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

## Every report states its settings

`report.html` (footer) and both CSV files (two last columns, `site_read_on` and `detection_settings`) say which
thresholds and which model the matches were produced with, and the day the sites were last read. The text is the
`signature` that `run_matching` stored after the last finished pass, put in words by `match.describe_signature` (so
there is one source: change what the signature holds and that function, a test fails if they drift apart). A brand
with no finished pass reads "inconnus". The columns, not comment lines, carry the CSV settings: Excel keeps them when
rows are sorted or filtered, and a CSV reader still finds its header on line 1. A CSV with no row carries none of it;
the HTML always does.

## Calibration in CI (`backend/tests/test_calibration.py`)

A guard rail, not a validation. A fixed reference set is **generated by code** (seeded textured pictures, each one
re-compressed, shrunk, mirrored, heavily compressed, colour-shifted, brightened and lightly cropped; every other pairing
is a pair of different pictures), so nothing is stored and no client image is involved. The test runs the real hash
comparison (`match.compare` with the thresholds of `config.yaml`) and, when OpenCV is installed, the real keypoint check
(`verify.compare`), and fails when precision or recall fall under floors written in the file.

| Level | Pairs | Precision | Recall | Floors in the test |
|---|---|---|---|---|
| Hash (30 pictures × 7 edits) | 6,300 | 1.000 (0 false of 6,090) | 0.990 (208 of 210) | 0.98 / 0.90 |
| Keypoints (6 pictures × 7 edits) | 252 | 1.000 (0 false of 210) | 1.000 (42 of 42) | 0.98 / 0.95 |

(Measured on 20 Oct 2026, Pillow 12.3, OpenCV 5.0.) What it catches, tried by temporarily editing `config.yaml`: hash
thresholds of 0, 2 or 3 bits (recall 0.54 / 0.77 / 0.82), of 20 bits (precision 0.63) and of 64 (0.03); zero or 400
required keypoints, coverage thresholds of 90 %. What it does **not**: hash thresholds from 4 to 16 bits pass (synthetic
negatives are far apart, so they cannot show where false positives start; real products on one template can), coverage
thresholds of 0 pass (the inlier count still guards), and nothing here touches CLIP (the cosine bands and the model need
torch and real pictures; the CI job has torch, but its single CLIP test only checks that an embedding is computed).

**Changing the reference on purpose.** Edit `TRANSFORMS`, `SEED` or the sizes in the test file, run
`python -m pytest -q backend/tests/test_calibration.py`, and re-measure: the failure messages print the figures; to see
them on a passing run, call `reference_set` and the test body from a Python shell. Then update the floors and the table
above in the same pull request, with the reason. Never lower a floor to get a red build green.

**CLIP on real images stays manual.** `nyra calibrate` / `nyra cloud-calibrate` (AXE-526) need real, hand-labelled pairs
and torch; run it by hand before changing a CLIP threshold or the model, and put the figures in this file. *Proposal, not
configured:* a nightly or weekly job on a machine that holds a labelled set outside the repository, running `nyra
calibrate` and comparing with the last figures. Nothing runs it today.

## Functions that rest on a threshold: the flag rule

**A function that rests on a threshold stays behind a flag, or a per-brand setting that is off by default, until
precision and recall have been measured on real images.** Current state (20 Oct 2026):

| Function | Threshold | Switch | Default | Measured on real images |
|---|---|---|---|---|
| Hash match (level 1) | 8 / 8 bits | `match.phash_threshold`, `dhash_threshold` (organization) | **on**, core | no labelled set; CLIP overrules a colliding hash |
| CLIP bands (level 2) | 0.92 / 0.85 / 0.75 | `match.clip_similarity_*` (organization) | **on**, core | 400 pairs checked by eye, one brand |
| Keypoint check of CLIP candidates | 20 inliers, 5 % / 20 % | `match.verify_clip_matches` (organization) | **on** | 1,057 images of one brand, copies made by us |
| Labels (types, contents) | 0.82 (`CONTENT_THRESHOLD`) | `NYRA_LABELS=1` (API) and `VITE_LABELS=1` (interface), build-time | **off**: the routes are not registered, the screens not built | no |
| Logos set aside | 0.95 (`logos.THRESHOLD`) | per brand, `brands.set_aside_logos` (Réglages > Général, administrators) | **off** | 1,716 images of one brand; precision only, recall not counted |
| Grouping of crops (`variants`) | CLIP ≥ 0.75 for 5 neighbours, then ≥ 20 inliers and ≥ 20 % of both images | none | **ON for every brand, after every comparison** | **no** (the thresholds were measured for brand-to-site matches, not for site-to-site grouping) |
| "Où est-elle utilisée ?" (`locate`) | 10 inliers for a small crop (`geometric_locate_min_inliers`) | a person starts the job | off until asked; no automatic run | no ("not measured on real images", above) |

**Grouping of crops breaks the rule today**: it runs on its own and has no switch. A wrong group shows two different
photos as one row in "À traiter", where one decision covers every file of the group. It is left running pending a decision
of the team: either measure it on real images, or put it behind a per-brand setting that starts off.

Adding a function with a threshold means adding a row here, with its switch and its default.

## The algorithm proposes, a person decides

No deletion, no withdrawal and no final conclusion is laid by a job or a pipeline step: a match, a crop group, a logo
score or a label is a proposal; "à retirer", "faux positif", "retiré", an exclusion, a logo put back and a label answer
are written only by a person's request (`cloud/routes/`). Two tests hold it:

- `test_cloud_worker.py::test_no_automatic_job_takes_a_decision_for_a_person` runs index, comparison (full, after a
  signature change), crop grouping, logo scoring, `locate` and `check` on a brand holding decisions, and checks that
  `reviews`, `excluded_hashes`, `brands.set_aside_logos` and `logo_kept` are unchanged and the new matches undecided.
  Not covered: the crawl (needs Chromium) and the labels, which have no job.
- `test_units.py::test_only_a_persons_request_writes_a_decision` scans `backend/nyra`: the SQL that writes those tables
  lives in four files, and only `cloud/routes/` may call their writers. It is a text scan, so a write built from
  concatenated strings escapes it.

A new automatic function must be exercised in the first test and, if it writes anywhere near these tables, listed in
the second.

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
