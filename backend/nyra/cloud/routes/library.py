"""The brand's library of reference images: list, upload, edit, delete, CSV import and export."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from nyra import fetch
from nyra import report as report_module
from nyra.refs import RefValidationError, change_tags, normalize_tags, parse_expiry

from .. import db as cloud_db
from .. import jobs as cloud_jobs
from .. import storage as cloud_storage
from .common import (
    BRAND,
    MAX_UPLOAD_BYTES,
    BrandScope,
    Ctx,
    active_jobs,
    brand_admin_dep,
    brand_member_dep,
    get_ctx,
    iso,
    log,
    put_reference_files,
    safe_filename,
    upload_failure,
)

MAX_FILES_PER_UPLOAD = 100
MAX_THUMBS_PER_REQUEST = 200  # the interface shows 100 cards at a time
MAX_CSV_BYTES = 2_000_000

router = APIRouter()


class MetaBody(BaseModel):
    expiry_date: str = ""
    credit: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=2000)
    # None leaves the tags as they are; a list replaces them.
    tags: Optional[list[str]] = Field(default=None, max_length=50)


class FilenamesBody(BaseModel):
    filenames: list[str] = Field(default_factory=list, max_length=5000)


class ThumbsBody(BaseModel):
    # No name validation: they are matched exactly against this brand's references, so an unknown name just has no entry.
    filenames: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=MAX_THUMBS_PER_REQUEST)


class BulkExpiryBody(FilenamesBody):
    expiry_date: str = ""


class BulkTagsBody(FilenamesBody):
    add: list[str] = Field(default_factory=list, max_length=50)
    remove: list[str] = Field(default_factory=list, max_length=50)


@router.get(f"{BRAND}/library")
def library(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    # ponytail: the whole library's metadata (~10,000 references, ~7 MB) goes out in one response so that the filters,
    # tags, tabs and counters stay client-side. Upgrade path: server-side filtering and cursor pagination.
    # Nothing is signed here: thumbnails come from `library/thumbs`, only for the cards on screen.
    with cloud_db.connect(ctx.settings.database_url) as conn:
        rows = cloud_db.list_references(conn, scope.brand_id)
        indexing = active_jobs(conn, scope.brand_id, ("index",))
    today = datetime.now(timezone.utc).date()
    items = []
    for row in rows:
        expiry = iso(row["expiry_date"])
        left = report_module.days_until(expiry, today)
        items.append({
            "id": str(row["id"]),
            "filename": row["filename"],
            "expiry_date": expiry or "",
            "days_left": left,
            "status": report_module.urgency_status(left),
            "credit": row["credit"] or "",
            "notes": row["notes"] or "",
            "tags": list(row["tags"]),
            "width": row["width"],
            "height": row["height"],
            "indexed": bool(row["embedded"]),
            "compared": row["compared_at"] is not None,
        })
    return {"items": items, "indexing": indexing}


@router.post(f"{BRAND}/library/thumbs")
def library_thumbs(body: ThumbsBody, scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Signed thumbnail URLs of the named references (the cards on screen), one signing per reference.

    A reference whose thumbnail object is missing falls back to its working copy, then its original;
    one with nothing to show has no entry.
    """
    names = sorted(set(body.filenames))
    with cloud_db.connect(ctx.settings.database_url) as conn:
        rows = cloud_db.reference_paths(conn, scope.brand_id, names)
    chains = {row["filename"]: [row[key] for key in ("thumb_path", "work_path", "storage_path") if row[key]] for row in rows}
    urls: dict[str, str] = {}
    for rank in range(3):  # thumbnail, working copy, original
        wanted = {name: chain[rank] for name, chain in chains.items() if name not in urls and rank < len(chain)}
        if not wanted:
            break
        try:
            signed = cloud_storage.signed_urls(ctx.settings.storage_client(), cloud_storage.BUCKET_REFS, wanted.values())
        except Exception as exc:  # noqa: BLE001 - a 502, not an empty 200: the interface retries instead of keeping blank cards
            log.warning("signing thumbnails failed", exc_info=True)
            raise HTTPException(status_code=502, detail="Les vignettes ne sont pas disponibles pour le moment.") from exc
        urls.update({name: signed[path] for name, path in wanted.items() if path in signed})
    return {"urls": urls}


