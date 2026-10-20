"""When a client leaves: erase everything of an organization, or hand its data over. Operator commands only
(`nyra cloud-delete-org`, `nyra cloud-export-org`); no route calls this.

**Erasing** removes, in this order: the files of the organization in every bucket (prefix `{org_id}/`), then its row
in `organizations` (every other table follows through `on delete cascade`, checked below and by a test that reads
the catalog), then the Auth accounts that belonged to nothing else. The files go first because a failure there
leaves the organization intact and the command can simply be run again; a failure after the database step leaves
only powerless accounts, which the report names. A trace outlives the organization: a log line and one row of
`deleted_organizations` (identifier, name, date, operator, counts: no content, no e-mail).

**Exporting** writes readable CSV files and the original files to a folder, one row or one file at a time (memory
does not grow with the organization), and says plainly what it could not write.
"""

from __future__ import annotations

import csv
import getpass
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Optional

from psycopg import sql
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from . import audit as cloud_audit
from . import db as cloud_db
from . import storage as cloud_storage
from .routes.library import _csv_cell

log = logging.getLogger("nyra.offboarding")

BUCKETS = (cloud_storage.BUCKET_REFS, cloud_storage.BUCKET_SITE_IMAGES, cloud_storage.BUCKET_REPORTS)

# Rows of an organization, table by table. Every table with an `org_id` column is counted by that column. These
# have none: how to find their rows. Anything else in `public` must be in GLOBAL, or the test fails: a new table
# cannot slip past the erasure unnoticed.
INDIRECT = {
    "organizations": "id = %(org)s",
    "brand_logos": "brand_id IN (SELECT id FROM brands WHERE org_id = %(org)s)",
    "image_pages": "page_id IN (SELECT id FROM pages WHERE org_id = %(org)s)",
}
GLOBAL = {"_test_migrations", "platform_staff", "service_metrics", "deleted_organizations"}  # shared, or outliving the organization

FETCH_BATCH = 500
MAX_FILE_BYTES = 200_000_000
MAX_TOTAL_BYTES = 50_000_000_000


class OffboardingError(Exception):
    """A refusal or a failure the operator must read; the message says what to do."""


def _org_tables(conn) -> list[str]:
    rows = conn.execute(
        """SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' AND column_name = 'org_id'
           AND table_name <> ALL(%s) ORDER BY table_name""", (list(GLOBAL),)).fetchall()
    return [row["table_name"] for row in rows]


def count_rows(conn, org_id: uuid.UUID) -> dict[str, int]:
    """Rows of the organization per table (zero included), for the report and for the check after the erasure."""
    where = {table: "org_id = %(org)s" for table in _org_tables(conn)} | INDIRECT
    counts = {}
    for table, clause in sorted(where.items()):
        query = sql.SQL("SELECT count(*) AS n FROM {} WHERE ").format(sql.Identifier("public", table)) + sql.SQL(clause)
        counts[table] = conn.execute(query, {"org": org_id}).fetchone()["n"]
    return counts


def _exclusive_users(conn, org_id: uuid.UUID) -> list[dict]:
    """Members of this organization only: not members of another one, not on the Nyra team."""
    return conn.execute(
        """SELECT u.id, u.email FROM memberships m JOIN auth.users u ON u.id = m.user_id
           WHERE m.org_id = %s
             AND NOT EXISTS (SELECT 1 FROM memberships o WHERE o.user_id = m.user_id AND o.org_id <> m.org_id)
             AND NOT EXISTS (SELECT 1 FROM platform_staff s WHERE s.user_id = m.user_id)
           ORDER BY lower(u.email)""", (org_id,)).fetchall()


