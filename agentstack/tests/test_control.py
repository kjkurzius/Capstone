"""Smoke tests for the control loop, with both planes faked.

The point is to prove the gate logic — ship / revise / escalate / fail-closed —
without spending a cent or needing either API to be reachable.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentstack.control import Fleet
from agentstack.guards import Governor, Limits
from agentstack.jev import Answer, Noul, Verdict
from agentstack.ledger import Ledger
from agentstack.roster import Subagent, Thresholds
from agentstack.worker import Run


AGENT = Subagent(
    name="writer",
    charter="You write short replies.",
    tools=("draft_reply",),
    acceptance=(Noul("answers", "Does the reply answer the question?"),),
    thresholds=Thresholds(ship=0.95, review=0.60),
)


class FakeJev:
    """Returns a scripted confidence for verify, and clean guards."""

    def __init__(self, verify_conf: float, *, degraded: bool = False):
        self._conf = verify_conf
        self._degraded = degraded

    def ask(self, state, questions):
        if self._degraded:
            return Verdict({q.key: Answer(q.key, False, 0.0) for q in questions},
                           1.0, degraded=True)
        answers = {}
        for q in questions:
            if q.key == "owner":
                answers[q.key] = Answer(q.key, "writer", 0.99)
            elif q.key == "deterministic":
                answers[q.key] = Answer(q.key, False, 0.99)
            elif q.key in ("in_scope", "safe", "in_charter"):
                answers[q.key] = Answer(q.key, True, 0.99)
            elif q.key in ("destructive", "irreversible", "spends", "personal_data"):
                answers[q.key] = Answer(q.key, False, 0.99)
            elif q.key in ("value", "complexity"):
                answers[q.key] = Answer(q.key, "moderate", 0.9)
            else:                                   # acceptance criteria
                answers[q.key] = Answer(q.key, True, self._conf)
        return Verdict(answers, 1.0)


class FakeWorker:
    def __init__(self):
        self.runs = 0

    def run(self, agent, task, schemas, execute, gate):
        self.runs += 1
        return Run(text=f"draft {self.runs}", tool_calls=0,
                   input_tokens=100, output_tokens=50,
                   seconds=0.01, stopped_for="done")


def build(jev, worker, tmp_path) -> Fleet:
    return Fleet(
        roster={"writer": AGENT},
        jev=jev,
        worker=worker,
        ledger=Ledger(tmp_path / "t.db"),
        governor=Governor(Limits()),
        tool_schemas={"writer": []},
        execute=lambda name, args: {"ok": True},
    )


def test_high_confidence_ships_without_a_human(tmp_path):
    fleet = build(FakeJev(0.99), FakeWorker(), tmp_path)
    out = fleet.handle("what are your hours?")
    assert out.disposition == "shipped"
    assert out.confidence >= AGENT.thresholds.ship


def test_low_confidence_escalates(tmp_path):
    fleet = build(FakeJev(0.40), FakeWorker(), tmp_path)
    out = fleet.handle("what are your hours?")
    assert out.disposition == "escalated"


def test_middle_band_revises_then_escalates(tmp_path):
    worker = FakeWorker()
    fleet = build(FakeJev(0.80), worker, tmp_path)
    out = fleet.handle("what are your hours?")
    assert out.disposition == "escalated"
    # One initial attempt plus max_revisions retries, not an unbounded loop.
    assert worker.runs == AGENT.budget.max_revisions + 1


def test_jev_outage_fails_closed(tmp_path):
    worker = FakeWorker()
    fleet = build(FakeJev(0.99, degraded=True), worker, tmp_path)
    out = fleet.handle("what are your hours?")
    assert out.disposition == "escalated"
    assert "unreachable" in out.reason
    # And nothing was generated: an outage must not cost model spend.
    assert worker.runs == 0


def test_governor_blocks_a_tool_outside_the_charter(tmp_path):
    gov = Governor(Limits())
    with pytest.raises(Exception):
        gov.check_tool_call("writer", "pay", {"amount_usd": 500},
                            allowed=frozenset({"draft_reply"}))


def test_autonomy_rate_is_measurable(tmp_path):
    ledger = Ledger(tmp_path / "m.db")
    for i in range(8):
        ledger.decision(f"t{i}", "writer", 0.98, "shipped", 0.01, 1.0)
    for i in range(2):
        ledger.decision(f"e{i}", "writer", 0.50, "escalated", 0.01, 1.0)
    assert ledger.autonomy_rate("writer") == pytest.approx(0.8)