@router.get(f"{BRAND}/library/{{filename}}/url")
def library_original_url(filename: str, scope: BrandScope = Depends(brand_member_dep),
                         ctx: Ctx = Depends(get_ctx)) -> dict:
    """Signed URL of one reference's original, for the edit modal."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        row = cloud_db.get_reference_by_filename(conn, scope.brand_id, safe_filename(filename))
    if row is None:
        raise HTTPException(status_code=404, detail="Référence introuvable.")
    url = ctx.sign(cloud_storage.BUCKET_REFS, [row["storage_path"]]).get(row["storage_path"])
    if not url:
        raise HTTPException(status_code=502, detail="L'image n'est pas disponible pour le moment.")
    return {"url": url}


def _read_upload(upload_file: UploadFile) -> tuple[Optional[dict], str, bytes]:
    """(why the file is refused, its name, its bytes); the first item is None when it is fine."""
    raw_name = upload_file.filename or ""
    try:
        filename = safe_filename(raw_name)
    except HTTPException as exc:
        return {"filename": raw_name or "(sans nom)", "reason": str(exc.detail)}, "", b""
    data = upload_file.file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        return {"filename": filename, "reason": "Fichier vide."}, filename, data
    if len(data) > MAX_UPLOAD_BYTES:
        return {"filename": filename, "reason": "Fichier trop lourd (30 Mo au plus)."}, filename, data
    return None, filename, data


def _store_upload(ctx: Ctx, scope: BrandScope, client, config, upload_file: UploadFile, filename: str,
                  data: bytes) -> tuple[Optional[bool], str]:
    """Store one file as a reference: (a reference of that name was replaced, why the file was refused).
    The first item is None when the file was refused."""
    img, refused = fetch.decode_reference(data, config.crawl.max_image_pixels)
    if img is None:
        return None, refused
    content_type = upload_file.content_type if (upload_file.content_type or "").startswith("image/") else None
    stored = put_reference_files(client, scope.org_id, scope.brand_id, filename, data, img, content_type)
    with cloud_db.connect(ctx.settings.database_url) as conn:
        existing = cloud_db.get_reference_by_filename(conn, scope.brand_id, filename)
        cloud_db.upsert_reference_image(
            conn, org_id=scope.org_id, brand_id=scope.brand_id, filename=filename,
            expiry_date=iso(existing["expiry_date"]) if existing else None,
            credit=existing["credit"] if existing else None,
            notes=existing["notes"] if existing else None,
            **stored,
        )
    if existing and existing["storage_path"] != stored["storage_path"]:
        # Uploaded before brands existed: the new file replaces the old one.
        ctx.remove_files(cloud_storage.BUCKET_REFS, [existing["storage_path"], existing["thumb_path"]])
    return bool(existing), ""


@router.post(f"{BRAND}/library/upload")
def upload(files: list[UploadFile] = File(...), scope: BrandScope = Depends(brand_admin_dep),
           ctx: Ctx = Depends(get_ctx)) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="Aucun fichier.")
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(status_code=400, detail=f"{MAX_FILES_PER_UPLOAD} fichiers au plus par envoi.")
    client = ctx.settings.storage_client()
    saved: list[str] = []
    replaced: list[str] = []  # also in `saved`: a reference of that name was already in the library
    failed: list[dict] = []
    with cloud_db.connect(ctx.settings.database_url) as conn:
        config = ctx.org_config(conn, scope.org_id)
    for upload_file in files:
        refusal, filename, data = _read_upload(upload_file)
        if refusal:
            failed.append(refusal)
            continue
        try:
            was_replaced, refused = _store_upload(ctx, scope, client, config, upload_file, filename, data)
        except Exception as exc:  # noqa: BLE001 - one bad file must not sink the others
            log.exception("upload of %r to brand %s failed", filename, scope.brand_id)
            failed.append({"filename": filename, "reason": upload_failure(exc)})
            continue
        if was_replaced is None:
            failed.append({"filename": filename, "reason": refused})
            continue
        saved.append(filename)
        if was_replaced:
            replaced.append(filename)
    job = None
    if saved:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            job = cloud_jobs.enqueue(conn, org_id=scope.org_id, brand_id=scope.brand_id, kind="index",
                                     created_by=scope.member.user_id)
    return {"saved": saved, "replaced": replaced, "failed": failed, "job": job}


@router.put(f"{BRAND}/library/{{filename}}")
def update_meta(filename: str, body: MetaBody, scope: BrandScope = Depends(brand_admin_dep),
                ctx: Ctx = Depends(get_ctx)) -> dict:
    filename = safe_filename(filename)
    try:
        expiry = parse_expiry(body.expiry_date)
    except RefValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        tags = None if body.tags is None else normalize_tags(body.tags)
    except RefValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with cloud_db.connect(ctx.settings.database_url) as conn:
        found = cloud_db.update_reference_meta(
            conn, scope.brand_id, filename, expiry_date=expiry,
            credit=body.credit.strip() or None, notes=body.notes.strip() or None, tags=tags,
        )
    if not found:
        raise HTTPException(status_code=404, detail="Référence introuvable.")
    return {"ok": True, "expiry_date": expiry or "", "tags": tags}


def _delete_filenames(ctx: Ctx, brand_id: uuid.UUID, filenames: list[str]) -> int:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        deleted = cloud_db.delete_references(conn, brand_id, filenames)
    ctx.remove_files(cloud_storage.BUCKET_REFS, [row[key] for row in deleted for key in ("storage_path", "thumb_path", "work_path")])
    return len(deleted)


@router.delete(f"{BRAND}/library/{{filename}}")
def delete_ref(filename: str, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    if not _delete_filenames(ctx, scope.brand_id, [safe_filename(filename)]):
        raise HTTPException(status_code=404, detail="Référence introuvable.")
    return {"ok": True}


@router.post(f"{BRAND}/library/delete")
def delete_many(body: FilenamesBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    names = [safe_filename(name) for name in body.filenames]
    return {"deleted": _delete_filenames(ctx, scope.brand_id, names)}


@router.post(f"{BRAND}/library/expiry")
def bulk_expiry(body: BulkExpiryBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    try:
        expiry = parse_expiry(body.expiry_date)
    except RefValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    names = [safe_filename(name) for name in body.filenames]
    with cloud_db.connect(ctx.settings.database_url) as conn:
        updated = cloud_db.set_expiry_for(conn, scope.brand_id, names, expiry)
    return {"updated": updated}


@router.post(f"{BRAND}/library/tags")
def bulk_tags(body: BulkTagsBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Add and/or remove tags on several references at once."""
    try:
        add, remove = normalize_tags(body.add), normalize_tags(body.remove)
    except RefValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not add and not remove:
        raise HTTPException(status_code=400, detail="Aucun tag à ajouter ou à retirer.")
    names = [safe_filename(name) for name in body.filenames]
    with cloud_db.connect(ctx.settings.database_url) as conn:
        current = cloud_db.get_reference_tags(conn, scope.brand_id, names)
        changed: dict[str, list[str]] = {}
        for name, tags in current.items():
            try:
                new = change_tags(tags, add, remove)
            except RefValidationError as exc:
                raise HTTPException(status_code=400, detail=f"{name} : {exc}") from exc
            if new != tags:
                changed[name] = new
        updated = cloud_db.set_reference_tags(conn, scope.brand_id, changed)
    return {"updated": updated}


