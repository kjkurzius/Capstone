"""Escalation — the only upward edge in the system.

This is a product with exactly one user. Its failure mode is not too few
items, it is too many: past roughly ten a day you start rubber-stamping, and
a queue you rubber-stamp is worse than no queue because it manufactures the
appearance of oversight.

Every item therefore carries a recommended default and a deadline, so silence
is safe rather than blocking — except for COMMITMENT, which never times out
because it is the boundary of what an agent may do in your name.
"""

from __future__ import annotations

import enum
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class Klass(enum.StrEnum):
    """Why code could not decide this itself."""

    NOVEL = "novel"                    # no policy covers this situation
    LOW_CONFIDENCE = "low_confidence"  # policy covers it; Jev is unsure
    THRESHOLD = "threshold"            # a deterministic limit was hit
    CONFLICT = "conflict"              # agents or signals disagree
    COMMITMENT = "commitment"          # binds the company. Always the human.


# SLA in seconds, and what happens when it expires.
#
# THRESHOLD denies on timeout: money never moves because nobody looked.
# COMMITMENT has no deadline at all — no confidence level and no configuration
# makes it expire. Everything else in this system is a dial; this is not.
POLICY: dict[Klass, tuple[float | None, str]] = {
    Klass.NOVEL:          (8 * 3600, "hold"),
    Klass.LOW_CONFIDENCE: (8 * 3600, "hold"),
    Klass.THRESHOLD:      (4 * 3600, "deny"),
    Klass.CONFLICT:       (8 * 3600, "hold"),
    Klass.COMMITMENT:     (None,     ""),
}

# Classes that should shrink as you write policy and tune thresholds. If these
# are not falling month over month, the system is running but not learning.
DECAYING = frozenset({Klass.NOVEL, Klass.LOW_CONFIDENCE, Klass.CONFLICT})

# Delivered the moment they are raised rather than in the next digest.
INTERRUPTS = frozenset({Klass.COMMITMENT, Klass.THRESHOLD})


class EscalationError(RuntimeError):
    pass


@dataclass
class Escalation:
    """One decision, stated as a counterfactual rather than a question.

    `proposed` is what happens on timeout; `alternative` is the other option.
    Phrasing it as "I will do X unless you say otherwise" is what makes the
    item answerable in one tap instead of requiring an investigation.
    """

    task_id: str
    klass: Klass
    summary: str                        # one line, readable on a phone
    proposed: str                       # what happens if you say nothing
    alternative: str                    # the other option
    evidence: dict[str, Any] = field(default_factory=dict)
    agent: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    raised_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.klass is Klass.COMMITMENT and self.proposed:
            # A COMMITMENT with a default would execute on silence, which is
            # exactly the thing this class exists to prevent.
            raise EscalationError("COMMITMENT cannot carry a default action")
        if self.klass is not Klass.COMMITMENT and not self.proposed:
            raise EscalationError(f"{self.klass} needs a default; silence must resolve")

    @property
    def deadline(self) -> float | None:
        sla, _ = POLICY[self.klass]
        return None if sla is None else self.raised_at + sla

    def expired(self, now: float | None = None) -> bool:
        d = self.deadline
        return d is not None and (now or time.time()) >= d

    def render(self) -> str:
        """What lands in Slack. Decidable without opening anything else."""
        lines = [f"*[{self.klass.upper()}]* {self.summary}"]
        if self.agent:
            lines.append(f"_agent:_ {self.agent}  ·  _task:_ `{self.task_id}`")
        for k, v in self.evidence.items():
            lines.append(f"  • {k}: {v}")
        if self.klass is Klass.COMMITMENT:
            lines.append("*Waiting on you. This one does not time out.*")
        else:
            hours = POLICY[self.klass][0] / 3600
            lines.append(f"→ *{self.proposed}* in {hours:.0f}h unless you reply")
            lines.append(f"   reply `{self.id} alt` for: {self.alternative}")
        return "\n".join(lines)


SCHEMA = """
CREATE TABLE IF NOT EXISTS escalations (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    klass       TEXT NOT NULL,
    agent       TEXT,
    summary     TEXT NOT NULL,
    proposed    TEXT NOT NULL,
    alternative TEXT NOT NULL,
    evidence    TEXT NOT NULL,
    raised_at   REAL NOT NULL,
    notified_at REAL,
    resolved_at REAL,
    resolution  TEXT,          -- proposed | alternative | custom | timeout
    note        TEXT
);
CREATE INDEX IF NOT EXISTS esc_open ON escalations(resolved_at, klass);
"""


