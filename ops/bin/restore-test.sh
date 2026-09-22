#!/usr/bin/env bash
# Restore the newest backup into a scratch database and prove it is readable.
#
# This is the job that distinguishes a backup from a hope. It restores for
# real, queries the restored tables, and records the result so a stale or
# failing verification becomes an escalation rather than a surprise.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${HERE}/secrets.sh"

: "${PGHOST:=127.0.0.1}"
: "${PGDATABASE:=agentstack}"
SCRATCH="agentstack_restore_test"
WORK="$(mktemp -d)"
OK=false
ROWS=0
DETAIL=""

cleanup() {
    rm -rf "${WORK}"
    PGPASSWORD="$(secret_get pg_owner_password)" \
        psql -h "${PGHOST}" -U agentstack_owner -d postgres -q \
             -c "DROP DATABASE IF EXISTS ${SCRATCH}" >/dev/null 2>&1 || true

    # Always record the attempt, including a failed one — silence here is
    # indistinguishable from "never ran", which is the failure we are guarding
    # against in the first place.
    PGPASSWORD="$(secret_get pg_app_password)" \
        psql -h "${PGHOST}" -U agentstack_app -d "${PGDATABASE}" -q -c \
        "INSERT INTO restore_tests (ts, ok, rows_seen, detail)
         VALUES (extract(epoch from now()), ${OK}, ${ROWS},
                 \$\$${DETAIL}\$\$)" >/dev/null 2>&1 || true
}
trap cleanup EXIT

LATEST="$(find /opt/agentstack/backups -name '*.age' -type f \
          -exec ls -t {} + 2>/dev/null | head -1)"
if [[ -z "${LATEST}" ]]; then
    DETAIL="no backup file found"
    echo "FAIL: ${DETAIL}" >&2
    exit 1
fi

AGE_AT="$(date -u -r "${LATEST}" +%s 2>/dev/null || stat -c %Y "${LATEST}")"
if (( $(date -u +%s) - AGE_AT > 172800 )); then
    DETAIL="newest backup is older than 48h"
    echo "FAIL: ${DETAIL}" >&2
    exit 1
fi

age --decrypt --identity <(secret_get backup_age_identity) \
    --output "${WORK}/restore.dump" "${LATEST}"

export PGPASSWORD
PGPASSWORD="$(secret_get pg_owner_password)"
psql -h "${PGHOST}" -U agentstack_owner -d postgres -q \
     -c "DROP DATABASE IF EXISTS ${SCRATCH}" -c "CREATE DATABASE ${SCRATCH}"
pg_restore -h "${PGHOST}" -U agentstack_owner -d "${SCRATCH}" \
           --no-owner --no-privileges "${WORK}/restore.dump"

# Restoring without error is not enough — read the data back.
ROWS="$(psql -h "${PGHOST}" -U agentstack_owner -d "${SCRATCH}" -tAc \
        "SELECT count(*) FROM events")"
DECISIONS="$(psql -h "${PGHOST}" -U agentstack_owner -d "${SCRATCH}" -tAc \
        "SELECT count(*) FROM decisions")"

if (( ROWS == 0 )); then
    DETAIL="restored cleanly but the event log is empty"
    echo "FAIL: ${DETAIL}" >&2
    exit 1
fi

OK=true
DETAIL="restored ${ROWS} events, ${DECISIONS} decisions from $(basename "${LATEST}")"
echo "$(date -u +%FT%TZ) restore test ok: ${DETAIL}"
