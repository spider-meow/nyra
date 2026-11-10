"""The brand's library of reference images: list, upload, edit, delete, CSV import and export."""

from __future__ import annotations

import csv
import io
import mimetypes
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Iterator, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from nyra import fetch
from nyra import report as report_module
from nyra.refs import RefValidationError, change_tags, normalize_tags, parse_rights

from .. import db as cloud_db
from .. import downloads
from .. import jobs as cloud_jobs
from .. import storage as cloud_storage
from ..csvcell import csv_cell, csv_text
from .collections import collection_json, require_collection
from .common import (
    BRAND,
    MAX_FILENAME,
    MAX_UPLOAD_BYTES,
    BrandScope,
    Ctx,
    active_jobs,
    brand_admin_dep,
    brand_member_dep,
    enforce,
    get_ctx,
    heavy_image,
    is_tiff,
    iso,
    log,
    megabytes,
    put_reference_files,
    rights_from_body,
    safe_filename,
    upload_byte_limit,
    upload_failure,
    upload_pixel_limit,
)

MAX_FILES_PER_UPLOAD = 100
MAX_SAME_NAME = 100  # visuals of one name kept side by side: `a.jpg`, then `a (1).jpg` to `a (100).jpg`
MAX_THUMBS_PER_REQUEST = 200  # the interface shows 100 cards at a time
MAX_CSV_BYTES = 2_000_000

router = APIRouter()


class MetaBody(BaseModel):
    expiry_date: str = ""
    unlimited_rights: bool = False
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
    unlimited_rights: bool = False


class BulkCreditBody(FilenamesBody):
    # Empty clears the credit of every named visual: the interface asks for it explicitly ("Retirer le crédit").
    credit: str = Field(default="", max_length=500)


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
        collections = cloud_db.list_collections(conn, scope.brand_id)
        # Only while a worker is on it: a queued index job (no worker yet) must not look like work in progress.
        indexing = active_jobs(conn, scope.brand_id, ("index",), ("running",))
    today = datetime.now(timezone.utc).date()
    items = []
    for row in rows:
        expiry = iso(row["expiry_date"])
        left = report_module.days_until(expiry, today)
        items.append({
            "id": str(row["id"]),
            "filename": row["filename"],
            "expiry_date": expiry or "",
            "unlimited_rights": row["unlimited_rights"],
            "days_left": left,
            "status": report_module.rights_status(row, left),
            "credit": row["credit"] or "",
            "notes": row["notes"] or "",
            "tags": list(row["tags"]),
            "collection_id": str(row["collection_id"]) if row["collection_id"] else None,
            "width": row["width"],
            "height": row["height"],
            "indexed": bool(row["embedded"]),
            "compared": row["compared_at"] is not None,
            "created_at": iso(row["created_at"]),
        })
    return {"items": items, "collections": [collection_json(row) for row in collections], "indexing": indexing}


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


DOWNLOAD_WAIT_SECONDS = 5  # a place for one conversion not free by then: 503 (see `heavy_image`)