def delete_organization(database_url: str, client, slug: str, *, confirm_name: Optional[str], operator: str = "",
                        dry_run: bool = False) -> dict:
    """Erase the organization with this slug and return the report. `confirm_name` must be its exact name (the
    command asks the operator to type it); `dry_run` lists what would go and changes nothing."""
    operator = operator or getpass.getuser()
    with cloud_db.connect(database_url) as conn:
        org = cloud_db.get_organization_by_slug(conn, slug)
        if org is None:
            raise OffboardingError(f"Aucune organisation avec l'identifiant {slug!r}.")
        if not dry_run and confirm_name != org["name"]:
            raise OffboardingError("Confirmation refusée : le nom saisi n'est pas exactement celui de l'organisation.")
        busy = conn.execute("SELECT count(*) AS n FROM jobs WHERE org_id = %s AND status IN ('queued', 'running')",
                            (org["id"],)).fetchone()["n"]
        if busy and not dry_run:
            raise OffboardingError(f"{busy} tâche(s) en cours ou en attente : arrêtez-les (ou attendez) avant de supprimer.")
        rows, users = count_rows(conn, org["id"]), _exclusive_users(conn, org["id"])
    org_id = org["id"]
    files = {bucket: cloud_storage.delete_prefix(client, bucket, str(org_id), dry_run=dry_run) for bucket in BUCKETS}
    report: dict[str, Any] = {"organization": {"id": str(org_id), "name": org["name"], "slug": org["slug"]},
                              "dry_run": dry_run, "operator": operator, "rows": rows, "files": files,
                              "users": [user["email"] or str(user["id"]) for user in users], "users_failed": []}
    if dry_run:
        return report
    with cloud_db.connect(database_url) as conn:  # one transaction: the trace and the erasure stand or fall together
        conn.execute("INSERT INTO deleted_organizations (org_id, name, slug, operator, report) VALUES (%s, %s, %s, %s, %s)",
                     (org_id, org["name"], org["slug"], operator, Jsonb({"rows": rows, "files": files, "users": len(users)})))
        conn.execute("DELETE FROM organizations WHERE id = %s", (org_id,))
        left = {table: n for table, n in count_rows(conn, org_id).items() if n}
        if left:
            raise OffboardingError(f"Des lignes restent après la suppression (annulée) : {left}")
    for user in users:
        try:
            client.auth.admin.delete_user(str(user["id"]))
        except Exception as exc:  # noqa: BLE001 - the others are still deleted; the report lists this one
            log.exception("deleting Auth account %s of the deleted organization %s failed", user["id"], org_id)
            report["users_failed"].append({"id": str(user["id"]), "error": str(exc)[:200]})
    log.warning("ORGANIZATION DELETED %s", json.dumps({
        "org_id": str(org_id), "name": org["name"], "slug": org["slug"], "operator": operator, "files": files,
        "rows": sum(rows.values()), "users_deleted": len(users) - len(report["users_failed"]),
        "users_failed": [item["id"] for item in report["users_failed"]]}))
    return report


# --- export ----------------------------------------------------------------------------------------------------

EXPORTS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("marques.csv", ("marque", "identifiant", "créée le"),
     "SELECT name, slug, created_at FROM brands WHERE org_id = %(org)s ORDER BY name"),
    ("sites.csv", ("marque", "adresse", "libellé", "ajoutée le"),
     """SELECT b.name, s.url, s.label, s.created_at FROM sites s JOIN brands b ON b.id = s.brand_id
        WHERE s.org_id = %(org)s ORDER BY b.name, s.url"""),
    ("references.csv", ("marque", "fichier", "collection", "échéance", "droits illimités", "crédit", "notes", "tags", "ajoutée le",
                        "fichier dans l'export"),
     """SELECT b.name, r.filename, c.name, r.expiry_date, r.unlimited_rights, r.credit, r.notes, array_to_string(r.tags, ', '),
               r.created_at, 'files/refs/' || r.storage_path
        FROM reference_images r JOIN brands b ON b.id = r.brand_id LEFT JOIN collections c ON c.id = r.collection_id
        WHERE r.org_id = %(org)s ORDER BY b.name, r.filename"""),
    ("decisions.csv", ("marque", "référence", "image du site", "décision", "par", "le"),
     """SELECT b.name, r.filename, si.url, v.decision, u.email, v.updated_at
        FROM reviews v JOIN reference_images r ON r.id = v.reference_id JOIN brands b ON b.id = r.brand_id
        JOIN site_images si ON si.id = v.site_image_id LEFT JOIN auth.users u ON u.id = v.reviewed_by
        WHERE v.org_id = %(org)s ORDER BY b.name, v.updated_at"""),
    ("exclusions.csv", ("marque", "adresse de l'image", "raison", "empreinte", "valeur", "par", "le"),
     """SELECT b.name, e.site_url, e.reason, e.hash_type, e.hash, u.email, e.created_at
        FROM excluded_hashes e JOIN brands b ON b.id = e.brand_id LEFT JOIN auth.users u ON u.id = e.created_by
        WHERE e.org_id = %(org)s ORDER BY b.name, e.created_at"""),
    ("rapports.csv", ("marque", "généré le", "période (jours)", "par", "rapport", "correspondances", "non trouvées", "chiffres"),
     """SELECT b.name, p.generated_at, p.within_days, u.email, 'files/reports/' || p.storage_path_html,
               'files/reports/' || p.storage_path_csv, 'files/reports/' || p.storage_path_not_found_csv, p.stats::text
        FROM reports p JOIN brands b ON b.id = p.brand_id LEFT JOIN auth.users u ON u.id = p.generated_by
        WHERE p.org_id = %(org)s ORDER BY p.generated_at"""),
    ("membres.csv", ("e-mail", "rôle", "depuis le"),
     """SELECT u.email, m.role, m.created_at FROM memberships m JOIN auth.users u ON u.id = m.user_id
        WHERE m.org_id = %(org)s ORDER BY lower(u.email)"""),
    ("journal.csv", ("date", "marque", "par", "action", "objet", "identifiant", "détail"),
     """SELECT a.created_at, b.name, u.email, a.action, a.object_type, a.object_id, a.detail::text
        FROM audit_log a LEFT JOIN brands b ON b.id = a.brand_id LEFT JOIN auth.users u ON u.id = a.user_id
        WHERE a.org_id = %(org)s ORDER BY a.created_at, a.id"""),
)