def _csv_cell(value: str) -> str:
    """Spreadsheets run a cell that starts like a formula: such a cell gets a leading apostrophe, which makes it text.

    `=` and `@` always; `+` and `-` unless a number or a space follows ("-20 %" and "+33 6" stay as they are).
    """
    first = value[:1]
    if first in ("=", "@", "\t", "\r") or (first in ("+", "-") and len(value) > 1 and value[1] not in "0123456789 ."):
        return "'" + value
    return value


def _csv_text(value: str) -> str:
    """The reverse of `_csv_cell`, so an exported file imports back to the same text."""
    return value[1:] if value.startswith("'") and _csv_cell(value[1:]) != value[1:] else value


def _csv_entry(line: int, row: dict, has_tags: bool) -> Optional[dict]:
    """One CSV row as an import entry with its status; None for a row without a file name."""
    # A row with more cells than the header comes with a None key and a list: ignore the extra cells.
    row = {key.strip().lower(): _csv_text((value or "").strip()) for key, value in row.items() if key is not None}
    name = Path(row.get("filename", "")).name
    if not name:
        return None
    entry = {"line": line, "filename": name, "expiry_date": "", "credit": row.get("credit", ""),
             "notes": row.get("notes", ""), "tags": None, "status": "ok", "message": ""}
    try:
        entry["expiry_date"] = parse_expiry(row.get("expiry_date", "")) or ""
    except RefValidationError as exc:
        entry.update(status="bad_date", message=str(exc))
    if has_tags:
        try:
            entry["tags"] = normalize_tags(row.get("tags", ""))
        except RefValidationError as exc:
            if entry["status"] == "ok":
                entry.update(status="bad_tags", message=str(exc))
    return entry