def _own_reference(ctx: Ctx, scope: BrandScope, filename: str):
    """The row of one of this brand's references, or a 404."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        row = cloud_db.get_reference_by_filename(conn, scope.brand_id, safe_filename(filename))
    if row is None:
        raise HTTPException(status_code=404, detail="Référence introuvable.")
    return row


@router.get(f"{BRAND}/library/{{filename}}/downloads")
def library_downloads(filename: str, scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """The sizes this image can be downloaded in, from the row alone: nothing is read from the storage."""
    row = _own_reference(ctx, scope, filename)
    return {"choices": downloads.choices(row["width"], row["height"], row["byte_size"])}


def _original_bytes(ctx: Ctx, row) -> bytes:
    """The stored original, or a 404 (purged or never stored) / 502 (the storage does not answer)."""
    if not row["storage_path"]:
        raise HTTPException(status_code=404, detail="L'original de ce visuel n'est plus disponible.")
    try:
        return cloud_storage.download(ctx.settings.storage_client(), cloud_storage.BUCKET_REFS, row["storage_path"])
    except Exception as exc:  # noqa: BLE001 - told apart below: a storage that is down is a 502, a missing object a 404
        log.warning("reading the original for a download failed", exc_info=True)
        if cloud_storage.is_transient(exc):
            raise HTTPException(status_code=502, detail="L'image n'est pas disponible pour le moment.") from exc
        raise HTTPException(status_code=404, detail="L'original de ce visuel n'est plus disponible.") from exc


def _rendered(ctx: Ctx, row, size: str, wait: float) -> tuple[bytes, str, Optional[str]]:
    """(content, download name, media type) of one image at `size`, a key of `downloads.choices` for it. The original goes out byte for byte;
    a smaller size is made now, in the one place of `heavy_image` (waited for `wait` seconds at most) and capped in pixels."""
    data = _original_bytes(ctx, row)
    if size == downloads.ORIGINAL:
        return data, downloads.download_name(row["filename"], size, 0, Path(row["filename"]).suffix.lower()), mimetypes.guess_type(row["filename"])[0]
    with heavy_image(wait=wait):
        img, why = fetch.decode_reference(data, upload_pixel_limit(ctx.settings.base_config()))
        if img is None:
            raise HTTPException(status_code=422, detail=why)
        body, width, extension, media = downloads.resized(img, downloads.known_size(size))
    return body, downloads.download_name(row["filename"], size, width, extension), media


@router.get(f"{BRAND}/library/{{filename}}/download")
def library_download(filename: str, size: str = Query(downloads.ORIGINAL, max_length=20),
                     scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> Response:
    """One image as an attachment, at a size of `downloads.choices`. The original goes out byte for byte; a smaller size
    is made now (one conversion at a time, capped in pixels) and kept nowhere."""
    row = _own_reference(ctx, scope, filename)
    if size not in {offer["key"] for offer in downloads.choices(row["width"], row["height"], row["byte_size"])}:
        raise HTTPException(status_code=400, detail="Cette taille n'est pas proposée pour cette image.")
    body, name, media = _rendered(ctx, row, size, DOWNLOAD_WAIT_SECONDS)
    with cloud_db.connect(ctx.settings.database_url) as conn:
        scope.audit(conn, "library.download", "reference", row["id"], count=1, size=size)  # the choice, never the name or the content
    return Response(body, media_type=media or "application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name}"'})


# --- several images in one ZIP (AXE-678) ----------------------------------------------------------

ZIP_WAIT_SECONDS = 30  # a conversion waits this long for the place of `heavy_image`; after it the image is left out of the archive
ZIP_LOCK_SECONDS = 600  # a batch of one person that never ended (a stream dropped without closing) stops counting after this
# One archive at a time per person. Process memory, like the rate limit (`ratelimit.py`): with several web processes a person could run
# one per process. At most one entry per person ever seen; a finished or dropped batch frees its entry.
_ZIPS: dict[uuid.UUID, float] = {}
_ZIPS_LOCK = threading.Lock()


class ZipBody(BaseModel):
    filenames: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=1000)  # the real limit is `MAX_ZIP_FILES`, said in words
    size: str = Field(default=downloads.ORIGINAL, max_length=20)


class _Sink:
    """What `zipfile` writes to: it keeps the bytes until the stream takes them, so the archive is never whole in memory."""

    def __init__(self) -> None:
        self.parts: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.parts.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass  # nothing is buffered below

    def take(self) -> bytes:
        data, self.parts = b"".join(self.parts), []
        return data


def _zip_rows(ctx: Ctx, scope: BrandScope, body: ZipBody) -> list:
    """The rows of the visuals asked for (in the order asked, once each), or the refusal of the whole batch: nothing named that is not this brand's."""
    names = list(dict.fromkeys(safe_filename(name) for name in body.filenames))
    if not names:
        raise HTTPException(status_code=400, detail="Aucun visuel sélectionné.")
    if len(names) > downloads.MAX_ZIP_FILES:
        raise HTTPException(status_code=413, detail=f"Au plus {downloads.MAX_ZIP_FILES} visuels par fichier : téléchargez en deux fois.")
    if body.size not in (downloads.ORIGINAL, *(step[0] for step in downloads.STEPS)):
        raise HTTPException(status_code=400, detail="Cette taille n'est pas proposée.")
    with cloud_db.connect(ctx.settings.database_url) as conn:
        found = {row["filename"]: row for row in cloud_db.get_references_by_filenames(conn, scope.brand_id, names)}
    if len(found) != len(names):  # an unknown name, or one of another brand: the same answer, and nothing is made
        raise HTTPException(status_code=404, detail="Un des visuels n'existe plus dans cette bibliothèque.")
    if sum(row["byte_size"] or 0 for row in found.values()) > downloads.MAX_ZIP_BYTES:
        raise HTTPException(status_code=413, detail=f"Plus de {megabytes(downloads.MAX_ZIP_BYTES)} dans ce lot : téléchargez en deux fois.")
    return [found[name] for name in names]


