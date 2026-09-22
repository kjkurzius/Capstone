"""Slack boundary tests.

These are the ones that matter: this channel authorizes actions with money
attached, so each test here is an attack that must fail closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentstack.escalation import Escalation, Klass, Queue
from agentstack.slack import (
    AuthError, ReplyHandler, SlackConfig, blocks_for, parse_text_reply,
    verify_signature,
)

SECRET = "s3cr3t"
OPERATOR = "U_KARL"
INTRUDER = "U_SOMEONE_ELSE"

CONFIG = SlackConfig(bot_token="xoxb-x", signing_secret=SECRET,
                     channel="C1", operator_user_id=OPERATOR)


def sign(body: bytes, ts: int) -> dict[str, str]:
    base = b"v0:" + str(ts).encode() + b":" + body
    sig = "v0=" + hmac.new(SECRET.encode(), base, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": str(ts), "X-Slack-Signature": sig}


def click(esc_id: str, choice: str, user: str = OPERATOR) -> bytes:
    payload = {"type": "block_actions", "user": {"id": user},
               "actions": [{"value": f"{esc_id}:{choice}"}]}
    return b"payload=" + urllib.parse.quote(json.dumps(payload)).encode()


def esc(klass=Klass.LOW_CONFIDENCE, **kw) -> Escalation:
    base = dict(task_id="t1", klass=klass, summary="refund $240",
                proposed="hold", alternative="issue the refund")
    if klass is Klass.COMMITMENT:
        base["proposed"] = ""
    return Escalation(**{**base, **kw})


@pytest.fixture
def setup(tmp_path):
    q = Queue(tmp_path / "e.db")
    e = q.raise_(esc())
    return q, e, ReplyHandler(CONFIG, q)


# --- gate 1 & 2: authenticity ------------------------------------------

def test_valid_signature_passes():
    body = b'{"a":1}'
    ts = int(time.time())
    verify_signature(SECRET, str(ts), body, sign(body, ts)["X-Slack-Signature"])


def test_forged_signature_rejected():
    body = b'{"a":1}'
    ts = int(time.time())
    with pytest.raises(AuthError, match="signature"):
        verify_signature(SECRET, str(ts), body, "v0=deadbeef")


def test_signature_is_bound_to_the_exact_body():
    # Re-serializing parsed JSON is the usual way this check gets disabled.
    ts = int(time.time())
    headers = sign(b'{"a":1}', ts)
    with pytest.raises(AuthError, match="signature"):
        verify_signature(SECRET, str(ts), b'{"a": 1}',
                         headers["X-Slack-Signature"])


def test_replayed_request_rejected():
    body = b'{"a":1}'
    old = int(time.time()) - 3600
    with pytest.raises(AuthError, match="replay"):
        verify_signature(SECRET, str(old), body,
                         sign(body, old)["X-Slack-Signature"])


def test_missing_timestamp_rejected():
    with pytest.raises(AuthError, match="timestamp"):
        verify_signature(SECRET, "", b"{}", "v0=x")


# --- gate 3: the right person ------------------------------------------

def test_non_operator_in_the_channel_cannot_resolve(setup):
    # The attack from Project Vend: someone in the channel talks the system
    # into acting. A valid Slack signature is NOT authorization.
    q, e, handler = setup
    body = click(e.id, "alternative", user=INTRUDER)
    ts = int(time.time())
    with pytest.raises(AuthError, match="operator"):
        handler.handle(sign(body, ts), body)
    assert len(q.open_items()) == 1          # untouched


def test_operator_can_resolve(setup):
    q, e, handler = setup
    body = click(e.id, "alternative")
    out = handler.handle(sign(body, int(time.time())), body)
    assert "issue the refund" in out["text"]
    assert not q.open_items()


# --- gate 4: real, open item -------------------------------------------

def test_double_click_is_idempotent(setup):
    q, e, handler = setup
    body = click(e.id, "alternative")
    ts = int(time.time())
    handler.handle(sign(body, ts), body)
    again = handler.handle(sign(body, ts), body)
    assert "already resolved" in again["text"]


def test_slack_retry_is_dropped_not_replayed(setup):
    q, e, handler = setup
    body = click(e.id, "alternative")
    headers = sign(body, int(time.time())) | {"X-Slack-Retry-Num": "1"}
    handler.handle(headers, body)
    assert len(q.open_items()) == 1          # retry never resolved it


def test_unknown_id_does_not_raise(setup):
    # Must not 500 — a 5xx makes Slack retry, amplifying the problem.
    q, e, handler = setup
    body = click("nope", "alternative")
    out = handler.handle(sign(body, int(time.time())), body)
    assert "unknown or already resolved" in out["text"]


def test_bot_messages_are_ignored(setup):
    # Otherwise the digest is read back as a reply to itself.
    q, e, handler = setup
    body = json.dumps({"event": {"bot_id": "B1", "text": f"{e.id} alt"}}).encode()
    handler.handle(sign(body, int(time.time())), body)
    assert len(q.open_items()) == 1


def test_url_verification_challenge(setup):
    q, e, handler = setup
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    out = handler.handle(sign(body, int(time.time())), body)
    assert out["challenge"] == "abc"


# --- text fallback ------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("abc123 alt", ("abc123", "alternative")),
    ("`abc123` ok", ("abc123", "proposed")),
    ("abc123 yes", ("abc123", "proposed")),
    ("please just approve it", None),        # no guessing
    ("abc123", None),
    ("abc123 maybe", None),
])
def test_text_replies_are_rigid(text, expected):
    assert parse_text_reply(text) == expected


# --- rendering ----------------------------------------------------------

def test_commitment_renders_without_a_timeout_promise():
    blocks = blocks_for(esc(Klass.COMMITMENT))
    text = json.dumps(blocks)
    assert "No default, no timeout" in text
    assert "automatically in" not in text


def test_button_values_carry_the_id_so_stale_messages_are_safe():
    e = esc()
    actions = [b for b in blocks_for(e) if b["type"] == "actions"][0]
    assert {el["value"] for el in actions["elements"]} == {
        f"{e.id}:proposed", f"{e.id}:alternative"}


def test_evidence_can_be_redacted():
    e = esc(evidence={"customer_email": "a@b.com", "amount": 240})
    text = json.dumps(blocks_for(e, redact=frozenset({"customer_email"})))
    assert "a@b.com" not in text
    assert "240" in text
