"""Planner round trip.

The claim under test: structured outputs guarantee shape, not validity. Every
schema-valid-but-wrong plan below must be rejected and re-asked, and a planner
that cannot converge must escalate rather than spend.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentstack.escalation import Klass, Queue
from agentstack.ledger import Ledger
from agentstack.plan import Plan, PlannerDeclined, Step
from agentstack.planner import (
    Planner, PlannerFailed, PlanExecutor, _default_render, roster_brief,
)
from agentstack.roster import ROSTER


# --- fakes --------------------------------------------------------------

@dataclass
class FakeBlock:
    text: str
    type: str = "text"


@dataclass
class FakeUsage:
    input_tokens: int = 1000
    output_tokens: int = 200


@dataclass
class FakeResponse:
    content: list
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_reason: str = "end_turn"


class FakeClient:
    """Replays scripted planner replies and records what it was sent."""

    def __init__(self, replies: list):
        self._replies = list(replies)
        self.calls: list[dict] = []
        self.beta = self

    @property
    def messages(self):
        return self

    def create(self, **kw):
        self.calls.append(kw)
        r = self._replies.pop(0)
        if isinstance(r, FakeResponse):
            return r
        return FakeResponse(content=[FakeBlock(json.dumps(r))])


def reply(steps, blocked=""):
    return {"steps": steps, "blocked": blocked}


GOOD = [
    {"id": "a", "agent": "intake", "objective": "qualify", "depends_on": []},
    {"id": "b", "agent": "support", "objective": "reply", "depends_on": ["a"]},
]


# --- the round trip -----------------------------------------------------

def test_valid_plan_on_first_attempt():
    p = Planner(ROSTER, client=FakeClient([reply(GOOD)]))
    result = p.plan("handle this")
    assert result.attempts == 1
    assert [s.id for s in result.plan.order()] == ["a", "b"]
    assert result.usd > 0


def test_schema_valid_but_invalid_plan_is_reasked_with_the_error():
    # An agent that does not exist is perfectly schema-valid. This is the
    # whole reason validate() runs after structured outputs.
    bad = [{"id": "a", "agent": "ghost", "objective": "x", "depends_on": []}]
    client = FakeClient([reply(bad), reply(GOOD)])
    result = Planner(ROSTER, client=client).plan("do it")

    assert result.attempts == 2
    assert result.plan is not None
    # The re-ask must name the actual failure, not just say "try again".
    followup = client.calls[1]["messages"][-1]["content"]
    assert "unknown agent" in followup and "ghost" in followup


def test_cycle_is_reasked_not_repaired():
    cyclic = [
        {"id": "a", "agent": "intake", "objective": "x", "depends_on": ["b"]},
        {"id": "b", "agent": "intake", "objective": "y", "depends_on": ["a"]},
    ]
    client = FakeClient([reply(cyclic), reply(GOOD)])
    result = Planner(ROSTER, client=client).plan("do it")
    assert result.attempts == 2
    assert "cycle" in client.calls[1]["messages"][-1]["content"]


def test_gives_up_after_the_attempt_budget():
    bad = [{"id": "a", "agent": "ghost", "objective": "x", "depends_on": []}]
    client = FakeClient([reply(bad)] * 3)
    with pytest.raises(PlannerFailed, match="3 attempts"):
        Planner(ROSTER, client=client, max_attempts=3).plan("do it")
    assert len(client.calls) == 3          # bounded, not unbounded


def test_declining_is_not_an_error():
    client = FakeClient([reply([], blocked="no agent can process PDFs")])
    result = Planner(ROSTER, client=client).plan("parse this PDF")
    assert result.plan is None
    assert result.declined == "no agent can process PDFs"
    assert len(client.calls) == 1          # a decline is final, not re-asked


def test_refusal_stop_reason_is_handled_before_reading_content():
    client = FakeClient([FakeResponse(content=[], stop_reason="refusal")])
    with pytest.raises(PlannerFailed, match="refused"):
        Planner(ROSTER, client=client).plan("something disallowed")


def test_unparseable_reply_is_retried():
    client = FakeClient([
        FakeResponse(content=[FakeBlock("not json at all")]),
        reply(GOOD),
    ])
    result = Planner(ROSTER, client=client).plan("do it")
    assert result.attempts == 2


# --- what the planner is allowed to see ---------------------------------

def test_request_shape_matches_the_architecture():
    client = FakeClient([reply(GOOD)])
    Planner(ROSTER, client=client).plan("do it")
    kw = client.calls[0]

    assert kw["model"] == "claude-opus-5"
    assert kw["thinking"] == {"type": "adaptive"}
    # Planning is the one place worth spending effort; workers run at "low".
    assert kw["output_config"]["effort"] == "high"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    # The stable prefix must be cached — it is reused on every planning call.
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["fallbacks"] == "default"


def test_roster_brief_withholds_the_machinery():
    brief = roster_brief(ROSTER)
    assert "intake" in brief and "support" in brief
    assert "budget $" in brief
    # Tool names and acceptance criteria are deliberately withheld: the
    # planner says who does what, not how, or it starts writing instructions.
    assert "price_book" not in brief
    assert "no_overpromise" not in brief


# --- execution ----------------------------------------------------------

@dataclass
class FakeOutcome:
    disposition: str
    text: str = "done"
    usd: float = 0.01
    reason: str = ""


class FakeFleet:
    def __init__(self, dispositions):
        self._d = list(dispositions)
        self.tasks: list[str] = []

    def handle(self, task):
        self.tasks.append(task)
        d = self._d.pop(0)
        return FakeOutcome(d, text=f"result-{len(self.tasks)}",
                           reason="below review floor" if d == "escalated" else "")


def two_step_plan():
    return Plan((Step("a", "intake", "qualify"),
                 Step("b", "support", "reply", ("a",))))


def test_plan_runs_in_dependency_order(tmp_path):
    fleet = FakeFleet(["shipped", "shipped"])
    ex = PlanExecutor(fleet, Ledger(tmp_path / "l.db"))
    run = ex.run("p1", two_step_plan())
    assert run.completed
    assert [o.step.id for o in run.outcomes] == ["a", "b"]
    # Step b declared a dependency on a, so it receives a's output.
    assert "result-1" in fleet.tasks[1]


def test_plan_halts_on_the_first_non_shipping_step(tmp_path):
    # Later steps were planned assuming the earlier ones worked. Continuing
    # past an escalation acts on an assumption nobody has confirmed.
    fleet = FakeFleet(["escalated", "shipped"])
    ex = PlanExecutor(fleet, Ledger(tmp_path / "l.db"))
    run = ex.run("p1", two_step_plan())
    assert not run.completed
    assert run.halted_at == "a"
    assert len(fleet.tasks) == 1          # step b never ran


def test_halt_raises_a_conflict_escalation(tmp_path):
    q = Queue(tmp_path / "e.db")
    fleet = FakeFleet(["escalated", "shipped"])
    ex = PlanExecutor(fleet, Ledger(tmp_path / "l.db"), queue=q)
    ex.run("p1", two_step_plan())

    items = q.open_items()
    assert len(items) == 1
    assert items[0].klass is Klass.CONFLICT
    assert "halted" in items[0].summary


def test_a_step_cannot_see_undeclared_output():
    # The plan's edges are the real information flow, not a suggestion.
    text = _default_render(Step("c", "support", "do it"),
                           {"a": "secret from a"})
    assert "secret from a" not in text


def test_plan_execution_is_recorded(tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    ex = PlanExecutor(FakeFleet(["shipped", "shipped"]), ledger)
    ex.run("p1", two_step_plan())
    kinds = [r[0] for r in ledger._db.execute(
        "SELECT kind FROM events WHERE task_id='p1' ORDER BY ts").fetchall()]
    assert kinds == ["plan_start", "plan_step", "plan_step", "plan_end"]
