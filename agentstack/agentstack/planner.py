"""The planner round trip.

One Opus 5 call decomposes a novel task into a plan, code validates it, and
the harness executes it. The planner never speaks to a worker.

The distinction that keeps depth at 1: structured outputs guarantee the reply
is *shaped* like a plan. They guarantee nothing about whether it is a *valid*
plan. The model can still name an agent that does not exist, ask for more
budget than an agent has, or describe a dependency cycle — all schema-valid.
So `plan.validate()` runs on every reply, and a plan that fails is re-asked
with the exact error rather than patched.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

from .escalation import Escalation, Klass, Queue
from .ledger import Ledger
from .plan import (
    PLAN_SCHEMA, PLANNER_CHARTER, InvalidPlan, Plan, PlannerDeclined, Step,
    validate,
)
from .roster import Subagent

MODEL = "claude-opus-5"
USD_PER_INPUT_TOKEN = 5.00 / 1_000_000
USD_PER_OUTPUT_TOKEN = 25.00 / 1_000_000


class PlannerFailed(RuntimeError):
    """Could not produce a valid plan within the attempt budget."""


@dataclass
class PlanResult:
    plan: Plan | None
    attempts: int
    usd: float
    seconds: float
    declined: str = ""
    errors: list[str] = field(default_factory=list)


def roster_brief(roster: dict[str, Subagent]) -> str:
    """What the planner is allowed to know: names, charters, budgets.

    Deliberately not the tool schemas or the acceptance criteria. The planner
    decides *who* does *what*, and giving it the machinery of how invites it
    to write instructions instead of a plan.
    """
    lines = ["AGENTS AVAILABLE:"]
    for name, a in sorted(roster.items()):
        charter = " ".join(a.charter.split())
        lines.append(f"\n- {name} (budget ${a.budget.max_usd:.2f} per step)")
        lines.append(f"  {charter}")
    return "\n".join(lines)


class Planner:
    def __init__(
        self,
        roster: dict[str, Subagent],
        *,
        client: anthropic.Anthropic | None = None,
        max_attempts: int = 3,
        max_plan_usd: float = 10.0,
    ):
        self._c = client or anthropic.Anthropic()
        self._roster = roster
        # Each re-ask costs a full Opus call. Three attempts is enough for a
        # model to fix a named error and few enough that a planner stuck in a
        # loop escalates instead of quietly spending.
        self._max_attempts = max_attempts
        self._max_plan_usd = max_plan_usd

    def plan(self, task: str) -> PlanResult:
        started = time.perf_counter()
        usd = 0.0
        errors: list[str] = []

        # The roster and charter are a stable prefix, so they cache across
        # every planning call the business ever makes. Only the task varies.
        system = [{
            "type": "text",
            "text": f"{PLANNER_CHARTER}\n\n{roster_brief(self._roster)}",
            "cache_control": {"type": "ephemeral"},
        }]
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]

        for attempt in range(1, self._max_attempts + 1):
            resp = self._c.beta.messages.create(
                model=MODEL,
                max_tokens=8_000,
                system=system,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={
                    "effort": "high",     # planning is the one place to spend
                    "format": {"type": "json_schema", "schema": PLAN_SCHEMA},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            usd += (resp.usage.input_tokens * USD_PER_INPUT_TOKEN
                    + resp.usage.output_tokens * USD_PER_OUTPUT_TOKEN)

            # Always check before reading content — a refusal returns HTTP 200
            # with no usable body.
            if resp.stop_reason == "refusal":
                errors.append("planner refused the task")
                break

            raw = _extract_json(resp)
            if raw is None:
                errors.append("no parseable JSON in the reply")
                continue

            try:
                plan = validate(raw, self._roster, max_plan_usd=self._max_plan_usd)
            except PlannerDeclined as declined:
                return PlanResult(None, attempt, usd,
                                  time.perf_counter() - started,
                                  declined=str(declined))
            except InvalidPlan as bad:
                # Re-ask with the exact failure. Never repair the plan here:
                # a patched plan is one the model did not actually endorse,
                # and the point of validation is that nothing unendorsed runs.
                errors.append(str(bad))
                messages += [
                    {"role": "assistant", "content": json.dumps(raw)},
                    {"role": "user", "content":
                        f"That plan was rejected: {bad}\n\n"
                        f"Return a corrected plan, or set `blocked` if the "
                        f"task cannot be done with these agents."},
                ]
                continue

            return PlanResult(plan, attempt, usd, time.perf_counter() - started,
                              errors=errors)

        raise PlannerFailed(
            f"no valid plan in {self._max_attempts} attempts: {'; '.join(errors)}"
        )


def _extract_json(resp: Any) -> dict | None:
    """output_config.format guarantees the first text block is valid JSON."""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            try:
                parsed = json.loads(block.text)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


# --- execution ---------------------------------------------------------------

@dataclass
class StepOutcome:
    step: Step
    disposition: str
    text: str
    usd: float


@dataclass
class PlanRun:
    outcomes: list[StepOutcome] = field(default_factory=list)
    halted_at: str = ""          # step id, if the plan stopped early
    reason: str = ""

    @property
    def usd(self) -> float:
        return sum(o.usd for o in self.outcomes)

    @property
    def completed(self) -> bool:
        return not self.halted_at


class PlanExecutor:
    """Runs a validated plan, one step at a time, through the Fleet.

    Halts on the first step that does not ship. Every later step was planned
    on the assumption that the earlier ones succeeded, so continuing past an
    escalation means acting on an assumption a human has not confirmed yet —
    which is exactly the compounding this architecture exists to prevent.
    """

    def __init__(self, fleet, ledger: Ledger, queue: Queue | None = None):
        self._fleet = fleet
        self._ledger = ledger
        self._queue = queue

    def run(self, plan_id: str, plan: Plan,
            render: Callable[[Step, dict[str, str]], str] | None = None) -> PlanRun:
        render = render or _default_render
        run = PlanRun()
        results: dict[str, str] = {}

        self._ledger.event(plan_id, "plan_start", {
            "steps": [s.id for s in plan.order()],
            "planned_usd": round(plan.total_usd, 4),
        })

        for step in plan.order():
            outcome = self._fleet.handle(render(step, results))
            run.outcomes.append(
                StepOutcome(step, outcome.disposition, outcome.text, outcome.usd)
            )
            self._ledger.event(plan_id, "plan_step", {
                "step": step.id, "agent": step.agent,
                "disposition": outcome.disposition,
                "usd": round(outcome.usd, 4),
            }, step.agent)

            if outcome.disposition not in ("shipped", "deterministic"):
                run.halted_at = step.id
                run.reason = (f"step {step.id!r} ({step.agent}) "
                              f"{outcome.disposition}: {outcome.reason}")
                self._halt(plan_id, run, step)
                break

            results[step.id] = outcome.text

        self._ledger.event(plan_id, "plan_end", {
            "completed": run.completed, "halted_at": run.halted_at,
            "usd": round(run.usd, 4),
        })
        return run

    def _halt(self, plan_id: str, run: PlanRun, step: Step) -> None:
        if self._queue is None:
            return
        remaining = len(run.outcomes)
        self._queue.raise_(Escalation(
            task_id=plan_id,
            # A stalled plan is a CONFLICT, not low confidence: the plan and
            # reality disagree, which usually means the plan was wrong rather
            # than the step was.
            klass=Klass.CONFLICT,
            agent=step.agent,
            summary=f"Plan halted at step {step.id!r} — {run.reason}",
            proposed="abandon the remaining steps",
            alternative="resume the plan from this step",
            evidence={"completed_steps": remaining, "usd_spent": round(run.usd, 4)},
        ))


def _default_render(step: Step, results: dict[str, str]) -> str:
    """Build a step's task text from its objective plus its dependencies.

    Only declared dependencies are passed. A step cannot see the output of a
    step it did not declare a dependency on, so the plan's edges are the real
    information flow rather than a suggestion.
    """
    parts = [step.objective]
    for dep in step.depends_on:
        if dep in results:
            parts.append(f"\n--- output of step {dep} ---\n{results[dep]}")
    return "\n".join(parts)