def _csv_entries(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:2048]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    fields = {name.strip().lower() for name in (reader.fieldnames or [])}
    if "filename" not in fields:
        raise HTTPException(status_code=400, detail="Le CSV doit contenir une colonne filename.")
    has_tags = "tags" in fields  # without the column, the tags stay as they are
    entries = (_csv_entry(line, row, has_tags) for line, row in enumerate(reader, start=2))
    return [entry for entry in entries if entry is not None]


@router.post(f"{BRAND}/library/import-csv")
def import_csv(file: UploadFile = File(...), apply: bool = Query(False),
               scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Preview (default) or apply expiry dates/credits/notes from a CSV.

    Every row comes back with a status so the interface can show what
    would change before anything is written.
    """
    raw = file.file.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=400, detail="CSV trop lourd (2 Mo au plus).")
    parsed = _csv_entries(raw)
    with cloud_db.connect(ctx.settings.database_url) as conn:
        known = cloud_db.existing_filenames(conn, scope.brand_id, [entry["filename"] for entry in parsed])
        for entry in parsed:
            if entry["status"] == "ok" and entry["filename"] not in known:
                entry.update(status="unknown_file", message="Aucune image de ce nom dans la bibliothèque.")
        applied = 0
        if apply:
            for entry in parsed:
                if entry["status"] == "ok":
                    cloud_db.update_reference_meta(
                        conn, scope.brand_id, entry["filename"], expiry_date=entry["expiry_date"] or None,
                        credit=entry["credit"] or None, notes=entry["notes"] or None, tags=entry["tags"],
                    )
                    applied += 1
    return {"rows": parsed, "applied": applied}


@router.get(f"{BRAND}/library/export-csv")
def export_csv(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> Response:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        rows = cloud_db.list_references(conn, scope.brand_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["filename", "expiry_date", "credit", "notes", "tags"])
    for row in rows:
        writer.writerow([_csv_cell(row["filename"]), iso(row["expiry_date"]) or "", _csv_cell(row["credit"] or ""),
                         _csv_cell(row["notes"] or ""), _csv_cell(", ".join(row["tags"]))])
    return Response(
        content=("﻿" + buf.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="references.csv"'},
    )
