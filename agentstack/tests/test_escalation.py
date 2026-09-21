"""Escalation and plan validation.

The invariants here are the ones that matter when nobody is watching: silence
must be safe, money must not move by default, and a COMMITMENT must never
resolve itself.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentstack.escalation import (
    DECAYING, Escalation, EscalationError, Klass, Queue, digest,
)
from agentstack.plan import InvalidPlan, validate
from agentstack.roster import ROSTER


def esc(klass=Klass.LOW_CONFIDENCE, **kw):
    base = dict(task_id="t1", klass=klass, summary="s",
                proposed="hold", alternative="ship")
    if klass is Klass.COMMITMENT:
        base["proposed"] = ""
    return Escalation(**{**base, **kw})


# --- the invariants -----------------------------------------------------

def test_commitment_cannot_carry_a_default():
    with pytest.raises(EscalationError):
        Escalation(task_id="t", klass=Klass.COMMITMENT, summary="sign this",
                   proposed="sign it", alternative="decline")


def test_commitment_never_expires():
    e = esc(Klass.COMMITMENT)
    assert e.deadline is None
    assert not e.expired(time.time() + 365 * 86400)


def test_every_other_class_must_have_a_default():
    # Silence has to resolve, or the operator becomes the bottleneck.
    with pytest.raises(EscalationError):
        Escalation(task_id="t", klass=Klass.NOVEL, summary="s",
                   proposed="", alternative="a")


def test_threshold_denies_on_timeout(tmp_path):
    # Money must never move because nobody looked.
    q = Queue(tmp_path / "e.db")
    from agentstack.escalation import POLICY
    assert POLICY[Klass.THRESHOLD][1] == "deny"
    q.raise_(esc(Klass.THRESHOLD, proposed="deny the payment"))
    fired = q.sweep(now=time.time() + 5 * 3600)
    assert len(fired) == 1
    assert not q.open_items()


def test_sweep_leaves_commitments_alone(tmp_path):
    q = Queue(tmp_path / "e.db")
    q.raise_(esc(Klass.COMMITMENT))
    q.raise_(esc(Klass.NOVEL))
    fired = q.sweep(now=time.time() + 100 * 86400)
    assert [e.klass for e in fired] == [Klass.NOVEL]
    assert [e.klass for e in q.open_items()] == [Klass.COMMITMENT]


# --- queue behaviour ----------------------------------------------------

def test_resolving_twice_is_an_error(tmp_path):
    q = Queue(tmp_path / "e.db")
    e = q.raise_(esc())
    q.resolve(e.id, "proposed")
    with pytest.raises(EscalationError):
        q.resolve(e.id, "alternative")


def test_over_budget_is_reported(tmp_path):
    q = Queue(tmp_path / "e.db", daily_budget=3)
    for _ in range(5):
        q.raise_(esc())
    assert q.over_budget() == 2


def test_is_learning_compares_decaying_classes(tmp_path):
    q = Queue(tmp_path / "e.db")
    now = time.time()
    for _ in range(6):                                   # prior 30 days
        q.raise_(esc(raised_at=now - 45 * 86400))
    for _ in range(2):                                   # recent 30 days
        q.raise_(esc(raised_at=now - 5 * 86400))
    assert q.is_learning(now)
    # Permanent classes must not count toward the trend.
    assert Klass.THRESHOLD not in DECAYING
    assert Klass.COMMITMENT not in DECAYING


def test_digest_separates_blocking_from_self_resolving(tmp_path):
    out = digest([esc(Klass.COMMITMENT, summary="sign MSA"),
                  esc(Klass.NOVEL, summary="odd request")])
    assert "Waiting on you (1)" in out
    assert "Will resolve themselves (1)" in out
    assert "does not time out" in out


# --- plan validation ----------------------------------------------------

def good_plan():
    return {"steps": [
        {"id": "a", "agent": "intake", "objective": "qualify", "depends_on": []},
        {"id": "b", "agent": "support", "objective": "reply", "depends_on": ["a"]},
    ]}


def test_valid_plan_orders_topologically():
    p = validate(good_plan(), ROSTER, max_plan_usd=10.0)
    assert [s.id for s in p.order()] == ["a", "b"]


def test_unknown_agent_rejects_the_whole_plan():
    raw = good_plan()
    raw["steps"][1]["agent"] = "ghost"
    with pytest.raises(InvalidPlan, match="unknown agent"):
        validate(raw, ROSTER, max_plan_usd=10.0)


def test_step_cannot_exceed_its_agent_budget():
    raw = good_plan()
    raw["steps"][0]["max_usd"] = 999.0
    with pytest.raises(InvalidPlan, match="capped"):
        validate(raw, ROSTER, max_plan_usd=10_000.0)


def test_cycle_is_rejected():
    raw = {"steps": [
        {"id": "a", "agent": "intake", "objective": "x", "depends_on": ["b"]},
        {"id": "b", "agent": "intake", "objective": "y", "depends_on": ["a"]},
    ]}
    with pytest.raises(InvalidPlan, match="cycle"):
        validate(raw, ROSTER, max_plan_usd=10.0)


def test_dependency_on_undefined_step_is_rejected():
    raw = good_plan()
    raw["steps"][1]["depends_on"] = ["nope"]
    with pytest.raises(InvalidPlan, match="undefined"):
        validate(raw, ROSTER, max_plan_usd=10.0)


def test_plan_total_is_capped():
    with pytest.raises(InvalidPlan, match="over the"):
        validate(good_plan(), ROSTER, max_plan_usd=0.01)
