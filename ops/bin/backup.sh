#!/usr/bin/env bash
# Nightly logical backup: dump -> compress -> encrypt -> off-box.
#
# Encrypted before it leaves the machine, because the backup contains every
# customer record the business has ever touched and object storage is not a
# trust boundary you control.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${HERE}/secrets.sh"

: "${PGDATABASE:=agentstack}"
: "${PGHOST:=127.0.0.1}"
: "${PGUSER:=agentstack_app}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET must be set (e.g. s3://agentstack-backups)}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

DUMP="${WORK}/${PGDATABASE}-${STAMP}.dump"
AGE_RECIPIENT="$(secret_get backup_age_recipient)"

PGPASSWORD="$(secret_get pg_app_password)" \
    pg_dump --format=custom --compress=9 --no-owner --no-privileges \
            -h "${PGHOST}" -U "${PGUSER}" -d "${PGDATABASE}" -f "${DUMP}"

age --recipient "${AGE_RECIPIENT}" --output "${DUMP}.age" "${DUMP}"
rm -f "${DUMP}"

aws s3 cp "${DUMP}.age" "${BACKUP_BUCKET}/daily/" --only-show-errors

# Keep the local copy the restore test reads without a network round trip,
# and prune anything older than a week.
mkdir -p /opt/agentstack/backups
cp "${DUMP}.age" /opt/agentstack/backups/
find /opt/agentstack/backups -name '*.age' -mtime +7 -delete

echo "$(date -u +%FT%TZ) backup ok: $(basename "${DUMP}.age")"