def _zip_chunks(ctx: Ctx, rows: list, size: str, user_id: uuid.UUID) -> Iterator[bytes]:
    """The archive, piece by piece: each image is read, converted and written before the next is touched (so one image in memory).
    An image that cannot be made is left out and told in `LISEZ-MOI.txt`; the person's place is given back whatever happens."""
    sink, taken, skipped = _Sink(), set(), []
    now = datetime.now(timezone.utc).timetuple()[:6]
    try:
        with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED) as archive:  # stored: the images are compressed already
            for row in rows:
                offered = {offer["key"] for offer in downloads.choices(row["width"], row["height"], row["byte_size"])}
                try:
                    body, name, _ = _rendered(ctx, row, size if size in offered else downloads.ORIGINAL, ZIP_WAIT_SECONDS)  # never enlarged
                except HTTPException as exc:
                    skipped.append((row["filename"], str(exc.detail)))
                    continue
                entry = zipfile.ZipInfo(downloads.unique_name(name, taken), now)
                entry.external_attr = 0o644 << 16
                archive.writestr(entry, body)
                yield sink.take()
            if skipped:
                archive.writestr(zipfile.ZipInfo(downloads.README_NAME, now), downloads.readme(skipped).encode("utf-8"))
        yield sink.take()  # the table of contents, written when the archive is closed
    finally:
        with _ZIPS_LOCK:
            _ZIPS.pop(user_id, None)


