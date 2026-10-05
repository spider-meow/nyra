"""Images read on the brand's sites with no counterpart in its library: list them, add them to
the library, or set them aside for good (exclusions)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from nyra import fetch, netguard
from nyra import match as match_module
from nyra.config import Config
from nyra.refs import RefValidationError, parse_expiry

from .. import db as cloud_db
from .. import jobs as cloud_jobs
from .. import storage as cloud_storage
from .common import (
    BRAND,
    IMAGE_SUFFIXES,
    MAX_UPLOAD_BYTES,
    BrandScope,
    Ctx,
    brand_admin_dep,
    brand_member_dep,
    get_ctx,
    iso,
    log,
    put_reference_files,
    safe_filename,
    unreferenced_images,
    upload_failure,
)

router = APIRouter()


class AdoptBody(BaseModel):
    site_image_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    expiry_date: str = ""
    credit: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=2000)
    # Optional name per image (site image id -> file name); otherwise taken from the image's address.
    filenames: dict[str, str] = Field(default_factory=dict)


class ExclusionBody(BaseModel):
    site_image_id: uuid.UUID
    reason: str = Field(default="", max_length=500)


def filename_from_url(url: str, storage_path: str) -> str:
    """A library file name for an image read at `url`: its last path segment, with an image extension."""
    stem = Path(unquote(urlparse(url).path)).name.strip() or "image"
    suffix = Path(stem).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        stored = Path(storage_path).suffix.lower()
        stem = f"{stem}{stored if stored in IMAGE_SUFFIXES else '.jpg'}"
    stem = stem.replace("\\", "_").replace("\x00", "")
    if len(stem) > 200:
        suffix = Path(stem).suffix
        stem = stem[: 200 - len(suffix)] + suffix
    return stem


def unique_filename(name: str, taken: set[str]) -> str:
    """`photo.jpg`, then `photo (2).jpg`… so an addition never replaces a visual already in the library."""
    if name not in taken:
        return name
    base, suffix = Path(name).stem, Path(name).suffix
    index = 2
    while f"{base} ({index}){suffix}" in taken:
        index += 1
    return f"{base} ({index}){suffix}"


def original_from_site(url: str, config: Config) -> Optional[bytes]:
    """The image as the site serves it (Storage only keeps a working copy of crawled images)."""
    try:
        with netguard.client(headers={"User-Agent": config.crawl.user_agent}) as http:
            got = fetch.download(url, http, timeout=20.0, max_bytes=MAX_UPLOAD_BYTES)
    except Exception:  # noqa: BLE001 - the stored working copy is the fallback
        log.warning("fetching the original of %s failed; using the stored working copy", url.split("?")[0], exc_info=True)
        return None
    return got[0] if got else None


MAX_WHERE = 20


def _site_name(row: dict) -> str:
    return row["site_label"] or urlparse(row["site_url"]).netloc


def _variants(rows: list[dict]) -> list[dict]:
    """The distinct files of one photo, in the order of `rows` (largest first, see `unreferenced_images`): each
    one's rows, the first being the row to act on."""
    files: dict[Any, list[dict]] = {}
    for row in rows:
        files.setdefault(row["content_hash"] or row["id"], []).append(row)
    return list(files.values())


def _variant_item(members: list[dict], urls: dict[str, str]) -> dict:
    lead = members[0]
    # One line per site and page: two addresses of the same bytes read on one page are one occurrence.
    where = list(dict.fromkeys((_site_name(row), page) for row in members for page in row["pages"]))
    return {
        "id": str(lead["id"]),
        "url": lead["url"],
        "url_count": len(members),
        "width": lead["width"],
        "height": lead["height"],
        "thumb": urls.get(lead["thumb_path"] or "") or urls.get(lead["storage_path"] or "", ""),
        "image": urls.get(lead["storage_path"] or "", ""),
        "where": [{"site": site, "page": page} for site, page in where[:MAX_WHERE]],
        "where_count": len(where),
    }


def _site_image_item(group: dict, urls: dict[str, str]) -> dict:
    lead, rows = group["lead"], group["rows"]
    pages = sorted({page for row in rows for page in row["pages"]})
    sites = {row["site_id"]: _site_name(row) for row in rows}
    return {
        "id": str(lead["id"]),
        "ids": [str(row["id"]) for row in rows],
        "variants": [_variant_item(members, urls) for members in _variants(rows)],
        "url": lead["url"],
        "urls": [row["url"] for row in rows][:10],
        "url_count": len(rows),
        "filename": filename_from_url(lead["url"], lead["storage_path"] or ""),
        "pages": pages[:20],
        "page_count": len(pages),
        "site_ids": [str(site_id) for site_id in sites],
        "sites": list(sites.values()),
        "width": lead["width"],
        "height": lead["height"],
        "compared": all(row["compared"] for row in rows),
        "first_seen": iso(min(row["first_seen"] for row in rows)),
        "thumb": urls.get(lead["thumb_path"] or "") or urls.get(lead["storage_path"] or "", ""),
        "image": urls.get(lead["storage_path"] or "", ""),
    }


