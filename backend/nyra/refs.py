"""Reference image ingestion.

`RefSource` is the seam for swapping the hand-maintained CSV+folder input
(the CLI path) for a DAM export later, without touching the rest of the
pipeline: anything that can yield `RefEntry` objects works.

Expiry dates are the one field where a silent misreading is a legal risk,
so parsing is strict: ISO `YYYY-MM-DD`, or day-first `DD/MM/YYYY`
(also with `-` or `.`). Month-first dates are never guessed.
"""

from __future__ import annotations

import csv
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator, Optional

from PIL import Image

from nyra.config import Config
from nyra.match import compute_clip_embeddings, compute_flip_hashes, compute_hashes

REQUIRED_CSV_COLUMNS = {"filename", "expiry_date"}
_DAY_FIRST = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$")


@dataclass(frozen=True)
class RefEntry:
    filename: str
    image_path: Path
    expiry_date: Optional[str]  # ISO 8601 "YYYY-MM-DD", or None if unknown
    credit: Optional[str] = None
    notes: Optional[str] = None


class RefSource(ABC):
    """Anything that can enumerate the rights-managed reference library."""

    @abstractmethod
    def iter_refs(self) -> Iterator[RefEntry]:
        ...


class RefValidationError(ValueError):
    pass


def parse_expiry(raw: Optional[str]) -> Optional[str]:
    """ISO date string, None for blank, RefValidationError for anything else."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        pass
    found = _DAY_FIRST.match(raw)
    if found:
        day, month, year = (int(part) for part in found.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError as exc:
            hint = " (le mois vient en second : JJ/MM/AAAA)" if month > 12 and day <= 12 else ""
            raise RefValidationError(f"Date impossible : {raw!r}{hint}") from exc
    raise RefValidationError(f"Date illisible : {raw!r}. Formats acceptés : AAAA-MM-JJ ou JJ/MM/AAAA.")


class CsvRefSource(RefSource):
    """Reads refs.csv (filename, expiry_date, credit, notes) + an images folder."""

    def __init__(self, images_dir: Path | str, csv_path: Path | str):
        self.images_dir = Path(images_dir)
        self.csv_path = Path(csv_path)

    def iter_refs(self) -> Iterator[RefEntry]:
        if not self.csv_path.exists():
            raise RefValidationError(f"refs CSV not found: {self.csv_path}")
        if not self.images_dir.exists():
            raise RefValidationError(f"refs image folder not found: {self.images_dir}")

        with self.csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            missing = REQUIRED_CSV_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise RefValidationError(f"refs.csv is missing required columns: {sorted(missing)}")

            for line, row in enumerate(reader, start=2):
                filename = (row.get("filename") or "").strip()
                if not filename:
                    continue
                image_path = self.images_dir / filename
                if not image_path.exists():
                    raise RefValidationError(f"referenced image missing on disk: {image_path}")
                try:
                    expiry = parse_expiry(row.get("expiry_date", ""))
                except RefValidationError as exc:
                    raise RefValidationError(f"refs.csv line {line} ({filename}): {exc}") from exc
                yield RefEntry(
                    filename=filename,
                    image_path=image_path,
                    expiry_date=expiry,
                    credit=(row.get("credit") or "").strip() or None,
                    notes=(row.get("notes") or "").strip() or None,
                )


@dataclass
class RefFeatures:
    width: int
    height: int
    phash: str
    dhash: str
    phash_flip: str
    dhash_flip: str


def reference_features(img: Image.Image) -> RefFeatures:
    rgb = img.convert("RGB")
    phash, dhash = compute_hashes(rgb)
    phash_flip, dhash_flip = compute_flip_hashes(rgb)
    return RefFeatures(img.size[0], img.size[1], phash, dhash, phash_flip, dhash_flip)


def ingest(
    source: RefSource,
    db_path: Path | str,
    config: Config,
    *,
    compute_embeddings: bool = True,
    progress=None,
) -> int:
    """Hash (and optionally embed) every reference image into a SQLite database."""
    from nyra import db

    db.init_db(db_path)
    entries = list(source.iter_refs())
    batch = max(1, config.match.embedding_batch_size)
    with db.connect(db_path) as conn:
        for start in range(0, len(entries), batch):
            chunk = entries[start : start + batch]
            images = []
            for entry in chunk:
                with Image.open(entry.image_path) as img:
                    img.load()
                    images.append(img.convert("RGB"))
            embeddings = compute_clip_embeddings(images, config.match) if compute_embeddings else [None] * len(chunk)
            for entry, img, embedding in zip(chunk, images, embeddings):
                features = reference_features(img)
                db.upsert_reference_image(
                    conn,
                    filename=entry.filename,
                    path=str(entry.image_path),
                    expiry_date=entry.expiry_date,
                    credit=entry.credit,
                    notes=entry.notes,
                    phash=features.phash,
                    dhash=features.dhash,
                    phash_flip=features.phash_flip,
                    dhash_flip=features.dhash_flip,
                    embedding=embedding,
                    width=features.width,
                    height=features.height,
                )
            if progress:
                progress(min(start + batch, len(entries)), len(entries))
    return len(entries)
