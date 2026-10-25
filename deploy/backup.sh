#!/usr/bin/env bash
# Daily online backup of the SQLite DB into /srv/call-analyzer/backups/calls-YYYYMMDD.db,
# keeping the newest 14. Audio is not included.
#
# sqlite3 ".backup" runs inside the app container, as the app user, so the copy is
# consistent while the app keeps writing and file ownership stays right.
# Installed as a cron job by deploy/backup.cron (03:30 UTC).
set -euo pipefail
cd "$(dirname "$0")/.."

KEEP=14
name="calls-$(date -u +%Y%m%d).db"

docker compose -p call-analyzer -f deploy/docker-compose.yml exec -T app sh -c "
  set -e
  umask 077
  sqlite3 /app/data/call-bench.db \".backup '/app/backups/$name'\"
  test \"\$(sqlite3 '/app/backups/$name' 'PRAGMA integrity_check')\" = ok
  ls -1t /app/backups/calls-*.db | tail -n +$((KEEP + 1)) | xargs -r rm --
"
echo "Backup OK: /srv/call-analyzer/backups/$name"
