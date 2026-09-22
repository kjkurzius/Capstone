#!/usr/bin/env bash
# macOS Keychain wrapper. Secrets never live in a plist, a .env, or the repo.
#
# The Linux fallback exists because ARCHITECTURE.md commits to keeping every
# Mac-only dependency behind an interface — the day you lift this to a VM,
# you change this file and nothing else.
set -euo pipefail

SERVICE_PREFIX="agentstack"

secret_set() {
    local name="$1" value="$2"
    if command -v security >/dev/null 2>&1; then
        security add-generic-password -a "$USER" -s "${SERVICE_PREFIX}.${name}" \
            -w "$value" -U
    else
        install -m 600 /dev/null "${HOME}/.agentstack/${name}"
        printf '%s' "$value" > "${HOME}/.agentstack/${name}"
    fi
}

secret_get() {
    local name="$1"
    if command -v security >/dev/null 2>&1; then
        security find-generic-password -a "$USER" -s "${SERVICE_PREFIX}.${name}" -w
    else
        cat "${HOME}/.agentstack/${name}"
    fi
}

# Exported for the scripts that source this file.
export -f secret_set secret_get 2>/dev/null || true

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-}" in
        set) secret_set "$2" "$3" ;;
        get) secret_get "$2" ;;
        *)   echo "usage: secrets.sh {set <name> <value>|get <name>}" >&2; exit 2 ;;
    esac
fi