class Queue:
    def __init__(self, path: str | Path = "escalations.db", *, daily_budget: int = 10):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()
        # Not a rate limit — a health check. Blowing through it means the
        # system is broken, and that is worth knowing loudly.
        self._daily_budget = daily_budget

    def raise_(self, esc: Escalation) -> Escalation:
        self._db.execute(
            "INSERT INTO escalations (id, task_id, klass, agent, summary, proposed, "
            "alternative, evidence, raised_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (esc.id, esc.task_id, str(esc.klass), esc.agent, esc.summary,
             esc.proposed, esc.alternative, json.dumps(esc.evidence, default=str),
             esc.raised_at),
        )
        self._db.commit()
        return esc

    def over_budget(self, now: float | None = None) -> int:
        """How far past the daily budget we are. Positive means escalate the
        escalation: something upstream is wrong."""
        since = (now or time.time()) - 86400
        n = self._db.execute(
            "SELECT COUNT(*) FROM escalations WHERE raised_at >= ?", (since,)
        ).fetchone()[0]
        return max(0, n - self._daily_budget)

    def open_items(self) -> list[Escalation]:
        rows = self._db.execute(
            "SELECT id, task_id, klass, agent, summary, proposed, alternative, "
            "evidence, raised_at FROM escalations WHERE resolved_at IS NULL "
            "ORDER BY raised_at"
        ).fetchall()
        return [
            Escalation(
                id=r[0], task_id=r[1], klass=Klass(r[2]), agent=r[3] or "",
                summary=r[4], proposed=r[5], alternative=r[6],
                evidence=json.loads(r[7]), raised_at=r[8],
            )
            for r in rows
        ]

    def unnotified(self) -> list[Escalation]:
        """Open items not yet delivered. Keeps a digest from repeating itself."""
        ids = {r[0] for r in self._db.execute(
            "SELECT id FROM escalations WHERE resolved_at IS NULL "
            "AND notified_at IS NULL").fetchall()}
        return [e for e in self.open_items() if e.id in ids]

    def mark_notified(self, esc_id: str, now: float | None = None) -> None:
        self._db.execute("UPDATE escalations SET notified_at=? WHERE id=?",
                         (now or time.time(), esc_id))
        self._db.commit()

    def resolve(self, esc_id: str, resolution: str, note: str = "") -> None:
        if resolution not in ("proposed", "alternative", "custom", "timeout"):
            raise EscalationError(f"unknown resolution {resolution!r}")
        cur = self._db.execute(
            "UPDATE escalations SET resolved_at=?, resolution=?, note=? "
            "WHERE id=? AND resolved_at IS NULL",
            (time.time(), resolution, note, esc_id),
        )
        self._db.commit()
        if cur.rowcount == 0:
            raise EscalationError(f"{esc_id} is unknown or already resolved")

    def sweep(self, now: float | None = None) -> list[Escalation]:
        """Resolve everything whose deadline has passed, by its default.

        Returns what expired so the caller can execute those defaults. Run it
        on a timer. COMMITMENT is structurally excluded: it has no deadline,
        so `expired()` is always False for it.
        """
        fired = [e for e in self.open_items() if e.expired(now)]
        for e in fired:
            self.resolve(e.id, "timeout", f"default applied: {e.proposed}")
        return fired

    # --- the metric ---------------------------------------------------------

    def volume(self, days: int = 30, now: float | None = None) -> dict[str, int]:
        since = (now or time.time()) - days * 86400
        return dict(self._db.execute(
            "SELECT klass, COUNT(*) FROM escalations WHERE raised_at >= ? "
            "GROUP BY klass", (since,)
        ).fetchall())

    def is_learning(self, now: float | None = None) -> bool:
        """True when the decaying classes shrank versus the prior 30 days.

        THRESHOLD and COMMITMENT are permanent and excluded. If this returns
        False for two months running, you are answering the same questions
        over and over and none of the answers became policy.
        """
        now = now or time.time()
        recent = self._count(now - 30 * 86400, now)
        prior = self._count(now - 60 * 86400, now - 30 * 86400)
        return recent < prior if prior else True

    def _count(self, start: float, end: float) -> int:
        marks = ",".join("?" * len(DECAYING))
        return self._db.execute(
            f"SELECT COUNT(*) FROM escalations WHERE raised_at >= ? AND "
            f"raised_at < ? AND klass IN ({marks})",
            (start, end, *(str(k) for k in DECAYING)),
        ).fetchone()[0]


def digest(items: list[Escalation], over_budget: int = 0) -> str:
    """The twice-daily batch. Interrupts go out on their own, immediately."""
    if not items and not over_budget:
        return "No open escalations."
    out: list[str] = []
    if over_budget:
        out.append(f":rotating_light: *{over_budget} over the daily escalation "
                   f"budget.* The system is producing too many decisions — "
                   f"treat that as the problem, not the backlog.\n")
    blocking = [e for e in items if e.klass is Klass.COMMITMENT]
    rest = [e for e in items if e.klass is not Klass.COMMITMENT]
    if blocking:
        out.append(f"*Waiting on you ({len(blocking)})*")
        out += [e.render() for e in blocking]
    if rest:
        out.append(f"\n*Will resolve themselves ({len(rest)})*")
        out += [e.render() for e in rest]
    return "\n\n".join(out)
