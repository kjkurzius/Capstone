# Operations runbook

Provisioning a Mac mini to run the fleet unattended.

```bash
./ops/install.sh          # idempotent, safe to re-run
./ops/bin/healthcheck.sh  # must pass before anything runs unattended
```

---

## The decision that matters most: LaunchAgent vs LaunchDaemon

This trips everyone, and getting it wrong means the box comes back from a
reboot with nothing running.

| | LaunchAgent | LaunchDaemon |
|---|---|---|
| Location | `~/Library/LaunchAgents` | `/Library/LaunchDaemons` |
| Runs at | user login | boot, as root |
| Login keychain | **yes** | **no** |
| Survives reboot with nobody logged in | **no** | yes |

You cannot have both. A LaunchDaemon starts at boot but has no access to your
login keychain, so every secret would have to live in a root-readable file. A
LaunchAgent can read the keychain but does not start until someone logs in.

**Use a LaunchAgent and enable auto-login.** On a headless box that already
holds your business's credentials, the marginal physical security of a login
prompt is worth less than keeping secrets in the Keychain and out of a file on
disk. This is what `install.sh` sets up.

### The FileVault tradeoff — decide this deliberately

**FileVault and auto-login are mutually exclusive after a reboot.** With
FileVault on, the disk must be unlocked before macOS reaches the login window,
so the machine will sit at the unlock prompt with nothing running until
somebody is physically present.

- **FileVault on** — your data is safe if the machine is stolen, and an
  unplanned reboot is an outage lasting until you get home.
- **FileVault off** — the box recovers by itself from a power cut, and anyone
  who walks off with it has your customer data, your keys and your books.

For a business holding customer data, **leave FileVault on** and accept the
recovery constraint. Mitigate it:

```bash
sudo fdesetup authrestart      # reboot once with the disk pre-authorized
```

Use that for planned reboots. For unplanned ones, the honest answer is that a
single machine in your house has a recovery time measured in however long it
takes you to get to it — which is the argument in `ARCHITECTURE.md` for keeping
everything portable to a Linux VM.

---

## What Postgres enforces that the code cannot

Two guarantees live in `postgres/schema.sql` and `postgres/roles.sql` rather
than in Python, because a guarantee a future code path can forget is not a
guarantee.

**The event log is append-only.** `agentstack_app` holds `SELECT, INSERT` on
`events` and nothing else — no UPDATE, no DELETE, no TRUNCATE. Correcting the
record means appending a correcting event. An audit trail the application can
rewrite is not an audit trail, and this is the artifact your insurer and your
customers' security reviews will ask for.

`healthcheck.sh` re-verifies this on every run, because a well-meaning
migration that re-grants `ALL ON SCHEMA public` would silently turn the audit
trail back into an editable table.

**The COMMITMENT invariant is a CHECK constraint.** A row that would execute on
silence cannot be stored:

```sql
CONSTRAINT commitment_has_no_default
    CHECK (klass <> 'commitment' OR proposed = ''),
CONSTRAINT commitment_never_times_out
    CHECK (klass <> 'commitment' OR resolution IS DISTINCT FROM 'timeout')
```

The constructor in `escalation.py` enforces the same rule. Defense in depth on
the one control that governs actions binding the company.

---

## Backups: the restore is the thing

`backup.sh` runs nightly at 03:00 — `pg_dump` → compress → `age`-encrypt →
S3, encrypted *before* it leaves the machine. Object storage is not a trust
boundary you control, and the dump contains every customer record the business
has ever touched.

`restore-test.sh` runs weekly and is the job that earns its keep. It restores
the newest backup into a scratch database **for real**, then reads the data
back and counts rows. Restoring without error is not enough — a dump can
restore cleanly and be empty.

Every attempt writes to the `restore_tests` table, including failures, because
silence is indistinguishable from "never ran". `healthcheck.sh` fails hard if
the last successful restore is more than ten days old, which turns an unproven
backup into a visible problem instead of a discovery you make on your worst day.

**Do this once, by hand, before you trust any of it:** restore to a scratch
database yourself and query it. The scripts automate a procedure you have
personally verified, not one you hope works.

---

## Jobs

| Label | Schedule | Purpose |
|---|---|---|
| `com.agentstack.harness` | always | The control loop. `KeepAlive` on crash or non-zero exit. |
| `com.agentstack.digest` | 08:30, 16:30 | Escalation digests. Twice daily, deliberately. |
| `com.agentstack.backup` | 03:00 daily | Dump, encrypt, ship off-box. |
| `com.agentstack.restore-test` | Sunday 05:00 | Prove the backups restore. |

The harness restarts on a crash but **not** on a clean `exit(0)` — that is the
deliberate-shutdown path, and without it the only way to stop the harness is to
unload the job. `ThrottleInterval` is 30s so a startup bug becomes a visible
slow loop rather than a CPU fire.

```bash
launchctl list | grep agentstack                      # status
launchctl kickstart -k gui/$(id -u)/com.agentstack.harness   # restart
launchctl bootout gui/$(id -u)/com.agentstack.harness        # stop
tail -f /opt/agentstack/logs/harness.err.log
```

---

## Power and updates

```bash
sudo pmset -a sleep 0 disablesleep 1 autorestart 1 womp 1 hibernatemode 0
sudo softwareupdate --schedule off
```

`autorestart 1` brings the machine back after a power cut with nobody in the
building. Automatic updates are off because an unattended update reboot is an
outage, and with FileVault on it is an outage that lasts until you are
physically there. **Patch on a schedule you choose** — put it in the calendar
rather than leaving it to Apple.

---

## Secrets

Keychain only. Never a plist (world-readable), never `.env`, never the repo.

```bash
ops/bin/secrets.sh set anthropic_api_key '...'
ops/bin/secrets.sh get anthropic_api_key
```

`secrets.sh` has a file-backed Linux fallback at `~/.agentstack/` with `0600`
perms, so the day you lift this to a VM you change one file. That is the
`ARCHITECTURE.md` rule about keeping Mac-only dependencies off the critical
path, honored rather than asserted.

---

## Access

`tailscale up --ssh`. No port forwarding, no public IP, nothing exposed to a
scan. The Slack integration uses Socket Mode for the same reason — see
`docs/SLACK.md`.

---

## Moving to Linux

`ARCHITECTURE.md` commits to the Mac mini being *where this runs today*, not
*what it depends on*. The whole Mac surface is four things:

| Mac | Linux |
|---|---|
| `launchd` plists | systemd units (`Restart=on-failure`, `RestartSec=30`) |
| Keychain via `security` | the fallback already in `secrets.sh`, or a secrets manager |
| `pmset` | not applicable |
| Homebrew Postgres | distribution packages — schema and roles are unchanged |

**Rehearse this once, early, while it is cheap.** The restore test already
proves the data moves; the rest is four files.