@router.post(f"{BRAND}/library/zip")
def library_zip(body: ZipBody, scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> StreamingResponse:
    """Several images in one ZIP, all at the same size, sent as it is made and kept nowhere. Up to `MAX_ZIP_FILES` images and
    `MAX_ZIP_BYTES` announced, one archive at a time per person; a name that is not this brand's refuses the whole batch."""
    rows = _zip_rows(ctx, scope, body)
    user_id = scope.member.user_id
    with _ZIPS_LOCK:
        if time.monotonic() - _ZIPS.get(user_id, -ZIP_LOCK_SECONDS) < ZIP_LOCK_SECONDS:
            raise HTTPException(status_code=409, detail="Un autre téléchargement est déjà en préparation. Attendez qu'il se termine ou annulez-le.")
        _ZIPS[user_id] = time.monotonic()
    try:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            scope.audit(conn, "library.download", "reference", None, count=len(rows), size=body.size)  # how many and the choice, never the names
    except BaseException:  # the stream that would give the place back is never started
        with _ZIPS_LOCK:
            _ZIPS.pop(user_id, None)
        raise
    return StreamingResponse(_zip_chunks(ctx, rows, body.size, user_id), media_type="application/zip",
                             headers={"Content-Disposition": 'attachment; filename="visuels.zip"'})


def _read_upload(upload_file: UploadFile) -> tuple[Optional[dict], str, fetch.Source]:
    """(why the file is refused, its name, its content); the first item is None when it is fine. The content is the bytes, except for a TIFF:
    the spooled file itself, read from disk by the decoder and the hash, so that a 100 MB scan is never held in memory as bytes (AXE-630)."""
    raw_name = upload_file.filename or ""
    try:
        filename = safe_filename(raw_name)
    except HTTPException as exc:
        return {"filename": raw_name or "(sans nom)", "reason": str(exc.detail)}, "", b""
    upload_file.file.seek(0, 2)
    size = upload_file.file.tell()
    upload_file.file.seek(0)
    if not size:
        return {"filename": filename, "reason": "Fichier vide."}, filename, b""
    limit = upload_byte_limit(filename)
    if size > limit:
        advice = " Exportez-le en JPEG ou réduisez-le." if is_tiff(filename) else ""
        return {"filename": filename, "reason": f"Fichier trop lourd : {megabytes(size)}, {megabytes(limit)} au plus"
                f"{' pour un TIFF' if is_tiff(filename) else ''}.{advice}"}, filename, b""
    return None, filename, upload_file.file if is_tiff(filename) else upload_file.file.read()


def _new_names(conn, brand_id: uuid.UUID, files: list[UploadFile], replace: bool) -> int:
    """How many of these files add a visual to the library: all of them, unless they replace the visual of the same name."""
    names = []
    for upload_file in files:
        try:
            names.append(safe_filename(upload_file.filename or ""))
        except HTTPException:
            continue  # refused one by one in the loop below, with its reason
    if not replace:
        return len(names)
    known = conn.execute("SELECT COUNT(*) AS n FROM reference_images WHERE brand_id = %s AND filename = ANY(%s)",
                         (brand_id, list(set(names)))).fetchone()["n"]
    return len(set(names)) - known


def numbered_name(filename: str, number: int) -> str:
    """`a.jpg` and 2 give `a (2).jpg`; the stem is cut when the number would make the name too long."""
    path = Path(filename)
    tag = f" ({number})"
    return f"{path.stem[:MAX_FILENAME - len(tag) - len(path.suffix)]}{tag}{path.suffix}"


def _free_name(conn, brand_id: uuid.UUID, filename: str) -> Optional[str]:
    """The name itself if the brand has no visual of that name (capitals do not count: `A.JPG` is `a.jpg`), else the first
    `name (1).ext`, `name (2).ext`... that is free; None when the first MAX_SAME_NAME are taken."""
    candidates = [filename, *(numbered_name(filename, number) for number in range(1, MAX_SAME_NAME + 1))]
    taken = {row["name"] for row in conn.execute(
        "SELECT lower(filename) AS name FROM reference_images WHERE brand_id = %s AND lower(filename) = ANY(%s)",
        (brand_id, [candidate.lower() for candidate in candidates])).fetchall()}
    return next((candidate for candidate in candidates if candidate.lower() not in taken), None)


def _store_upload(ctx: Ctx, scope: BrandScope, client, config, upload_file: UploadFile, filename: str,
                  data: fetch.Source, rights: Optional[tuple[Optional[str], bool]], replace: bool = False) -> tuple[Optional[str], bool, str]:
    """Store one file as a reference: (the name it was saved under, a reference of that name was replaced, why it was refused).
    The name is None when the file was refused. A name already in the library is never taken over unless `replace`: the file
    is saved as `a (1).jpg`, `a (2).jpg`... `rights` (expiry date, unlimited rights) is the common value chosen for this
    upload; None keeps what a replaced reference had."""
    with heavy_image():
        img, refused = fetch.decode_reference(data, upload_pixel_limit(config))
        if img is None:
            return None, False, refused
        if not isinstance(data, bytes) and fetch.source_format(img) != "TIFF":
            # Named .tif, not a TIFF: no right to the TIFF limit. The usual one, and bytes, as for any other file.
            size = data.seek(0, 2)
            if size > MAX_UPLOAD_BYTES:
                return None, False, f"Fichier trop lourd : {megabytes(size)}, {megabytes(MAX_UPLOAD_BYTES)} au plus."
            data.seek(0)
            data = data.read()
        content_type = upload_file.content_type if (upload_file.content_type or "").startswith("image/") else None
        with cloud_db.connect(ctx.settings.database_url) as conn:
            # Held until the commit: two uploads of one name wait for each other, so the second one sees the first one's row and
            # takes the next name. ponytail: the lock is on the name asked for, so a file literally named `a (1).jpg` arriving at the
            # same moment as a rename to it is not serialized (the row would be replaced, the stored original overwritten): rare, not handled.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{scope.brand_id}/{filename.lower()}",))
            # ponytail: two identical files sent at the same moment under different names are not serialized: both are kept.
            twin = cloud_db.reference_with_hash(conn, scope.brand_id, fetch.sha256_of(data))
            if twin:
                return None, False, f"Identique à « {twin} », déjà dans la bibliothèque : rien n'a été ajouté."
            name = filename if replace else _free_name(conn, scope.brand_id, filename)
            if name is None:
                return None, False, f"{MAX_SAME_NAME} visuels portent déjà ce nom : renommez ce fichier."
            stored = put_reference_files(client, scope.org_id, scope.brand_id, name, data, img, content_type)
            existing = cloud_db.get_reference_by_filename(conn, scope.brand_id, name)
            ref_id = cloud_db.upsert_reference_image(
                conn, org_id=scope.org_id, brand_id=scope.brand_id, filename=name,
                expiry_date=rights[0] if rights else iso(existing["expiry_date"]) if existing else None,
                unlimited_rights=rights[1] if rights else existing["unlimited_rights"] if existing else False,
                credit=existing["credit"] if existing else None,
                notes=existing["notes"] if existing else None,
                **stored,
            )
            scope.audit(conn, "library.upload", "reference", ref_id, replaced=bool(existing), renamed=name != filename)  # one row per file, with its write
        if existing and existing["storage_path"] != stored["storage_path"]:
            # Uploaded before brands existed: the new file replaces the old one.
            ctx.remove_files(cloud_storage.BUCKET_REFS, [existing["storage_path"], existing["thumb_path"]])
        return name, bool(existing), ""


@router.post(f"{BRAND}/library/upload")
def upload(files: list[UploadFile] = File(...), expiry_date: str = Form(""), unlimited_rights: bool = Form(False),
           collection_id: Optional[uuid.UUID] = Form(None), replace: bool = Form(False),
           scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Add visuals. A file whose name is already in the library is saved as `a (1).jpg`, `a (2).jpg`... and reported in `renamed`;
    with `replace` it replaces the visual of that name instead (which keeps its credit, notes and, without a common value,
    its rights). A common expiry date (or unlimited rights) applies to these files only, replaced ones included."""
    expiry, unlimited = rights_from_body(expiry_date, unlimited_rights)
    rights = (expiry, unlimited) if expiry or unlimited else None
    if not files:
        raise HTTPException(status_code=400, detail="Aucun fichier.")
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(status_code=400, detail=f"{MAX_FILES_PER_UPLOAD} fichiers au plus par envoi.")
    if collection_id:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            require_collection(conn, scope.brand_id, collection_id)
    client = ctx.settings.storage_client()
    saved: list[str] = []
    replaced: list[str] = []  # also in `saved`: `replace` was asked and a reference of that name was already in the library
    renamed: list[dict] = []  # also in `saved`, under the new name: {"from": the name sent, "to": the name it was saved under}
    failed: list[dict] = []
    with cloud_db.connect(ctx.settings.database_url) as conn:
        config = ctx.org_config(conn, scope.org_id)
        new = _new_names(conn, scope.brand_id, files, replace)
    enforce(ctx, scope.org_id, "references", adding=new)
    enforce(ctx, scope.org_id, "storage", adding=sum(upload_file.size or 0 for upload_file in files))
    for upload_file in files:
        refusal, filename, data = _read_upload(upload_file)
        if refusal:
            failed.append(refusal)
            continue
        try:
            name, was_replaced, refused = _store_upload(ctx, scope, client, config, upload_file, filename, data, rights, replace)
        except Exception as exc:  # noqa: BLE001 - one bad file must not sink the others
            log.exception("upload of %r to brand %s failed", filename, scope.brand_id)
            failed.append({"filename": filename, "reason": upload_failure(exc)})
            continue
        if name is None:
            failed.append({"filename": filename, "reason": refused})
            continue
        saved.append(name)
        if was_replaced:
            replaced.append(name)
        if name != filename:
            renamed.append({"from": filename, "to": name})
    job = None
    if saved:
        with cloud_db.connect(ctx.settings.database_url) as conn:
            if collection_id:  # a replaced visual moves too: the person dropped it in that collection
                cloud_db.set_reference_collection(conn, scope.brand_id, saved, collection_id)
            job = cloud_jobs.enqueue(conn, org_id=scope.org_id, brand_id=scope.brand_id, kind="index",
                                     created_by=scope.member.user_id)
    return {"saved": saved, "replaced": replaced, "renamed": renamed, "failed": failed, "job": job}


@router.put(f"{BRAND}/library/{{filename}}")
def update_meta(filename: str, body: MetaBody, scope: BrandScope = Depends(brand_admin_dep),
                ctx: Ctx = Depends(get_ctx)) -> dict:
    filename = safe_filename(filename)
    expiry, unlimited = rights_from_body(body.expiry_date, body.unlimited_rights)
    try:
        tags = None if body.tags is None else normalize_tags(body.tags)
    except RefValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with cloud_db.connect(ctx.settings.database_url) as conn:
        found = cloud_db.update_reference_meta(
            conn, scope.brand_id, filename, expiry_date=expiry, unlimited_rights=unlimited,
            credit=body.credit.strip() or None, notes=body.notes.strip() or None, tags=tags,
        )
        if found:
            scope.audit(conn, "library.update", "reference", found, unlimited_rights=unlimited, has_expiry=bool(expiry))
    if not found:
        raise HTTPException(status_code=404, detail="Référence introuvable.")
    return {"ok": True, "expiry_date": expiry or "", "unlimited_rights": unlimited, "tags": tags}


def _delete_filenames(ctx: Ctx, scope: BrandScope, filenames: list[str]) -> int:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        deleted = cloud_db.delete_references(conn, scope.brand_id, filenames)
        if deleted:
            scope.audit(conn, "library.delete", "reference", deleted[0]["id"] if len(deleted) == 1 else None, count=len(deleted))
    ctx.remove_files(cloud_storage.BUCKET_REFS, [row[key] for row in deleted for key in ("storage_path", "thumb_path", "work_path")])
    return len(deleted)


@router.delete(f"{BRAND}/library/{{filename}}")
def delete_ref(filename: str, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    if not _delete_filenames(ctx, scope, [safe_filename(filename)]):
        raise HTTPException(status_code=404, detail="Référence introuvable.")
    return {"ok": True}


@router.post(f"{BRAND}/library/delete")
def delete_many(body: FilenamesBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    names = [safe_filename(name) for name in body.filenames]
    return {"deleted": _delete_filenames(ctx, scope, names)}


@router.post(f"{BRAND}/library/expiry")
def bulk_expiry(body: BulkExpiryBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    expiry, unlimited = rights_from_body(body.expiry_date, body.unlimited_rights)
    names = [safe_filename(name) for name in body.filenames]
    with cloud_db.connect(ctx.settings.database_url) as conn:
        updated = cloud_db.set_expiry_for(conn, scope.brand_id, names, expiry, unlimited)
        scope.audit(conn, "library.expiry", "reference", count=updated, unlimited_rights=unlimited, has_expiry=bool(expiry))
    return {"updated": updated}


@router.post(f"{BRAND}/library/credit")
def bulk_credit(body: BulkCreditBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Set the photo credit of several references at once, or clear it (an empty credit)."""
    credit = body.credit.strip()
    names = [safe_filename(name) for name in body.filenames]
    with cloud_db.connect(ctx.settings.database_url) as conn:
        updated = cloud_db.set_credit_for(conn, scope.brand_id, names, credit or None)
        scope.audit(conn, "library.credit", "reference", count=updated, cleared=not credit)
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
        scope.audit(conn, "library.tags", "reference", count=updated, added=len(add), removed=len(remove))
    return {"updated": updated}


def _csv_entry(line: int, row: dict, has_tags: bool) -> Optional[dict]:
    """One CSV row as an import entry with its status; None for a row without a file name."""
    # A row with more cells than the header comes with a None key and a list: ignore the extra cells.
    row = {key.strip().lower(): csv_text((value or "").strip()) for key, value in row.items() if key is not None}
    name = Path(row.get("filename", "")).name
    if not name:
        return None
    entry = {"line": line, "filename": name, "expiry_date": "", "unlimited_rights": False, "credit": row.get("credit", ""),
             "notes": row.get("notes", ""), "tags": None, "status": "ok", "message": ""}
    try:
        expiry, entry["unlimited_rights"] = parse_rights(row.get("expiry_date", ""))
        entry["expiry_date"] = expiry or ""
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
                        unlimited_rights=entry["unlimited_rights"], credit=entry["credit"] or None, notes=entry["notes"] or None, tags=entry["tags"],
                    )
                    applied += 1
            scope.audit(conn, "library.import_csv", "reference", count=applied, rows=len(parsed))
    return {"rows": parsed, "applied": applied}


@router.get(f"{BRAND}/library/export-csv")
def export_csv(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> Response:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        rows = cloud_db.list_references(conn, scope.brand_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["filename", "expiry_date", "credit", "notes", "tags"])
    for row in rows:
        writer.writerow([csv_cell(row["filename"]), "illimité" if row["unlimited_rights"] else iso(row["expiry_date"]) or "", csv_cell(row["credit"] or ""),
                         csv_cell(row["notes"] or ""), csv_cell(", ".join(row["tags"]))])
    return Response(
        content=("﻿" + buf.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="references.csv"'},
    )
