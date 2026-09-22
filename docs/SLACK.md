# Slack setup

The escalation channel authorizes actions with money attached. Treat it as a
security boundary, not a notification feed.

## Why this is the risky component

Project Vend's phase one failed here. Claudius lost its entire float because
people in a Slack channel talked it into an "Ultra-Capitalist Free-for-All."
The lesson is not that the model should have been more skeptical — it is that
**an approval path must not be a conversation.**

Nothing in `slack.py` asks a model to interpret intent. A reply either carries
a valid signature from the one authorized user and names a known open
escalation, or nothing happens. Free-text replies are parsed by a rigid
grammar that returns `None` rather than guessing.

## The four gates

Every inbound request passes all four before anything resolves:

1. **HMAC signature** over the raw request bytes, compared in constant time.
2. **Timestamp** within Slack's 5-minute replay window.
3. **Sender is the operator.** Being in the channel is not authorization — the
   bot is in the channel too, and so is anyone you ever invite.
4. **The escalation exists and is open.** Double clicks, stale messages and
   typos all resolve to the same harmless answer.

## Deployment on a Mac mini: use Socket Mode

The box is behind NAT, so do **not** expose a webhook endpoint through a
tunnel. Socket Mode opens an outbound WebSocket: no inbound port, no public
URL, no certificate to manage, nothing to find in a scan.

`ReplyHandler.handle()` is framework-agnostic — hand it the headers and the
raw body from either transport. With Socket Mode the envelope is already
authenticated by the socket, but run the handler anyway: gates 3 and 4 are the
ones that matter, and keeping one code path means one thing to audit.

```bash
pip install slack_sdk       # Socket Mode client
```

## Creating the app

Manifest, scopes trimmed to what the handler actually uses:

```yaml
display_information:
  name: agentstack
features:
  bot_user:
    display_name: agentstack
settings:
  socket_mode_enabled: true
  interactivity:
    is_enabled: true
oauth_config:
  scopes:
    bot:
      - chat:write          # post digests and escalations
      - im:history          # read replies in the DM
```

Then:

```bash
export SLACK_BOT_TOKEN=xoxb-...          # OAuth & Permissions
export SLACK_SIGNING_SECRET=...          # Basic Information
export SLACK_APP_TOKEN=xapp-...          # Socket Mode, connections:write
export SLACK_CHANNEL=D...                # your DM with the bot, not a channel
export SLACK_OPERATOR_USER_ID=U...       # your user id — the only one that counts
```

Keep these in the macOS Keychain in production, not in a file.

## Use a DM, not a channel

`SLACK_CHANNEL` should be your direct message with the bot. Two reasons:

- **Blast radius.** A channel gains members over time. Every one of them can
  see customer data in escalation evidence and can attempt a reply.
- **Slack is a subprocessor.** Anything you put in a message is data you have
  shared with Salesforce. Use `SlackTransport(redact=...)` to strip PII from
  evidence fields, and put identifiers in escalations rather than values:

```python
transport = SlackTransport(config, redact=frozenset({"customer_email", "preview"}))
```

If you are ever handling PHI, note that this path is not covered by a BAA. Send
a record id and look it up yourself.

## Wiring it up

```python
notifier = Notifier(transport, queue)

# right after raising — sends only COMMITMENT and money-bearing THRESHOLD
notifier.flush_interrupts()

# on a schedule, twice a day
notifier.send_digest()

# on a timer — applies defaults for anything past its deadline.
# COMMITMENT can never appear here; it carries no deadline.
notifier.apply_expired(execute=my_executor)
```

Run all three from one supervised process under `launchd` with `KeepAlive`.

## Operating it

- **Two digests a day.** Not more. An operator interrupted twenty times a day
  stops reading and starts clicking, and a rubber-stamped queue is worse than
  no queue because it manufactures the appearance of oversight.
- **Watch `queue.over_budget()`.** Exceeding ten a day means the *system* is
  broken. The digest says so rather than presenting the backlog as a to-do
  list.
- **Watch `queue.is_learning()`.** NOVEL, LOW_CONFIDENCE and CONFLICT should
  shrink month over month as answers become policy. THRESHOLD and COMMITMENT
  are permanent.
- **Never widen the operator list to move faster.** If you need a second
  approver, add a second gate — not a second identity on the same gate.
