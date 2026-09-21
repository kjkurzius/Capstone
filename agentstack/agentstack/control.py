"""The control loop.

This is the answer to "how do I connect Jev to the subagents": Jev is not
wired to Claude at all. Jev is wired to this loop, and this loop decides
whether Claude runs, what it may do while running, and whether its output
reaches a customer.

    intake → Jev triage → [deterministic | reject | escalate | Claude]
           → Jev guard on every tool call, before execution
           → Jev verify against acceptance criteria
           → ship | revise | escalate, by calibrated confidence
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .guards import Governor, GuardViolation
from .jev import JevClient, Verdict
from .ledger import Ledger
from .roster import GUARD_QUESTIONS, Subagent, triage_questions
from .worker import ClaudeWorker


@dataclass
class Outcome:
    task_id: str
    disposition: str            # shipped | revised | escalated | rejected | deterministic
    text: str
    confidence: float
    usd: float
    seconds: float
    reason: str = ""


class Fleet:
    def __init__(
        self,
        roster: dict[str, Subagent],
        jev: JevClient,
        worker: ClaudeWorker,
        ledger: Ledger,
        governor: Governor,
        *,
        tool_schemas: dict[str, list[dict]],
        execute: Callable[[str, dict], Any],
        deterministic: Callable[[str], str | None] = lambda _: None,
    ):
        self._roster = roster
        self._jev = jev
        self._worker = worker
        self._ledger = ledger
        self._gov = governor
        self._schemas = tool_schemas
        self._execute = execute
        self._deterministic = deterministic

    # --- entry point --------------------------------------------------------

    def handle(self, task: str) -> Outcome:
        task_id = uuid.uuid4().hex
        self._gov.check_running()
        self._ledger.event(task_id, "intake", {"task": task})

        triage = self._jev.ask(task, triage_questions(list(self._roster)))
        self._ledger.event(task_id, "triage", _dump(triage))

        if triage.degraded:
            # The reflex plane is down. Fail closed: a human decides, rather
            # than the fleet shipping ungated work at speed.
            return self._escalate(task_id, "", 0.0, 0.0, 0.0,
                                  "Jev unreachable — failing closed")

        if not triage["safe"]:
            return self._finish(task_id, "rejected", "", 0.0, 0.0, 0.0,
                                "flagged unsafe at triage")
        if not triage["in_scope"]:
            return self._finish(task_id, "rejected", "", 0.0, 0.0, 0.0,
                                "outside what the business offers")

        # Cheapest path first: if code can answer it exactly, no model runs.
        if triage["deterministic"]:
            answer = self._deterministic(task)
            if answer is not None:
                self._ledger.event(task_id, "deterministic", {"answer": answer})
                return self._finish(task_id, "deterministic", answer, 1.0, 0.0, 0.0)

        agent = self._roster.get(triage["owner"].value)
        if agent is None:
            return self._escalate(task_id, "", 0.0, 0.0, 0.0, "no agent owns this")

        return self._work(task_id, agent, task)

    # --- the worked path ----------------------------------------------------

    def _work(self, task_id: str, agent: Subagent, task: str) -> Outcome:
        allowed = frozenset(agent.tools)
        usd = seconds = 0.0
        text = ""
        confidence = 0.0

        for attempt in range(agent.budget.max_revisions + 1):
            run = self._worker.run(
                agent, task, self._schemas.get(agent.name, []),
                execute=self._execute,
                gate=lambda tool, args: self._gate(task_id, agent, tool, args, allowed),
            )
            usd += run.usd
            seconds += run.seconds
            text = run.text
            self._gov.record_spend(run.usd)
            self._ledger.event(task_id, "run", {
                "attempt": attempt, "stopped_for": run.stopped_for,
                "tool_calls": run.tool_calls, "blocked": run.blocked,
                "usd": run.usd, "seconds": run.seconds,
            }, agent.name)

            if run.stopped_for in ("budget", "refusal"):
                return self._escalate(task_id, text, 0.0, usd, seconds,
                                      f"run stopped: {run.stopped_for}", agent.name)

            # Verify against the charter and the output together, so the
            # verifier judges the work against the job, not in a vacuum.
            state = f"CHARTER:\n{agent.charter}\n\nTASK:\n{task}\n\nOUTPUT:\n{text}"
            verdict = self._jev.ask(state, list(agent.acceptance))
            confidence = verdict.confidence()
            self._ledger.event(task_id, "verify", _dump(verdict), agent.name)

            if verdict.degraded:
                return self._escalate(task_id, text, 0.0, usd, seconds,
                                      "Jev unreachable at verify — failing closed",
                                      agent.name)

            if confidence >= agent.thresholds.ship:
                return self._finish(task_id, "shipped", text, confidence,
                                    usd, seconds, "", agent.name)

            if confidence < agent.thresholds.review:
                return self._escalate(task_id, text, confidence, usd, seconds,
                                      f"confidence {confidence:.3f} below review floor",
                                      agent.name)

            # Between the thresholds: name the failing criteria and revise.
            failing = [k for k, a in verdict.answers.items() if not a.value or a.confidence < 0.9]
            task = (
                f"{task}\n\n---\nA previous attempt did not meet these acceptance "
                f"criteria: {', '.join(failing)}. Revise to satisfy every one of "
                f"them. Do not restate the criteria; just produce the corrected work."
            )

        # Revisions exhausted while still in the middle band.
        return self._escalate(task_id, text, confidence, usd, seconds,
                              "revision limit reached without clearing the ship threshold",
                              agent.name)

    # --- the guard, called before every tool call ---------------------------

    def _gate(self, task_id: str, agent: Subagent, tool: str,
              args: dict, allowed: frozenset[str]) -> str | None:
        """Return a refusal reason, or None to let the call through."""
        # Deterministic checks first: they are free, and they cannot be argued
        # with. Only pay for a Jev call on something already within the rules.
        try:
            self._gov.check_tool_call(agent.name, tool, args, allowed)
        except GuardViolation as exc:
            self._ledger.event(task_id, "blocked", {"tool": tool, "by": "governor",
                                                    "reason": str(exc)}, agent.name)
            return str(exc)

        state = (f"CHARTER:\n{agent.charter}\n\nPROPOSED ACTION: {tool}\n"
                 f"ARGUMENTS: {args}")
        v = self._jev.ask(state, list(GUARD_QUESTIONS))

        if v.degraded and tool in self._gov.limits.irreversible_tools:
            return "Jev unreachable and this action is irreversible"

        risky = [k for k in ("destructive", "irreversible", "spends", "personal_data")
                 if v.answers[k].value and v.answers[k].confidence > 0.7]
        if risky or not v["in_charter"]:
            reason = ", ".join(risky) or "outside charter"
            self._ledger.event(task_id, "blocked", {"tool": tool, "by": "jev",
                                                    "reason": reason}, agent.name)
            return reason
        return None

    # --- bookkeeping --------------------------------------------------------

    def _finish(self, task_id: str, disposition: str, text: str, confidence: float,
                usd: float, seconds: float, reason: str = "", agent: str = "") -> Outcome:
        self._ledger.decision(task_id, agent, confidence, disposition, usd, seconds)
        self._ledger.event(task_id, disposition,
                           {"reason": reason, "confidence": confidence}, agent)
        return Outcome(task_id, disposition, text, confidence, usd, seconds, reason)

    def _escalate(self, task_id: str, text: str, confidence: float, usd: float,
                  seconds: float, reason: str, agent: str = "") -> Outcome:
        return self._finish(task_id, "escalated", text, confidence,
                            usd, seconds, reason, agent)


def _dump(v: Verdict) -> dict:
    return {
        "latency_ms": round(v.latency_ms, 1),
        "degraded": v.degraded,
        "answers": {k: {"value": a.value, "confidence": round(a.confidence, 4)}
                    for k, a in v.answers.items()},
    }
