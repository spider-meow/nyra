-- Packshots (AXE-527): a product photographed alone on a plain background, found among the images read on a brand's sites and filed under a category of
-- "Images en ligne" (`nyra/packshots.py`). Nothing is hidden from the comparison: the library is compared with every image whatever this says.
--
--   site_images.packshot_score   CLIP's probability that the image is a photograph of a product (null: not scored yet)
--   site_images.bg_uniform       the share of the picture's border that is one flat colour (null: not measured yet)
--   brands.detect_packshots      the per-brand setting, off by default: while it is on, the Packshots tab shows what is detected and "À qualifier" leaves it out
--
-- Additive and nullable: safe to apply before deploying the code, and the code also runs without it (the detection is then off, nothing else changes).
-- Images read before the setting is turned on are measured by the next `index` job (it reads their thumbnail, nothing else).

alter table public.site_images
    add column if not exists packshot_score real,
    add column if not exists bg_uniform real;

alter table public.brands
    add column if not exists detect_packshots boolean not null default false;
