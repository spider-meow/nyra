-- Packshots : les retours des admins plateforme (AXE-668), pour mesurer la détection (AXE-527) et reprendre le travail d'une session à l'autre.
--
-- Un admin plateforme dit d'une image « c'est un packshot » ou « ce n'est pas un packshot ». Le retour est gardé AVEC ce qui permet de rejouer le cas :
-- les deux mesures et les deux seuils du moment, et si l'image était détectée. Sans cela un retour ne dit pas pourquoi l'algorithme s'est trompé.
-- Un retour ne change jamais la détection : il sert à mesurer, changer un seuil reste une décision.
--
-- Une ligne par admin et par image (un nouveau retour remplace l'ancien). `content_hash` survit à une relecture du site ; `site_image_id` devient
-- nul si la ligne de l'image disparaît, le retour reste. Écriture par le backend seulement, aucune politique de lecture : le client ne voit rien.
--
-- Additive : sans effet tant que le code ne l'utilise pas, et le code tourne aussi sans elle (les boutons n'apparaissent pas, la route répond 409).

create table if not exists public.packshot_feedback (
    id uuid primary key default gen_random_uuid(),
    org_id uuid not null references public.organizations(id) on delete cascade,
    brand_id uuid not null references public.brands(id) on delete cascade,
    site_image_id uuid references public.site_images(id) on delete set null,
    content_hash text,
    is_packshot boolean not null,
    packshot_score real,
    bg_uniform real,
    product_threshold real not null,
    uniform_threshold real not null,
    detected boolean not null,
    note text check (note is null or char_length(note) <= 200),
    created_by uuid not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (created_by, site_image_id)
);

create index if not exists packshot_feedback_brand_idx on public.packshot_feedback(brand_id);
create index if not exists packshot_feedback_org_id_idx on public.packshot_feedback(org_id);

alter table public.packshot_feedback enable row level security;
