"""What every route module shares: the request context, the auth and brand
dependencies, and the small helpers more than one domain needs.

`Ctx` holds the settings and the auth checks built from them. `create_app`
stores one on `app.state`; routes receive it with `Depends(get_ctx)`, so the
route functions can live at module level instead of inside `create_app`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import psycopg
from fastapi import Depends, HTTPException, Request

from nyra import fetch
from nyra import match as match_module
from nyra.config import Config, with_overrides
from nyra.refs import reference_features

from .. import auth as cloud_auth
from .. import db as cloud_db
from .. import insights as cloud_insights
from .. import jobs as cloud_jobs
from .. import storage as cloud_storage

if TYPE_CHECKING:
    from ..api import CloudSettings

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"}
MAX_UPLOAD_BYTES = 30_000_000

BRAND = "/api/orgs/{org_id}/brands/{brand_id}"

log = logging.getLogger("nyra.api")


class Ctx:
    """The settings of this app, the auth checks built from them, and the helpers that need them."""

    def __init__(self, settings: CloudSettings):
        self.settings = settings
        auth_args = dict(supabase_url=settings.supabase_url, jwt_secret=settings.jwt_secret, database_url=settings.database_url)
        self.user = cloud_auth.require_user(supabase_url=settings.supabase_url, jwt_secret=settings.jwt_secret)
        self.member = cloud_auth.require_member(**auth_args)
        self.admin = cloud_auth.require_admin(**auth_args)

    def org_config(self, conn, org_id: uuid.UUID) -> Config:
        return with_overrides(self.settings.base_config(), cloud_db.get_overrides(conn, org_id))

    def sign(self, bucket: str, paths) -> dict[str, str]:
        try:
            return cloud_storage.signed_urls(self.settings.storage_client(), bucket, paths)
        except Exception:  # noqa: BLE001 - missing thumbnails must not break a page
            log.warning("signing URLs in bucket %s failed; the page is served without them", bucket, exc_info=True)
            return {}

    def remove_files(self, bucket: str, paths: list[str]) -> None:
        try:
            cloud_storage.delete(self.settings.storage_client(), bucket, paths)
        except Exception:  # noqa: BLE001 - rows are gone; an orphan file is harmless
            log.warning("removing %d file(s) from bucket %s failed; orphans left behind", len(paths), bucket, exc_info=True)


@dataclass(frozen=True)
class BrandScope:
    """The caller's membership, and a brand checked to belong to their organization."""
    member: cloud_auth.Member
    org_id: uuid.UUID
    brand_id: uuid.UUID
    name: str


async def get_ctx(request: Request) -> Ctx:
    return request.app.state.ctx


def user_dep(token: str = Depends(cloud_auth._bearer_token), ctx: Ctx = Depends(get_ctx)) -> cloud_auth.Claims:
    return ctx.user(token)


def member_dep(org_id: uuid.UUID, token: str = Depends(cloud_auth._bearer_token),
               ctx: Ctx = Depends(get_ctx)) -> cloud_auth.Member:
    return ctx.member(org_id, token)


def admin_dep(member: cloud_auth.Member = Depends(member_dep), ctx: Ctx = Depends(get_ctx)) -> cloud_auth.Member:
    return ctx.admin(member)


def staff_dep(claims: cloud_auth.Claims = Depends(user_dep), ctx: Ctx = Depends(get_ctx)) -> cloud_auth.Claims:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if not cloud_insights.is_staff(conn, claims.user_id):
            raise HTTPException(status_code=403, detail="Réservé à l'équipe Nyra.")
    return claims


def _scope_for(ctx: Ctx, member: cloud_auth.Member, brand_id: uuid.UUID) -> BrandScope:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        brand = cloud_db.get_brand(conn, member.org_id, brand_id)
    if brand is None:
        raise HTTPException(status_code=404, detail="Marque introuvable.")
    return BrandScope(member=member, org_id=member.org_id, brand_id=brand_id, name=brand["name"])


def brand_member_dep(brand_id: uuid.UUID, member: cloud_auth.Member = Depends(member_dep),
                     ctx: Ctx = Depends(get_ctx)) -> BrandScope:
    return _scope_for(ctx, member, brand_id)


def brand_admin_dep(brand_id: uuid.UUID, member: cloud_auth.Member = Depends(admin_dep),
                    ctx: Ctx = Depends(get_ctx)) -> BrandScope:
    return _scope_for(ctx, member, brand_id)


def iso(value) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def brand_json(row: dict) -> dict:
    return {"id": str(row["id"]), "name": row["name"], "slug": row["slug"]}


