#!/bin/sh
# Prune old evecorps dumps from the off-site copy. Called by backup-db.sh after
# a successful upload; can be run by hand with --dry-run to see what it would do.
#
#   prune-remote.sh [--dry-run] <remote-dir>
#
# Deliberately conservative:
#  - only touches files directly in <remote-dir> named exactly
#    evecorps-YYYYMMDD-HHMM.dump; anything else there is ignored
#  - age comes from the date in the filename, not the remote modtime
#  - always keeps the newest MIN_KEEP dumps, however old (so if backups stop,
#    the last good ones are never aged out)
#  - deletes each file by its explicit path (no wildcard deletes) and at most
#    MAX_DELETE per run, so a wrong clock can't wipe out a backlog in one go
#  - refuses to run on a remote root or a path outside the backups folder
# On OneDrive, deleted files go to the recycle bin rather than being purged.
set -eu

KEEP_DAYS=${REMOTE_KEEP_DAYS:-90}
MIN_KEEP=${REMOTE_MIN_KEEP:-30}
MAX_DELETE=${REMOTE_MAX_DELETE:-7}

DRY=
if [ "${1:-}" = "--dry-run" ]; then DRY=1; shift; fi
REMOTE=${1:?usage: prune-remote.sh [--dry-run] <remote-dir>}

case "$REMOTE" in
    *:*/evecorps-db|/*/evecorps-db) ;;
    *) echo "prune-remote: refusing unexpected path '$REMOTE'" >&2; exit 1 ;;
esac

CUTOFF=$(date -u -d "-$KEEP_DAYS days" +%Y%m%d)

# Lexical order of these names is chronological order.
ALL=$(rclone lsf "$REMOTE" --files-only --max-depth 1 \
        | grep -E '^evecorps-[0-9]{8}-[0-9]{4}\.dump$' | sort || true)
COUNT=$(printf '%s\n' "$ALL" | grep -c . || true)

if [ "$COUNT" -le "$MIN_KEEP" ]; then
    echo "prune-remote: $COUNT dumps on remote, keeping all (minimum $MIN_KEEP)"
    exit 0
fi

# Everything except the newest MIN_KEEP, then only those older than the cutoff.
VICTIMS=$(printf '%s\n' "$ALL" | head -n "$((COUNT - MIN_KEEP))" \
            | awk -v c="$CUTOFF" 'substr($0, 10, 8) < c' | head -n "$MAX_DELETE")

if [ -z "$VICTIMS" ]; then
    echo "prune-remote: nothing older than $KEEP_DAYS days ($COUNT dumps on remote)"
    exit 0
fi

for f in $VICTIMS; do
    if [ -n "$DRY" ]; then
        echo "prune-remote: would delete $REMOTE/$f"
    else
        rclone deletefile "$REMOTE/$f"
        echo "prune-remote: deleted $REMOTE/$f"
    fi
done
