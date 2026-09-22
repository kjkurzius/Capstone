#!/usr/bin/env bash
# Preflight and liveness. Run it after install, and on a timer.
#
# Exits non-zero on the first hard failure so launchd and a human read the
# same signal. Every check here corresponds to something that has taken a
# machine like this offline: a full disk, a slept box, a stale backup, a
# crash-looping job, an expired credential.
set -uo pipefail

FAIL=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=1; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; }

echo "agentstack healthcheck — $(date -u +%FT%TZ)"

# --- the box stays awake and comes back by itself ---------------------------
if command -v pmset >/dev/null 2>&1; then
    pm="$(pmset -g custom 2>/dev/null || true)"
    grep -qE '^\s*sleep\s+0' <<<"$pm" && ok "sleep disabled" || bad "the machine can sleep — sudo pmset -a sleep 0 disablesleep 1"
    grep -qE 'autorestart\s+1' <<<"$pm" && ok "restarts after power loss" || warn "autorestart off — sudo pmset -a autorestart 1"
fi

# --- disk ------------------------------------------------------------------
avail=$(df -Pk /opt/agentstack 2>/dev/null | awk 'NR==2 {print int($4/1048576)}')
if [[ -n "${avail:-}" ]]; then
    (( avail > 20 )) && ok "disk: ${avail}GB free" || bad "disk: only ${avail}GB free"
fi

# --- postgres --------------------------------------------------------------
if pg_isready -h "${PGHOST:-127.0.0.1}" -q 2>/dev/null; then
    ok "postgres accepting connections"
else
    bad "postgres is not accepting connections"
fi

# --- the append-only guarantee still holds ---------------------------------
# Worth checking every run: a well-meaning migration that re-grants ALL on the
# schema would silently turn the audit trail back into an editable table.
if command -v psql >/dev/null 2>&1; then
    priv=$(psql -h "${PGHOST:-127.0.0.1}" -U "${PGUSER:-agentstack_app}" \
           -d "${PGDATABASE:-agentstack}" -tAc \
           "SELECT has_table_privilege('agentstack_app','events','UPDATE')" 2>/dev/null || echo "?")
    case "$priv" in
        f) ok "event log is append-only" ;;
        t) bad "agentstack_app can UPDATE events — the audit trail is writable" ;;
        *) warn "could not verify the append-only grant" ;;
    esac
fi

# --- backups actually restore ----------------------------------------------
if command -v psql >/dev/null 2>&1; then
    last=$(psql -h "${PGHOST:-127.0.0.1}" -U "${PGUSER:-agentstack_app}" \
           -d "${PGDATABASE:-agentstack}" -tAc \
           "SELECT extract(epoch from now()) - ts FROM restore_tests
            WHERE ok ORDER BY ts DESC LIMIT 1" 2>/dev/null || echo "")
    if [[ -z "$last" ]]; then
        bad "no successful restore test on record — the backups are unproven"
    elif (( ${last%.*} > 864000 )); then
        bad "last successful restore test was $(( ${last%.*} / 86400 )) days ago"
    else
        ok "restore verified $(( ${last%.*} / 3600 ))h ago"
    fi
fi

# --- launchd jobs are loaded and not crash-looping --------------------------
if command -v launchctl >/dev/null 2>&1; then
    for label in harness digest backup restore-test; do
        line="$(launchctl list 2>/dev/null | grep "com.agentstack.${label}" || true)"
        if [[ -z "$line" ]]; then
            bad "com.agentstack.${label} is not loaded"
            continue
        fi
        status=$(awk '{print $2}' <<<"$line")
        [[ "$status" == "0" || "$status" == "-" ]] \
            && ok "com.agentstack.${label} loaded" \
            || bad "com.agentstack.${label} last exited ${status}"
    done
fi

# --- credentials are present (never printed) --------------------------------
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${HERE}/secrets.sh"
for name in pg_app_password anthropic_api_key jev_api_key slack_bot_token; do
    secret_get "$name" >/dev/null 2>&1 \
        && ok "secret present: ${name}" \
        || bad "secret missing: ${name}"
done

echo
(( FAIL == 0 )) && echo "all checks passed" || echo "FAILURES — do not run unattended"
exit "$FAIL"