def site_json(row: dict) -> dict:
    return {"id": str(row["id"]), "url": row["url"], "label": row["label"] or "", "images": row["images"],
            "last_crawled_at": iso(row["last_crawled_at"])}


def safe_filename(name: str) -> str:
    base = Path(name).name.strip()
    if not base or base in {".", ".."} or "/" in base or "\\" in base or "\x00" in base or len(base) > 200:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    if Path(base).suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Format non pris en charge : {base}")
    return base


def upload_failure(exc: Exception) -> str:
    """Why a file couldn't be added, in words an admin can act on (the full trace goes to the log)."""
    detail = str(exc).strip()[:240] or type(exc).__name__
    if cloud_storage.is_transient(exc):
        return f"Coupure réseau avec le stockage, même après plusieurs essais. Cliquez sur « Réessayer ». ({detail})"
    if isinstance(exc, psycopg.Error):
        return f"Enregistrement en base impossible : {detail}"
    if type(exc).__module__.startswith(("storage3", "supabase", "httpx", "httpcore")):
        return f"Le stockage a refusé le fichier : {detail}"
    return f"Erreur inattendue ({type(exc).__name__}) : {detail}"


def put_reference_files(client, org_id: uuid.UUID, brand_id: uuid.UUID, filename: str, data: bytes, img,
                        content_type: Optional[str] = None) -> dict:
    """Store a reference's original, thumbnail and working copy; the row fields they give
    (paths, hashes, size), ready for `upsert_reference_image`."""
    features = reference_features(img)
    width, height = img.info.get("original_size", (features.width, features.height))
    storage_path = cloud_storage.ref_path(org_id, brand_id, filename)
    thumb_path = cloud_storage.ref_thumb_path(org_id, brand_id, filename)
    work_path = cloud_storage.ref_work_path(org_id, brand_id, filename)
    cloud_storage.upload(client, cloud_storage.BUCKET_REFS, storage_path, data, content_type=content_type)
    cloud_storage.upload(client, cloud_storage.BUCKET_REFS, thumb_path, fetch.make_thumbnail(img),
                         content_type="image/jpeg")
    cloud_storage.upload(client, cloud_storage.BUCKET_REFS, work_path, fetch.make_working_copy(img),
                         content_type="image/jpeg")
    return dict(
        storage_path=storage_path, phash=features.phash, dhash=features.dhash,
        phash_flip=features.phash_flip, dhash_flip=features.dhash_flip,
        embedding=None, width=width, height=height, thumb_path=thumb_path, work_path=work_path, byte_size=len(data),
    )


def active_jobs(conn, brand_id: uuid.UUID, kinds: tuple[str, ...]) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM jobs WHERE brand_id = %s AND kind = ANY(%s) AND status = ANY(%s) LIMIT 1",
        (brand_id, list(kinds), list(cloud_jobs.ACTIVE)),
    ).fetchone())


def enqueue(ctx: Ctx, scope: BrandScope, kind: str, params: dict) -> dict:
    try:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            return {"job": cloud_jobs.enqueue(conn, org_id=scope.org_id, brand_id=scope.brand_id, kind=kind,
                                              params=params, created_by=scope.member.user_id)}
    except cloud_jobs.JobConflict as exc:
        label = {"crawl": "Une lecture", "match": "Une comparaison", "report": "Un rapport"}.get(kind, "Une tâche")
        raise HTTPException(status_code=409, detail=f"{label} est déjà en cours ou en attente.") from exc


def unreferenced_images(conn, brand_id: uuid.UUID, config: Config) -> list[dict]:
    """Images of the brand's sites that match nothing in its library and weren't set aside,
    one entry per photo: the same bytes under several URLs, and the crops and resizes of one photo
    (`variant_group`), count once. The lead of an entry is its largest image."""
    rows = cloud_db.unmatched_site_images(conn, brand_id)
    excluded = match_module.excluded_site_ids(rows, cloud_db.load_exclusions(conn, brand_id), config.match)
    shown = [row for row in rows if row["id"] not in excluded]
    # A twin of a grouped file (same bytes, new address) joins the group before the worker has seen it.
    group_of = {row["content_hash"]: row["variant_group"] for row in shown if row["variant_group"] and row["content_hash"]}
    groups: dict[Any, list[dict]] = {}
    for row in shown:
        key = row["variant_group"] or group_of.get(row["content_hash"]) or row["content_hash"] or row["id"]
        groups.setdefault(key, []).append(row)
    for members in groups.values():
        members.sort(key=lambda row: -image_pixels(row))  # stable: the lead is the first row of the largest file
    return [{"lead": members[0], "rows": members} for members in groups.values()]


def image_pixels(row: dict) -> int:
    return (row["width"] or 0) * (row["height"] or 0)

