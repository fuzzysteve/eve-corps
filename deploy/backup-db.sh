#!/bin/sh
# Nightly dump of the evecorps database (run by evecorps-backup.service as root).
# Keeps KEEP_DAYS of custom-format dumps locally and copies them to OneDrive,
# where prune-remote.sh keeps a longer history; restore with:
#   pg_restore --clean --if-exists -d evecorps <file>
set -eu

DIR=/home/web/backups/evecorps-db
KEEP_DAYS=14
# Off-site copy via rclone (root's /root/.config/rclone/rclone.conf).
REMOTE=fuzzwork:server-backups/evecorps-db

umask 077
mkdir -p "$DIR"
STAMP=$(date +%Y%m%d-%H%M)
TMP="$DIR/.evecorps-$STAMP.dump.tmp"
trap 'rm -f "$TMP"' EXIT

runuser -u postgres -- pg_dump --format=custom evecorps > "$TMP"
# Prove the dump is readable before keeping it.
pg_restore --list "$TMP" > /dev/null
mv "$TMP" "$DIR/evecorps-$STAMP.dump"

find "$DIR" -maxdepth 1 -name 'evecorps-*.dump' -mtime +"$KEEP_DAYS" -delete
echo "Backup written: $DIR/evecorps-$STAMP.dump ($(du -h "$DIR/evecorps-$STAMP.dump" | cut -f1))"

# Upload-only: `copy` never deletes on the remote, and --immutable refuses to
# overwrite an existing remote file. Copying the whole directory also catches up
# any dump whose upload failed on an earlier night.
rclone copy "$DIR" "$REMOTE" --include 'evecorps-*.dump' --immutable \
    --retries 3 --low-level-retries 10 --stats-one-line --stats 0 -v
echo "Uploaded to $REMOTE"

# Only prune once tonight's dump is confirmed on the remote.
if [ "$(rclone lsf "$REMOTE/evecorps-$STAMP.dump")" = "evecorps-$STAMP.dump" ]; then
    "$(dirname "$0")/prune-remote.sh" "$REMOTE"
else
    echo "evecorps-$STAMP.dump not found on $REMOTE after upload; not pruning" >&2
    exit 1
fi
