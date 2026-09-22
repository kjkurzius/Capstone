"""Plan-as-data — the only place delegation happens, and it is not delegation.

The planner (one Opus 5 call at high effort) decomposes a novel task into
bounded steps. It returns a typed object, NOT prompts it hands to workers.
Code validates that object against the roster and the governor before
anything runs, then the harness executes the steps itself.

This is what keeps depth at 1. A worker never receives instructions from
another model, so there is no chain of briefs to audit and no way for one
model's mistake to become another model's premise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

# Both fields are required so the schema stays strict. `blocked` is the empty
# string when a plan exists, and a one-sentence reason when the task cannot be
# done with the agents available — a planner that must either plan or decline
# is far less likely to invent an agent than one that can only plan.
PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["steps", "blocked"],
    "properties": {
        "blocked": {"type": "string"},
        "steps": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "agent", "objective", "depends_on"],
                "properties": {
                    "id": {"type": "string"},
                    "agent": {"type": "string"},
                    "objective": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "max_usd": {"type": "number"},
                },
            },
        },
    },
}


class InvalidPlan(ValueError):
    """The plan is rejected whole. Never partially executed."""


class PlannerDeclined(RuntimeError):
    """The planner says the task cannot be done with the agents available.

    Not an error. A planner that can decline produces fewer plans that almost
    work, and "almost works" is the expensive failure.
    """


@dataclass(frozen=True)
class Step:
    id: str
    agent: str
    objective: str
    depends_on: tuple[str, ...] = ()
    max_usd: float = 0.0


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...] = ()

    @property
    def total_usd(self) -> float:
        return sum(s.max_usd for s in self.steps)

    def order(self) -> list[Step]:
        """Topological order. Assumes validate() already ruled out cycles."""
        done: set[str] = set()
        out: list[Step] = []
        remaining = list(self.steps)
        while remaining:
            ready = [s for s in remaining if set(s.depends_on) <= done]
            out.extend(ready)
            done.update(s.id for s in ready)
            remaining = [s for s in remaining if s not in ready]
        return out


def validate(raw: dict, roster: dict, *, max_plan_usd: float) -> Plan:
    """Reject the whole plan, before any step runs.

    Everything checked here is a thing a model could plausibly get wrong, and
    every one of them is cheaper to catch now than mid-execution: an agent
    that does not exist, a budget that exceeds the cap, a dependency on a step
    that was never defined, or a cycle that would run forever.
    """
    blocked = (raw.get("blocked") or "").strip()
    if blocked:
        raise PlannerDeclined(blocked)

    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise InvalidPlan("plan has no steps")
    if len(raw_steps) > 12:
        raise InvalidPlan(f"{len(raw_steps)} steps exceeds the limit of 12")

    steps: list[Step] = []
    seen: set[str] = set()
    for i, s in enumerate(raw_steps):
        try:
            sid, agent = str(s["id"]), str(s["agent"])
            objective = str(s["objective"])
            deps = tuple(str(d) for d in s.get("depends_on", []))
        except (KeyError, TypeError) as exc:
            raise InvalidPlan(f"step {i} is malformed: {exc}") from exc

        if sid in seen:
            raise InvalidPlan(f"duplicate step id {sid!r}")
        seen.add(sid)

        if agent not in roster:
            raise InvalidPlan(f"step {sid!r} names unknown agent {agent!r}")
        if not objective.strip():
            raise InvalidPlan(f"step {sid!r} has an empty objective")

        cap = roster[agent].budget.max_usd
        usd = float(s.get("max_usd") or cap)
        if usd > cap:
            raise InvalidPlan(
                f"step {sid!r} asks ${usd:.2f}; {agent} is capped at ${cap:.2f}"
            )
        steps.append(Step(sid, agent, objective, deps, usd))

    for s in steps:
        for d in s.depends_on:
            if d not in seen:
                raise InvalidPlan(f"step {s.id!r} depends on undefined {d!r}")
            if d == s.id:
                raise InvalidPlan(f"step {s.id!r} depends on itself")

    plan = Plan(tuple(steps))
    if plan.total_usd > max_plan_usd:
        raise InvalidPlan(
            f"plan totals ${plan.total_usd:.2f}, over the ${max_plan_usd:.2f} cap"
        )
    _reject_cycles(steps)
    return plan


def _reject_cycles(steps: Sequence[Step]) -> None:
    done: set[str] = set()
    remaining = list(steps)
    while remaining:
        ready = [s for s in remaining if set(s.depends_on) <= done]
        if not ready:
            stuck = ", ".join(sorted(s.id for s in remaining))
            raise InvalidPlan(f"dependency cycle among: {stuck}")
        done.update(s.id for s in ready)
        remaining = [s for s in remaining if s not in ready]


PLANNER_CHARTER = """\
You decompose a task into a plan. You do not do the work and you do not \
write instructions for whoever does.

Return a plan whose steps each name one agent from the roster, state one \
concrete objective, and declare dependencies. Keep it to the fewest steps \
that actually accomplish the task — every extra step multiplies the error \
rate and costs money.

You have no authority to exceed an agent's budget or to invent an agent. A \
plan that does either is rejected whole, and nothing runs.

If the task cannot be done with the agents available, say so in one sentence \
instead of producing a plan that almost works."""