FILE_QUERIES = (
    (cloud_storage.BUCKET_REFS, "SELECT storage_path AS path FROM reference_images WHERE org_id = %(org)s ORDER BY storage_path"),
    (cloud_storage.BUCKET_REPORTS, """SELECT unnest(ARRAY[storage_path_html, storage_path_csv, storage_path_not_found_csv]) AS path
                                      FROM reports WHERE org_id = %(org)s ORDER BY generated_at"""),
)


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    return _csv_cell(value) if isinstance(value, str) else (value.isoformat() if hasattr(value, "isoformat") else value)


def _dump(conn, org_id: uuid.UUID, path: Path, header: tuple[str, ...], query: str) -> int:
    """One CSV file (UTF-8 with a BOM, so a spreadsheet reads the accents), rows streamed from a server-side cursor."""
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as handle, conn.cursor(name=f"export_{path.stem}") as cursor:
        writer = csv.writer(handle)
        writer.writerow(header)
        cursor.row_factory = tuple_row  # the query lists its columns in the order of the header
        cursor.execute(query, {"org": org_id})
        while batch := cursor.fetchmany(FETCH_BATCH):
            writer.writerows([[_cell(value) for value in row] for row in batch])
            count += len(batch)
    return count


def _copy_file(client, bucket: str, path: str, root: Path, max_file: int) -> int:
    """Download one object next to the others and return its size; 0 when the file of an earlier run is there (resume)."""
    target = (root / bucket / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise OffboardingError(f"Chemin refusé (il sort du dossier d'export) : {bucket}/{path}")
    if target.is_file() and target.stat().st_size > 0:
        return 0
    data = cloud_storage.download(client, bucket, path)
    if len(data) > max_file:
        raise OffboardingError(f"fichier de {len(data)} octets, au-delà de la limite de {max_file}")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.write_bytes(data)
    partial.replace(target)  # a run stopped half-way leaves a .part, never a truncated file under its real name
    return len(data)


def _copy_files(conn, client, org_id: uuid.UUID, root: Path, max_file: int, max_total: int) -> dict:
    stats: dict[str, Any] = {"copied": 0, "already_there": 0, "bytes": 0, "failures": []}
    for bucket, query in FILE_QUERIES:
        with conn.cursor(name=f"files_{bucket.replace('-', '_')}") as cursor:
            cursor.execute(query, {"org": org_id})
            while batch := cursor.fetchmany(FETCH_BATCH):
                for row in batch:
                    if stats["bytes"] > max_total:
                        raise OffboardingError(f"Plafond de {max_total} octets atteint : relancez avec un plafond plus haut (les fichiers déjà copiés sont gardés).")
                    try:
                        size = _copy_file(client, bucket, row["path"], root, max_file)
                    except Exception as exc:  # noqa: BLE001 - recorded in the manifest and in the exit code; the other files go on
                        log.exception("export of %s/%s failed", bucket, row["path"])
                        stats["failures"].append({"bucket": bucket, "path": row["path"], "error": str(exc)[:200]})
                        continue
                    stats["bytes"] += size
                    stats["copied" if size else "already_there"] += 1
    return stats


def export_organization(database_url: str, client, slug: str, out: Path, *, operator: str = "",
                        max_file_bytes: int = MAX_FILE_BYTES, max_total_bytes: int = MAX_TOTAL_BYTES) -> dict:
    """Write the organization's data to `out` and return the manifest (also written there as `manifeste.json`).
    A second run on the same folder finishes the job: files already there are kept. `complete` is false, and the
    command exits with an error, as soon as one file could not be copied."""
    operator = operator or getpass.getuser()
    out.mkdir(parents=True, exist_ok=True)
    with cloud_db.connect(database_url) as conn:
        org = cloud_db.get_organization_by_slug(conn, slug)
        if org is None:
            raise OffboardingError(f"Aucune organisation avec l'identifiant {slug!r}.")
        org_id = org["id"]
        # committed before anything is read, even if the export then fails; and the export is in the log it hands over
        cloud_audit.record(conn, org_id, None, "organization.export", "organization", org_id, operator=operator[:60])
    with cloud_db.connect(database_url) as conn:
        tables = {name: _dump(conn, org_id, out / name, header, query) for name, header, query in EXPORTS}
        files = _copy_files(conn, client, org_id, out / "files", max_file_bytes, max_total_bytes)
    manifest = {
        "format": 1, "nyra_version": metadata.version("nyra"), "exported_at": datetime.now(timezone.utc).isoformat(),
        "operator": operator, "organization": {"id": str(org_id), "name": org["name"], "slug": org["slug"]},
        "tables": tables, "files": files, "complete": not files["failures"],
        "note": "Les images lues sur les sites des marques ne sont pas incluses : elles se relisent sur les sites.",
    }
    (out / "manifeste.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("organization %s exported to %s: %s", org_id, os.fspath(out), json.dumps({"tables": tables, "files": files["copied"], "complete": manifest["complete"]}))
    return manifest
