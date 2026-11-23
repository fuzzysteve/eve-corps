#!/bin/sh
# Nightly dump of the evecorps database (run by evecorps-backup.service as root).
# Keeps KEEP_DAYS of custom-format dumps; restore with:
#   pg_restore --clean --if-exists -d evecorps <file>
set -eu

DIR=/home/web/backups/evecorps-db
KEEP_DAYS=14

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
