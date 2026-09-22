"""Slack delivery and reply handling.

This is the authorization channel for actions with money attached, so it is
written as a security boundary first and a UX second.

Project Vend's phase one failed here: Claudius was talked out of its entire
inventory by people in a Slack channel. The lesson is not "be more skeptical"
— it is that the approval path must not be a conversation. Nothing in this
module asks a model to interpret intent. A reply either carries a valid
signature from the one authorized user and names a known escalation, or it
does nothing at all.

Four gates, in order, before a single escalation resolves:

  1. HMAC signature over the raw body (constant-time)
  2. Timestamp inside the replay window
  3. Sender is the configured operator — not merely someone in the channel
  4. The escalation exists and is still open
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from .escalation import Escalation, EscalationError, Klass, Queue

SLACK_API = "https://slack.com/api"

# Slack's documented replay window. Anything older is refused outright.
REPLAY_WINDOW_SECONDS = 60 * 5


class AuthError(RuntimeError):
    """Rejected at the boundary. Never leaks why, to the caller."""


@dataclass(frozen=True)
class SlackConfig:
    bot_token: str
    signing_secret: str
    channel: str                    # a PRIVATE channel or DM — see redaction below
    operator_user_id: str           # the ONLY user whose replies count

    @classmethod
    def from_env(cls) -> SlackConfig:
        return cls(
            bot_token=os.environ["SLACK_BOT_TOKEN"],
            signing_secret=os.environ["SLACK_SIGNING_SECRET"],
            channel=os.environ["SLACK_CHANNEL"],
            operator_user_id=os.environ["SLACK_OPERATOR_USER_ID"],
        )


# --- gate 1 and 2: the request is really from Slack, and is not a replay -----

def verify_signature(
    signing_secret: str,
    timestamp: str,
    raw_body: bytes,
    signature: str,
    *,
    now: float | None = None,
) -> None:
    """Raise AuthError unless this request is authentically Slack's.

    `raw_body` must be the bytes exactly as received. Re-serializing parsed
    JSON changes the bytes and the signature will not match — which is the
    usual way this check gets silently disabled in practice.
    """
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        raise AuthError("bad timestamp") from None

    if abs((now or time.time()) - ts) > REPLAY_WINDOW_SECONDS:
        raise AuthError("timestamp outside replay window")

    basestring = b"v0:" + timestamp.encode() + b":" + raw_body
    expected = "v0=" + hmac.new(
        signing_secret.encode(), basestring, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature or ""):
        raise AuthError("signature mismatch")


# --- rendering ---------------------------------------------------------------

def _redact(evidence: dict[str, Any], keys: frozenset[str]) -> dict[str, Any]:
    return {k: ("[redacted]" if k in keys else v) for k, v in evidence.items()}


def blocks_for(esc: Escalation, *, redact: frozenset[str] = frozenset()) -> list[dict]:
    """Block Kit for one escalation.

    Buttons rather than parsed text: a button carries an exact escalation id
    and an exact choice, so there is nothing to interpret and nothing to
    misread. Free text is accepted too (see `parse_text_reply`) for replying
    from a phone, but it is the fallback, not the path.
    """
    ev = _redact(esc.evidence, redact)
    body = "\n".join(f"• *{k}:* {v}" for k, v in ev.items())

    header = f"*[{esc.klass.upper()}]*  {esc.summary}"
    if esc.agent:
        header += f"\n_{esc.agent}_ · `{esc.task_id[:8]}`"

    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
    ]
    if body:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": body}})

    if esc.klass is Klass.COMMITMENT:
        blocks.append({"type": "context", "elements": [{
            "type": "mrkdwn",
            "text": ":lock: *Binds the company. No default, no timeout — "
                    "this waits for you indefinitely.*",
        }]})
        actions = [("Approve", "alternative", "danger"), ("Decline", "proposed", None)]
    else:
        hours = (esc.deadline - esc.raised_at) / 3600 if esc.deadline else 0
        blocks.append({"type": "context", "elements": [{
            "type": "mrkdwn",
            "text": f":clock3: *{esc.proposed}* automatically in {hours:.0f}h "
                    f"unless you choose otherwise.",
        }]})
        actions = [(esc.proposed, "proposed", None),
                   (esc.alternative, "alternative", "primary")]

    blocks.append({
        "type": "actions",
        "block_id": f"esc:{esc.id}",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": label[:75]},
                # The id travels in the value, so a stale message still
                # resolves the right item — and only that item.
                "value": f"{esc.id}:{choice}",
                "action_id": f"esc_{choice}",
                **({"style": style} if style else {}),
            }
            for label, choice, style in actions
        ],
    })
    return blocks


# --- delivery ----------------------------------------------------------------

@dataclass
class SlackTransport:
    config: SlackConfig
    redact: frozenset[str] = frozenset()
    _http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=10.0))

    def _post(self, method: str, payload: dict) -> dict:
        r = self._http.post(
            f"{SLACK_API}/{method}",
            headers={"Authorization": f"Bearer {self.config.bot_token}",
                     "Content-Type": "application/json; charset=utf-8"},
            json=payload,
        )
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"slack {method} failed: {body.get('error')}")
        return body

    def deliver(self, esc: Escalation) -> str:
        """Post one escalation. Returns the message ts."""
        body = self._post("chat.postMessage", {
            "channel": self.config.channel,
            "text": f"[{esc.klass.upper()}] {esc.summary}",   # notification fallback
            "blocks": blocks_for(esc, redact=self.redact),
        })
        return body["ts"]

    def say(self, text: str, thread_ts: str | None = None) -> None:
        payload = {"channel": self.config.channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        self._post("chat.postMessage", payload)

    def close(self) -> None:
        self._http.close()


# --- gate 3 and 4: the right person, resolving a real open item --------------

def parse_text_reply(text: str) -> tuple[str, str] | None:
    """Parse `<id> alt|ok` from a free-text reply, or return None.

    Deliberately rigid. An unparseable reply is ignored rather than guessed
    at, because guessing is how an approval channel becomes a conversation.
    """
    parts = text.strip().split()
    if len(parts) != 2:
        return None
    esc_id, word = parts[0].strip("`"), parts[1].lower()
    choice = {"alt": "alternative", "alternative": "alternative",
              "ok": "proposed", "yes": "proposed", "default": "proposed"}.get(word)
    return (esc_id, choice) if choice else None


@dataclass
class ReplyHandler:
    """Framework-agnostic. Hand it raw headers and the raw body."""

    config: SlackConfig
    queue: Queue
    on_resolved: Callable[[Escalation, str], None] = lambda e, c: None

    def handle(self, headers: dict[str, str], raw_body: bytes,
               *, now: float | None = None) -> dict:
        lower = {k.lower(): v for k, v in headers.items()}

        verify_signature(
            self.config.signing_secret,
            lower.get("x-slack-request-timestamp", ""),
            raw_body,
            lower.get("x-slack-signature", ""),
            now=now,
        )

        payload = _payload(raw_body)

        # Slack retries on any non-2xx or on a response slower than 3s, so the
        # same click can arrive several times. Retries are acknowledged and
        # dropped rather than replayed.
        if lower.get("x-slack-retry-num"):
            return {"text": "ok"}

        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}

        user_id, action = _extract(payload)

        # No parsed action means this is not an authorization attempt at all —
        # the app's own digest, ordinary channel chatter, an unparseable reply.
        # Ignore it quietly rather than raising, or every message the app posts
        # would 500 and Slack would retry it.
        if action is None:
            if user_id == self.config.operator_user_id:
                return {"text": "Reply `<id> alt` or `<id> ok`, or use the buttons."}
            return {"text": "ok"}

        # Gate 3, and only now that something is actually trying to resolve.
        # Being in the channel is not authorization — the bot, and anyone else
        # invited, are both in the channel.
        if user_id != self.config.operator_user_id:
            raise AuthError("sender is not the operator")

        esc_id, choice = action
        open_by_id = {e.id: e for e in self.queue.open_items()}
        esc = open_by_id.get(esc_id)
        if esc is None:
            # Gate 4. Covers a double click, a stale message, and a typo
            # identically — and never 500s, which would trigger more retries.
            return {"text": f"`{esc_id}` is unknown or already resolved."}

        try:
            self.queue.resolve(esc_id, choice, note=f"slack:{user_id}")
        except EscalationError:
            return {"text": f"`{esc_id}` was already resolved."}

        self.on_resolved(esc, choice)
        taken = esc.proposed if choice == "proposed" else esc.alternative
        return {"text": f":white_check_mark: `{esc_id}` — {taken}"}


def _payload(raw_body: bytes) -> dict:
    """Interactivity arrives form-encoded; Events API arrives as JSON."""
    text = raw_body.decode("utf-8", "replace")
    if text.startswith("payload="):
        return json.loads(urllib.parse.parse_qs(text)["payload"][0])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        form = urllib.parse.parse_qs(text)
        return {k: v[0] for k, v in form.items()}


def _extract(payload: dict) -> tuple[str, tuple[str, str] | None]:
    """Pull (user_id, (escalation_id, choice)) from either payload shape."""
    if payload.get("type") == "block_actions":
        user = payload.get("user", {}).get("id", "")
        actions = payload.get("actions") or [{}]
        value = actions[0].get("value", "")
        esc_id, _, choice = value.partition(":")
        if choice in ("proposed", "alternative"):
            return user, (esc_id, choice)
        return user, None

    event = payload.get("event", {})
    # bot_id set means the app is hearing its own message. Ignoring this is
    # what stops a digest from being read back as a reply to itself.
    if event.get("bot_id") or event.get("subtype"):
        return "", None
    return event.get("user", ""), parse_text_reply(event.get("text", ""))


# --- scheduling --------------------------------------------------------------

@dataclass
class Notifier:
    """Batches, except where batching would be wrong.

    COMMITMENT and money-bearing THRESHOLD items go out the moment they are
    raised. Everything else waits for the next digest, because an operator
    interrupted twenty times a day stops reading and starts clicking.
    """

    transport: SlackTransport
    queue: Queue

    def flush_interrupts(self) -> int:
        """Call right after raising. Sends only what cannot wait."""
        from .escalation import INTERRUPTS

        sent = 0
        for esc in self.queue.unnotified():
            if esc.klass in INTERRUPTS:
                self.transport.deliver(esc)
                self.queue.mark_notified(esc.id)
                sent += 1
        return sent

    def send_digest(self) -> int:
        """Call on a schedule — twice a day is the intended cadence."""
        pending = self.queue.unnotified()
        over = self.queue.over_budget()

        if over:
            # The backlog is the symptom. Say so, rather than presenting a
            # long list as though working through it were the task.
            self.transport.say(
                f":rotating_light: *{over} escalations over the daily budget.* "
                f"The system is producing too many decisions — that is the "
                f"problem to fix, not the queue."
            )
        if not pending:
            return 0

        self.transport.say(f"*Escalation digest — {len(pending)} open*")
        for esc in pending:
            self.transport.deliver(esc)
            self.queue.mark_notified(esc.id)
        return len(pending)

    def apply_expired(self, execute: Callable[[Escalation], None]) -> list[Escalation]:
        """Resolve everything past its deadline and run its default.

        COMMITMENT is structurally excluded — it carries no deadline, so it
        can never appear here regardless of how long it has been waiting.
        """
        fired = self.queue.sweep()
        for esc in fired:
            execute(esc)
            self.transport.say(
                f":hourglass: `{esc.id}` timed out — applied default: {esc.proposed}"
            )
        return fired
