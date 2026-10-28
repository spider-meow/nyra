-- Empreinte (SHA-256) du fichier envoyé pour un visuel de la bibliothèque (AXE-626) : l'import refuse un fichier identique à un
-- visuel déjà présent dans la marque. Les visuels ajoutés avant n'en ont pas (NULL) : ils ne sont pas reconnus comme doublons.
--
-- Additive et idempotente : sans risque à appliquer avant le déploiement du code qui l'utilise.

alter table public.reference_images add column if not exists content_hash text;
create index if not exists reference_images_brand_hash_idx on public.reference_images (brand_id, content_hash) where content_hash is not null;
