"""Reference image ingestion.

`RefSource` is the seam for swapping the hand-maintained CSV+folder input
(the demo path) for a Brandcenter export later, without touching the rest
of the pipeline: anything that can yield `RefEntry` objects works.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

from PIL import Image

from rightswatch import db
from rightswatch.config import Config
from rightswatch.match import compute_hashes, compute_clip_embedding

REQUIRED_CSV_COLUMNS = {"filename", "expiry_date"}


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


def _parse_expiry(raw: str) -> Optional[str]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        # tolerate a couple of common human formats from a hand-filled CSV
        for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                continue
        raise RefValidationError(f"Unparseable expiry_date: {raw!r} (expected YYYY-MM-DD)")


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

        with self.csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            missing = REQUIRED_CSV_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise RefValidationError(f"refs.csv is missing required columns: {sorted(missing)}")

            for row in reader:
                filename = (row.get("filename") or "").strip()
                if not filename:
                    continue
                image_path = self.images_dir / filename
                if not image_path.exists():
                    raise RefValidationError(f"referenced image missing on disk: {image_path}")
                yield RefEntry(
                    filename=filename,
                    image_path=image_path,
                    expiry_date=_parse_expiry(row.get("expiry_date", "")),
                    credit=(row.get("credit") or "").strip() or None,
                    notes=(row.get("notes") or "").strip() or None,
                )


def ingest(
    source: RefSource,
    db_path: Path | str,
    config: Config,
    *,
    compute_embeddings: bool = True,
    progress=None,
) -> int:
    """Hash (and optionally embed) every reference image and persist it.

    Returns the number of reference images ingested. `progress`, if given,
    is called with (done, total_so_far) as entries are processed (total is
    unknown up front since `RefSource` is a generator).
    """
    db.init_db(db_path)
    entries = list(source.iter_refs())

    with db.connect(db_path) as conn:
        for i, entry in enumerate(entries):
            with Image.open(entry.image_path) as img:
                img.load()
                width, height = img.size
                phash, dhash = compute_hashes(img)
                embedding = compute_clip_embedding(img, config.match) if compute_embeddings else None

            db.upsert_reference_image(
                conn,
                filename=entry.filename,
                path=str(entry.image_path),
                expiry_date=entry.expiry_date,
                credit=entry.credit,
                notes=entry.notes,
                phash=phash,
                dhash=dhash,
                embedding=embedding,
                width=width,
                height=height,
            )
            if progress:
                progress(i + 1, len(entries))

    return len(entries)