@router.get(f"{BRAND}/site-images")
def site_images(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """What was read on the brand's sites without any counterpart in its library: rights unknown."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        config = ctx.org_config(conn, scope.org_id)
        groups = unreferenced_images(conn, scope.brand_id, config)
    leads = [variant[0] for group in groups for variant in _variants(group["rows"])]
    urls = ctx.sign(cloud_storage.BUCKET_SITE_IMAGES, [row["thumb_path"] for row in leads] + [row["storage_path"] for row in leads])
    return {"items": [_site_image_item(group, urls) for group in groups]}


def _adopt_one(ctx: Ctx, scope: BrandScope, client, config: Config, body: AdoptBody, expiry: Optional[str],
               row: dict, taken: set[str]) -> tuple[Optional[dict], str]:
    """Add one site image to the library: (the entry for `added`, why it was refused).
    The first item is None when the image was refused."""
    wanted = (body.filenames.get(str(row["id"])) or "").strip()
    name = safe_filename(wanted) if wanted else safe_filename(filename_from_url(row["url"], row["storage_path"] or ""))
    filename = unique_filename(name, taken)
    data = original_from_site(row["url"], config) or cloud_storage.download(
        client, cloud_storage.BUCKET_SITE_IMAGES, row["storage_path"])
    img, refused = fetch.decode_reference(data, config.crawl.max_image_pixels)
    if img is None:
        return None, refused
    stored = put_reference_files(client, scope.org_id, scope.brand_id, filename, data, img)
    with cloud_db.connect(ctx.settings.database_url) as conn:
        ref_id = cloud_db.upsert_reference_image(
            conn, org_id=scope.org_id, brand_id=scope.brand_id, filename=filename,
            expiry_date=expiry, credit=body.credit.strip() or None, notes=body.notes.strip() or None, **stored,
        )
        same = cloud_db.same_image_ids(conn, scope.brand_id, row["content_hash"], row["id"])
        cloud_db.write_matches(conn, scope.org_id, [(ref_id, image_id, "phash", 1.0, "haut") for image_id in same])
    taken.add(filename)
    return {"site_image_id": str(row["id"]), "filename": filename}, ""


@router.post(f"{BRAND}/site-images/adopt")
def adopt_site_images(body: AdoptBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Add images read on the site to the library. Each one is linked to its occurrences at once
    (a sure match: same image), so it goes straight to "À traiter"."""
    try:
        expiry = parse_expiry(body.expiry_date)
    except RefValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client = ctx.settings.storage_client()
    with cloud_db.connect(ctx.settings.database_url) as conn:
        config = ctx.org_config(conn, scope.org_id)
        rows = cloud_db.site_images_by_id(conn, scope.brand_id, body.site_image_ids)
        taken = {row["filename"] for row in cloud_db.list_references(conn, scope.brand_id)}
    found = {row["id"] for row in rows}
    added: list[dict] = []
    failed: list[dict] = [
        {"site_image_id": str(image_id), "url": "", "reason": "Image introuvable pour cette marque."}
        for image_id in body.site_image_ids if image_id not in found
    ]
    for row in rows:
        try:
            item, refused = _adopt_one(ctx, scope, client, config, body, expiry, row, taken)
        except HTTPException as exc:
            failed.append({"site_image_id": str(row["id"]), "url": row["url"], "reason": str(exc.detail)})
            continue
        except Exception as exc:  # noqa: BLE001 - one image must not sink the others
            log.exception("adopting site image %s into brand %s failed", row["id"], scope.brand_id)
            failed.append({"site_image_id": str(row["id"]), "url": row["url"], "reason": upload_failure(exc)})
            continue
        if item is None:
            failed.append({"site_image_id": str(row["id"]), "url": row["url"], "reason": refused})
        else:
            added.append(item)
    job = None
    if added:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            job = cloud_jobs.enqueue(conn, org_id=scope.org_id, brand_id=scope.brand_id, kind="index",
                                     created_by=scope.member.user_id)
    return {"added": added, "failed": failed, "job": job}


@router.get(f"{BRAND}/exclusions")
def list_exclusions(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        rows = cloud_db.list_exclusions(conn, scope.brand_id)
    urls = ctx.sign(cloud_storage.BUCKET_SITE_IMAGES, [row["thumb_path"] for row in rows])
    return {
        "exclusions": [
            {"id": str(row["id"]), "reason": row["reason"] or "", "site_url": row["site_url"] or "",
             "thumb_url": urls.get(row["thumb_path"] or "", ""), "created_at": iso(row["created_at"])}
            for row in rows
        ]
    }


@router.post(f"{BRAND}/exclusions")
def add_exclusion(body: ExclusionBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Never match this image (or a near copy of it) again, for this brand.

    Matches it already produced disappear at once; the next comparison
    recomputes everything with the new exclusion list.
    """
    with cloud_db.connect(ctx.settings.database_url) as conn:
        group_id = cloud_db.add_exclusion(conn, scope.org_id, scope.brand_id, body.site_image_id,
                                          reason=body.reason.strip() or None, created_by=scope.member.user_id)
        if group_id is None:
            raise HTTPException(status_code=404, detail="Image introuvable.")
        config = ctx.org_config(conn, scope.org_id)
        excluded = match_module.excluded_site_ids(
            cloud_db.site_hashes(conn, scope.brand_id), cloud_db.load_exclusions(conn, scope.brand_id), config.match
        )
        removed = conn.execute(
            "DELETE FROM matches WHERE org_id = %s AND site_image_id = ANY(%s)", (scope.org_id, list(excluded))
        ).rowcount
        conn.execute("DELETE FROM reference_locations WHERE org_id = %s AND site_image_id = ANY(%s)", (scope.org_id, list(excluded)))
    return {"id": str(group_id), "matches_removed": removed}


@router.delete(f"{BRAND}/exclusions/{{exclusion_id}}")
def delete_exclusion(exclusion_id: uuid.UUID, scope: BrandScope = Depends(brand_admin_dep),
                     ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if not cloud_db.delete_exclusion(conn, scope.brand_id, exclusion_id):
            raise HTTPException(status_code=404, detail="Exclusion introuvable.")
    # The match signature now differs: the next comparison brings the image back.
    try:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            job = cloud_jobs.enqueue(conn, org_id=scope.org_id, brand_id=scope.brand_id, kind="match",
                                     created_by=scope.member.user_id)
    except cloud_jobs.JobConflict:
        job = None
    return {"ok": True, "job": job}
