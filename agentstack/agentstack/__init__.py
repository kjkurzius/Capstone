"""agentstack — a Jev-gated, Claude-powered agent fleet for a single operator.

Three planes, and putting work in the wrong one is the expensive mistake:

    deterministic   plain code        anything with a correct answer
    reflex          Jev               decisions with a known answer space
    reasoning       Claude Opus 5     anything that must be generated

See docs/ARCHITECTURE.md.
"""

from .control import Fleet, Outcome
from .escalation import Escalation, Klass, Queue, digest
from .guards import Governor, GuardViolation, Limits
from .jev import Choice, JevClient, Noul, Score, Verdict
from .ledger import Ledger
from .plan import InvalidPlan, Plan, PlannerDeclined, Step, validate
from .planner import Planner, PlannerFailed, PlanExecutor, PlanRun
from .slack import (
    AuthError, Notifier, ReplyHandler, SlackConfig, SlackTransport,
    verify_signature,
)
from .roster import ROSTER, Budget, Subagent, Thresholds
from .worker import ClaudeWorker

__all__ = [
    "Fleet", "Outcome", "Governor", "GuardViolation", "Limits",
    "Escalation", "Klass", "Queue", "digest",
    "InvalidPlan", "Plan", "PlannerDeclined", "Step", "validate",
    "Planner", "PlannerFailed", "PlanExecutor", "PlanRun",
    "AuthError", "Notifier", "ReplyHandler", "SlackConfig",
    "SlackTransport", "verify_signature",
    "Choice", "JevClient", "Noul", "Score", "Verdict", "Ledger",
    "ROSTER", "Budget", "Subagent", "Thresholds", "ClaudeWorker",
]
