"""Labels of a brand's images: the four types of site image (logo, packshot…) and the contents (carafe…).

A person gives a few examples; the algorithm proposes the label of the other images (see `nyra/labels.py`).
"""

from __future__ import annotations

import uuid

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from nyra import labels as engine

from .. import db as cloud_db
from .. import labels as cloud_labels
from .. import storage as cloud_storage
from .common import BRAND, BrandScope, Ctx, brand_admin_dep, brand_member_dep, get_ctx, unreferenced_images

router = APIRouter()

SAMPLE_MAX = 100
# Images the sample picks from: the most recent ones. Bounds the work on a brand with a huge site.
SAMPLE_POOL = 3000


class LabelBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class DecisionBody(BaseModel):
    decision: str
    site_image_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    reference_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


def _clean_name(raw: str) -> str:
    name = " ".join(raw.split()).lower()
    if not 1 <= len(name) <= 40:
        raise HTTPException(status_code=400, detail="Le nom d'une étiquette compte de 1 à 40 caractères.")
    return name


@router.get(f"{BRAND}/labels")
def labels(scope: BrandScope = Depends(brand_member_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """The brand's labels with their number of examples, and how well the type examples predict themselves."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        cloud_labels.ensure_types(conn, scope.org_id, scope.brand_id)
        rows = cloud_labels.list_labels(conn, scope.brand_id)
        quality = cloud_labels.quality(conn, scope.brand_id)
    return {
        "labels": [{"id": str(row["id"]), "kind": row["kind"], "name": row["name"], "examples": row["examples"]} for row in rows],
        "quality": quality,
        "min_examples": engine.MIN_EXAMPLES,
    }


@router.post(f"{BRAND}/labels")
def add_label(body: LabelBody, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    """A content label (the types exist already). Asking for a name that exists returns it."""
    name = _clean_name(body.name)
    with cloud_db.connect(ctx.settings.database_url) as conn:
        label = cloud_labels.add_content_label(conn, scope.org_id, scope.brand_id, name)
    return {"id": str(label["id"]), "kind": "content", "name": label["name"], "examples": 0}


@router.delete(f"{BRAND}/labels/{{label_id}}")
def delete_label(label_id: uuid.UUID, scope: BrandScope = Depends(brand_admin_dep), ctx: Ctx = Depends(get_ctx)) -> dict:
    with cloud_db.connect(ctx.settings.database_url) as conn:
        if not cloud_labels.delete_content_label(conn, scope.brand_id, label_id):
            raise HTTPException(status_code=404, detail="Étiquette introuvable (les quatre types ne se suppriment pas).")
    return {"ok": True}


@router.post(f"{BRAND}/labels/{{label_id}}/images")
def decide(label_id: uuid.UUID, body: DecisionBody, scope: BrandScope = Depends(brand_admin_dep),
           ctx: Ctx = Depends(get_ctx)) -> dict:
    """Say that images are (`yes`), are not (`no`) this label, or forget it (`clear`)."""
    if body.decision not in cloud_labels.DECISIONS:
        raise HTTPException(status_code=400, detail="Décision inconnue.")
    if not body.site_image_ids and not body.reference_ids:
        raise HTTPException(status_code=400, detail="Aucune image.")
    with cloud_db.connect(ctx.settings.database_url) as conn:
        label = cloud_labels.get_label(conn, scope.brand_id, label_id)
        if label is None:
            raise HTTPException(status_code=404, detail="Étiquette introuvable.")
        if label["kind"] == "type" and body.decision == "no":
            raise HTTPException(status_code=400, detail="Un type se refuse en choisissant un autre type.")
        if label["kind"] == "type" and body.reference_ids:
            raise HTTPException(status_code=400, detail="Un type d'image ne s'applique qu'aux images des sites.")
        written = cloud_labels.set_decision(conn, label, scope.brand_id, body.decision, site_image_ids=body.site_image_ids,
                                            reference_ids=body.reference_ids, user_id=scope.member.user_id)
    return {"written": written}


@router.get(f"{BRAND}/labels/assignments")
def assignments(target: str = Query("site", pattern="^(site|reference)$"), scope: BrandScope = Depends(brand_member_dep),
                ctx: Ctx = Depends(get_ctx)) -> dict:
    """The labels of each image of the brand's sites or library: decided by a person, or proposed."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        found = cloud_labels.assignments(conn, scope.brand_id, target)
    return {"assignments": {str(image_id): entries for image_id, entries in found.items()}}


@router.get(f"{BRAND}/labels/sample")
def sample(count: int = Query(40, ge=1, le=SAMPLE_MAX), scope: BrandScope = Depends(brand_member_dep),
           ctx: Ctx = Depends(get_ctx)) -> dict:
    """Images of "Droits non vérifiés" to type by hand: unlike each other and unlike the ones already typed,
    so a few dozen answers teach the most."""
    with cloud_db.connect(ctx.settings.database_url) as conn:
        config = ctx.org_config(conn, scope.org_id)
        groups = unreferenced_images(conn, scope.brand_id, config)[:SAMPLE_POOL]
        typed = {row["site_image_id"] for row in conn.execute(
            """SELECT il.site_image_id FROM image_labels il JOIN labels l ON l.id = il.label_id
               WHERE l.brand_id = %s AND l.kind = 'type' AND il.positive""", (scope.brand_id,)).fetchall()}
        todo = [group for group in groups if not typed.intersection(row["id"] for row in group["rows"])]
        vectors = cloud_labels.embeddings_of(conn, [group["lead"]["id"] for group in todo])
        taken = cloud_labels.type_example_vectors(conn, scope.brand_id)
    todo = [group for group in todo if group["lead"]["id"] in vectors]
    picked = engine.diverse_sample(np.stack([vectors[group["lead"]["id"]] for group in todo]) if todo else np.zeros((0, 512)),
                                   count, taken)
    leads = [todo[index]["lead"] for index in picked]
    urls = ctx.sign(cloud_storage.BUCKET_SITE_IMAGES, [row["thumb_path"] for row in leads] + [row["storage_path"] for row in leads])
    return {"items": [
        {"id": str(todo[index]["lead"]["id"]), "ids": [str(row["id"]) for row in todo[index]["rows"]],
         "url": todo[index]["lead"]["url"], "width": todo[index]["lead"]["width"], "height": todo[index]["lead"]["height"],
         "thumb": urls.get(todo[index]["lead"]["thumb_path"] or "") or urls.get(todo[index]["lead"]["storage_path"] or "", ""),
         "image": urls.get(todo[index]["lead"]["storage_path"] or "", "")}
        for index in picked
    ], "remaining": max(0, len(todo) - len(picked))}
