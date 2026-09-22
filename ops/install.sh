#!/usr/bin/env bash
# Idempotent provisioning for a Mac mini. Safe to re-run.
#
# Deliberately does NOT store secrets or run destructive Postgres commands
# without confirmation. Read it before you run it.
set -euo pipefail

ROOT="/opt/agentstack"
BREW="$(command -v brew || echo /opt/homebrew/bin/brew)"
PG_VERSION="17"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

[[ "$(uname -s)" == "Darwin" ]] || { echo "macOS only; see ops/README.md for Linux" >&2; exit 1; }

say "Directories"
sudo mkdir -p "${ROOT}"/{logs,backups,ops}
sudo chown -R "$USER" "${ROOT}"

say "Homebrew packages"
"${BREW}" install "postgresql@${PG_VERSION}" age awscli tailscale || true
"${BREW}" services start "postgresql@${PG_VERSION}"
export PATH="$(${BREW} --prefix)/opt/postgresql@${PG_VERSION}/bin:${PATH}"

say "Power: stay awake, come back after an outage"
# A business box that sleeps is a business box that is down. autorestart
# brings it back after a power cut without anyone in the building.
sudo pmset -a sleep 0 disablesleep 1 autorestart 1 womp 1 hibernatemode 0
sudo pmset -a displaysleep 10

say "Automatic updates off"
# An unattended macOS update reboot is an outage, and with FileVault on it is
# an outage that lasts until someone physically unlocks the disk.
sudo softwareupdate --schedule off || true
sudo defaults write /Library/Preferences/com.apple.SoftwareUpdate \
     AutomaticallyInstallMacOSUpdates -bool false

say "Database"
createuser --createdb agentstack_owner 2>/dev/null || echo "  owner role exists"
createdb -O agentstack_owner agentstack 2>/dev/null || echo "  database exists"
psql -d agentstack -f "$(dirname "$0")/postgres/schema.sql"
psql -d agentstack -f "$(dirname "$0")/postgres/roles.sql"
psql -d agentstack -c "ALTER SYSTEM SET log_min_duration_statement = 500"
psql -d agentstack -c "SELECT pg_reload_conf()" >/dev/null

say "Python environment"
python3 -m venv "${ROOT}/venv"
"${ROOT}/venv/bin/pip" install --quiet --upgrade pip
"${ROOT}/venv/bin/pip" install --quiet -e "$(dirname "$0")/../agentstack[slack]"

say "launchd jobs"
mkdir -p "${HOME}/Library/LaunchAgents"
for plist in "$(dirname "$0")"/launchd/*.plist; do
    label="$(basename "${plist}" .plist)"
    cp "${plist}" "${HOME}/Library/LaunchAgents/"
    # bootout first so a re-run picks up an edited plist; || true because the
    # job legitimately may not be loaded yet.
    launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "${HOME}/Library/LaunchAgents/${label}.plist"
    echo "  loaded ${label}"
done

cat <<'NEXT'

==> Remaining, by hand — none of it belongs in a script

  1. Store secrets in the Keychain:
       ops/bin/secrets.sh set pg_app_password   '...'
       ops/bin/secrets.sh set pg_owner_password '...'
       ops/bin/secrets.sh set anthropic_api_key '...'
       ops/bin/secrets.sh set jev_api_key       '...'
       ops/bin/secrets.sh set slack_bot_token   '...'
       ops/bin/secrets.sh set backup_age_recipient '...'   # age public key
       ops/bin/secrets.sh set backup_age_identity  '...'   # age private key

  2. Set the app role password to match:
       psql -d agentstack -c "ALTER ROLE agentstack_app PASSWORD '...'"

  3. Enable auto-login, or the harness will not start after a reboot.
     Read the FileVault section of ops/README.md first — this is a real
     security tradeoff, not a checkbox.

  4. tailscale up --ssh

  5. ops/bin/healthcheck.sh     # must pass before this runs unattended

NEXT
