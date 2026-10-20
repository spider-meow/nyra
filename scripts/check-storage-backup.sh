#!/usr/bin/env bash
# Compare the Supabase Storage buckets with their copy (docs/DEPLOYMENT.md, "Backups").
# For each bucket: number of objects and bytes at the source and in the copy, then the objects the copy lacks or
# holds with another size. Exit 1 if more than MAX_MISSING objects differ (default 50: what was uploaded since the
# last copy), so a timer or a monitor can run it. Needs rclone with the remotes SOURCE and COPY configured.
#
#   SOURCE=supabase COPY=scw-crypt MAX_MISSING=50 scripts/check-storage-backup.sh
#
# Not run in the development environment (no Supabase Storage, no rclone there): first run is the team's.
set -euo pipefail

SOURCE="${SOURCE:-supabase}"
COPY="${COPY:-scw-crypt}"
MAX_MISSING="${MAX_MISSING:-50}"
status=0

for bucket in refs site-images reports; do
  src=$(rclone size --json "$SOURCE:$bucket")
  dst=$(rclone size --json "$COPY:$bucket/current")
  echo "$bucket  source: $src  copy: $dst"
  # --one-way: only what the source has and the copy does not (or with another size); extra objects in the copy are normal.
  # rclone check exits 1 both when it finds differences and when it fails: told apart by the differences it lists.
  rc=0
  report=$(rclone check "$SOURCE:$bucket" "$COPY:$bucket/current" --size-only --one-way --combined -) || rc=$?
  missing=$(printf '%s\n' "$report" | grep -c '^[-*!] ' || true)
  if [ "$rc" -ge 2 ] || { [ "$rc" -eq 1 ] && [ "$missing" -eq 0 ]; }; then
    echo "FAIL: rclone check failed on $bucket (exit $rc), the copy is not verified" >&2
    status=1
    continue
  fi
  echo "$bucket  differing objects: $missing (limit $MAX_MISSING)"
  if [ "$missing" -gt "$MAX_MISSING" ]; then
    echo "FAIL: $bucket is not backed up (rclone check --size-only --one-way $SOURCE:$bucket $COPY:$bucket/current)" >&2
    status=1
  fi
done
exit "$status"
