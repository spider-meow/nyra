"""Collections: the folders of a brand's library (a shoot, a season), three levels deep, and filing visuals in them."""

from __future__ import annotations

import uuid
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db as cloud_db
from .common import BRAND, BrandScope, Ctx, brand_admin_dep, get_ctx, safe_filename

MAX_DEPTH = 3

router = APIRouter()


class CollectionBody(BaseModel):
    name: str = Field(max_length=200)
    parent_id: Optional[uuid.UUID] = None  # ignored when renaming


class MoveBody(BaseModel):
    parent_id: Optional[uuid.UUID] = None  # None = the top level


class FileBody(BaseModel):
    filenames: list[str] = Field(default_factory=list, max_length=5000)
    collection_id: Optional[uuid.UUID] = None  # None un-files


def collection_json(row: dict) -> dict:
    return {"id": str(row["id"]), "parent_id": str(row["parent_id"]) if row["parent_id"] else None, "name": row["name"]}


def _name(raw: str) -> str:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= 80:
        raise HTTPException(status_code=400, detail="Le nom d'une collection compte de 1 à 80 caractères.")
    return name


def _taken() -> HTTPException:
    return HTTPException(status_code=409, detail="Une collection de ce nom existe déjà à cet endroit.")


def require_collection(conn, brand_id: uuid.UUID, collection_id: uuid.UUID) -> None:
    if cloud_db.get_collection(conn, brand_id, collection_id) is None:
        raise HTTPException(status_code=404, detail="Collection introuvable.")


@router.post(f"{BRAND}/collections")
def add_collection(body: CollectionBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    name = _name(body.name)
    with cloud_db.connect(ctx.settings.database_url) as conn:
        cloud_db.lock_collections(conn, scope.brand_id)
        if body.parent_id:
            require_collection(conn, scope.brand_id, body.parent_id)
            if cloud_db.collection_depth(conn, body.parent_id) >= MAX_DEPTH:
                raise HTTPException(status_code=400, detail=f"Les collections vont sur {MAX_DEPTH} niveaux au plus.")
        try:
            row = cloud_db.add_collection(conn, scope.org_id, scope.brand_id, body.parent_id, name)
        except psycopg.errors.UniqueViolation as exc:
            raise _taken() from exc
    return {"collection": collection_json(row)}


@router.put(f"{BRAND}/collections/{{collection_id}}")
def rename_collection(collection_id: uuid.UUID, body: CollectionBody, scope: BrandScope = Depends(brand_admin_dep),
                      ctx: Ctx = Depends(get_ctx)) -> dict:
    name = _name(body.name)
    with cloud_db.connect(ctx.settings.database_url) as conn:
        try:
            found = cloud_db.rename_collection(conn, scope.brand_id, collection_id, name)
        except psycopg.errors.UniqueViolation as exc:
            raise _taken() from exc
    if not found:
        raise HTTPException(status_code=404, detail="Collection introuvable.")
    return {"ok": True}


@router.post(f"{BRAND}/collections/{{collection_id}}/move")
def move_collection(collection_id: uuid.UUID, body: MoveBody, scope: BrandScope = Depends(brand_admin_dep),
                    ctx: Ctx = Depends(get_ctx)) -> dict:
    """Moves the collection, with everything in it, under another one (or to the top with no `parent_id`)."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        cloud_db.lock_collections(conn, scope.brand_id)
        require_collection(conn, scope.brand_id, collection_id)
        if body.parent_id:
            require_collection(conn, scope.brand_id, body.parent_id)
            below = cloud_db.collection_subtree(conn, collection_id)
            if body.parent_id in below:
                raise HTTPException(status_code=400, detail="Une collection ne peut pas aller dans elle-même ni dans l'une de ses sous-collections.")
            if cloud_db.collection_depth(conn, body.parent_id) + max(below.values()) > MAX_DEPTH:
                raise HTTPException(status_code=400, detail=f"Les collections vont sur {MAX_DEPTH} niveaux au plus.")
        try:
            moved = cloud_db.move_collection(conn, scope.brand_id, collection_id, body.parent_id)
        except psycopg.errors.UniqueViolation as exc:
            raise _taken() from exc
    if not moved:
        raise HTTPException(status_code=404, detail="Collection introuvable.")
    return {"ok": True}


@router.delete(f"{BRAND}/collections/{{collection_id}}")
def delete_collection(collection_id: uuid.UUID, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Deletes the collection and its sub-collections; their visuals stay in the library, unfiled."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if not cloud_db.delete_collection(conn, scope.brand_id, collection_id):
            raise HTTPException(status_code=404, detail="Collection introuvable.")
    return {"ok": True}


@router.post(f"{BRAND}/library/collection")
def file_references(body: FileBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """Put visuals in a collection (or take them out of any, with no `collection_id`)."""
    names = [safe_filename(name) for name in body.filenames]
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if body.collection_id:
            require_collection(conn, scope.brand_id, body.collection_id)
        return {"updated": cloud_db.set_reference_collection(conn, scope.brand_id, names, body.collection_id)}
