"""Subagent definitions.

A subagent is not a prompt. It is a charter, a tool set, acceptance criteria,
thresholds and a budget. The acceptance criteria are the job description: they
define what "done well" means as typed questions, which is what makes the role
both gateable and measurable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .jev import Choice, Noul, Question, Score


@dataclass(frozen=True)
class Budget:
    """Hard caps. Enforced in code, never by a model's judgement."""
    max_usd: float = 2.00
    max_tool_calls: int = 40
    max_seconds: float = 300.0
    max_revisions: int = 2


@dataclass(frozen=True)
class Thresholds:
    """The business dial. Tune per agent against logged outcomes, not vibes."""
    ship: float = 0.97
    review: float = 0.80


@dataclass(frozen=True)
class Subagent:
    name: str
    charter: str                        # system prompt: scope, voice, limits
    tools: Sequence[str]                # least privilege
    acceptance: Sequence[Question]      # what "done well" means
    thresholds: Thresholds = field(default_factory=Thresholds)
    budget: Budget = field(default_factory=Budget)
    effort: str = "low"                 # "low" for subagents, "high" to plan


# --- shared guard questions, asked before every tool call --------------------

GUARD_QUESTIONS: tuple[Question, ...] = (
    Noul("destructive", "Would this action delete or overwrite data that cannot be recovered?"),
    Noul("irreversible", "Is this action irreversible once taken — sent, published, paid, or signed?"),
    Noul("spends", "Does this action spend money or create a financial obligation?"),
    Noul("in_charter", "Is this action clearly within the stated charter of the agent taking it?"),
    Noul("personal_data", "Does this action expose personal or customer data outside the system?"),
)


# --- triage, asked once per incoming task ------------------------------------

def triage_questions(agent_names: Sequence[str]) -> tuple[Question, ...]:
    return (
        Noul("in_scope", "Is this request something the business actually offers?"),
        Noul("safe", "Is this request free of legal, safety, or reputational risk?"),
        Noul("deterministic", "Could this be answered exactly by running code, with no judgement required?"),
        Score("value", "How valuable is completing this request to the business?",
              levels=("negligible", "low", "moderate", "high", "critical")),
        Score("complexity", "How much open-ended reasoning does this require?",
              levels=("none", "little", "moderate", "substantial", "extensive")),
        # Routing is a Choice so a new agent is a config change, not a code change.
        Choice("owner", "Which team should own this request?", options=list(agent_names)),
    )


# --- example roster ----------------------------------------------------------
# Deliver is deliberately left abstract: its acceptance criteria depend on the
# product, and they are what sets the ceiling on autonomy rate.

INTAKE = Subagent(
    name="intake",
    charter=(
        "You qualify inbound requests. Decide whether the business can serve "
        "this request, what it should cost, and what the deliverable is. "
        "You never promise work outside the published catalogue. You never "
        "quote a price below the configured floor. If the request is unclear, "
        "you ask exactly one clarifying question and stop."
    ),
    tools=("crm_lookup", "price_book", "draft_reply"),
    acceptance=(
        Noul("scoped", "Does the reply state a specific, bounded deliverable?"),
        Noul("priced", "Does the reply state a price at or above the price floor?"),
        Noul("no_overpromise", "Is the reply free of commitments the business has not agreed to?"),
        Score("tone", "How well does the reply match a direct, professional voice?",
              levels=("wrong", "off", "acceptable", "good", "excellent")),
    ),
    thresholds=Thresholds(ship=0.95, review=0.75),
)

SUPPORT = Subagent(
    name="support",
    charter=(
        "You answer customer questions and resolve issues within policy. "
        "You may issue a refund up to the configured limit without approval. "
        "You never speculate about what the product will do in future. "
        "If the customer is angry or the issue is novel, you escalate."
    ),
    tools=("kb_search", "order_lookup", "refund", "draft_reply"),
    acceptance=(
        Noul("answers", "Does the reply actually answer what the customer asked?"),
        Noul("accurate", "Is every factual claim in the reply supported by the retrieved context?"),
        Noul("in_policy", "Is any action taken within the stated refund and policy limits?"),
        Noul("no_speculation", "Is the reply free of promises about unreleased functionality?"),
    ),
    thresholds=Thresholds(ship=0.97, review=0.85),
)

ROSTER: dict[str, Subagent] = {a.name: a for a in (INTAKE, SUPPORT)}
