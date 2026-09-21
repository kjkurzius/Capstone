"""agentstack — a Jev-gated, Claude-powered agent fleet for a single operator.

Three planes, and putting work in the wrong one is the expensive mistake:

    deterministic   plain code        anything with a correct answer
    reflex          Jev               decisions with a known answer space
    reasoning       Claude Opus 5     anything that must be generated

See docs/ARCHITECTURE.md.
"""

from .control import Fleet, Outcome
from .guards import Governor, GuardViolation, Limits
from .jev import Choice, JevClient, Noul, Score, Verdict
from .ledger import Ledger
from .roster import ROSTER, Budget, Subagent, Thresholds
from .worker import ClaudeWorker

__all__ = [
    "Fleet", "Outcome", "Governor", "GuardViolation", "Limits",
    "Choice", "JevClient", "Noul", "Score", "Verdict", "Ledger",
    "ROSTER", "Budget", "Subagent", "Thresholds", "ClaudeWorker",
]
