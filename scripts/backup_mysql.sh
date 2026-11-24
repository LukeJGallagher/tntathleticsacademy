#!/usr/bin/env bash
# Nightly MySQL backup for the Hostinger VPS. Keeps 30 days locally; optionally copies off-server with rclone.
#
# Setup (once):
#   1. Create ~/.my.cnf (chmod 600) so the password isn't on the command line:
#        [mysqldump]
#        user=DBUSER
#        password=DBPASS
#        host=localhost
#   2. chmod +x scripts/backup_mysql.sh
#   3. crontab -e  →  15 2 * * * /home/rollcall/app/scripts/backup_mysql.sh DBNAME >> /home/rollcall/backup.log 2>&1
#   4. Optional off-server copy: `rclone config` a remote (e.g. gdrive, scope drive.file) and set RCLONE_REMOTE below.
set -euo pipefail

DB="${1:?usage: backup_mysql.sh DBNAME}"
DIR="${BACKUP_DIR:-$HOME/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
RCLONE_REMOTE="${RCLONE_REMOTE:-}"          # e.g. "gdrive:rollcall-backups"

mkdir -p "$DIR"
chmod 700 "$DIR"
FILE="$DIR/${DB}_$(date +%Y%m%d_%H%M).sql.gz"

mysqldump --single-transaction --routines --no-tablespaces "$DB" | gzip -9 > "$FILE"
chmod 600 "$FILE"

# sanity check: a real dump is more than a few hundred bytes
if [ "$(stat -c%s "$FILE")" -lt 500 ]; then
  echo "$(date -Is) backup looks empty: $FILE" >&2
  exit 1
fi

find "$DIR" -name "${DB}_*.sql.gz" -mtime +"$KEEP_DAYS" -delete

if [ -n "$RCLONE_REMOTE" ]; then
  rclone copy "$FILE" "$RCLONE_REMOTE" --retries 3 --retries-sleep 30s
fi

echo "$(date -Is) ok $FILE"

# Restore:  gunzip -c FILE.sql.gz | mysql DBNAME
