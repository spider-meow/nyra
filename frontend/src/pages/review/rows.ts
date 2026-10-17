import type { Hit, MatchGroup } from "../../types";

/** A reference and what it matched. */
export type Pair = { group: MatchGroup; hit: Hit };
/** One photo found on the site: the reference it is shown with, and the other references it also matches (crops of one visual, say). */
export type Row = Pair & { key: string; others: Pair[] };

/** The same row seen from another of its references: that pair first, the rest as `others`. */
export function focusOn(row: Row, referenceId: string | null): Row {
  const pairs: Pair[] = [{ group: row.group, hit: row.hit }, ...row.others];
  const chosen = pairs.find((pair) => pair.group.reference_id === referenceId) ?? pairs[0];
  return { key: row.key, ...chosen, others: pairs.filter((pair) => pair !== chosen) };
}

/** One row per photo found on the site: several references can match one photo, they share its row. */
export function photoRows(groups: MatchGroup[], wanted: (hit: Hit) => boolean): Row[] {
  const byPhoto = new Map<string, Row>();
  for (const group of groups) {
    for (const hit of group.hits) {
      if (!wanted(hit)) continue;
      // The key of a photo is the same whichever reference found it.
      const photo = hit.photo_key;
      const row = byPhoto.get(photo);
      if (!row) byPhoto.set(photo, { key: photo, group, hit, others: [] });
      else if (row.group.reference_id !== group.reference_id && !row.others.some((pair) => pair.group.reference_id === group.reference_id)) row.others.push({ group, hit });
    }
  }
  return [...byPhoto.values()];
}

/** How far a visual is: its photos found online that are done (removed), out of those not set aside as false positives. */
export type Progress = { done: number; total: number };

/** The progress of each reference, whatever the decision filter hides. */
export function progressByReference(groups: MatchGroup[]): Map<string, Progress> {
  return new Map(groups.map((group) => {
    const counted = group.hits.filter((hit) => hit.decision !== "ecarte");
    return [group.reference_id, { done: counted.filter((hit) => hit.decision === "traite").length, total: counted.length }];
  }));
}
