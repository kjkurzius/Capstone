"""Deterministic limits — outside any model's reach.

Jev's guard pass is the fast screen. These are the hard stops. A model that has
been talked into something still cannot get past this file, because this file
does not take input from a model.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


class GuardViolation(RuntimeError):
    """Raised instead of taking the action. Always escalates, never retries."""


@dataclass
class Limits:
    max_usd_per_task: float = 2.00
    max_usd_per_day: float = 200.00
    max_payment_without_approval_usd: float = 0.00   # default: no autonomous payment
    max_recipients_without_approval: int = 1
    irreversible_tools: frozenset[str] = frozenset(
        {"send_email", "publish", "pay", "sign", "delete", "refund"}
    )


@dataclass
class Governor:
    limits: Limits = field(default_factory=Limits)
    _spend_today: float = 0.0
    _day: dt.date = field(default_factory=dt.date.today)
    halted: bool = False

    # --- the kill switch ---
    def halt(self, reason: str) -> None:
        self.halted = True
        self._halt_reason = reason

    def check_running(self) -> None:
        if self.halted:
            raise GuardViolation(f"fleet halted: {getattr(self, '_halt_reason', 'unspecified')}")

    # --- spend ---
    def _roll_day(self) -> None:
        today = dt.date.today()
        if today != self._day:
            self._day, self._spend_today = today, 0.0

    def record_spend(self, usd: float) -> None:
        self._roll_day()
        self._spend_today += usd
        if self._spend_today > self.limits.max_usd_per_day:
            self.halt(f"daily spend ${self._spend_today:.2f} exceeded cap")
            raise GuardViolation("daily spend cap exceeded")

    def check_task_spend(self, usd: float, cap: float) -> None:
        if usd > cap:
            raise GuardViolation(f"task spend ${usd:.2f} exceeded its ${cap:.2f} budget")

    # --- actions ---
    def check_tool_call(self, agent: str, tool: str, args: dict, allowed: frozenset[str]) -> None:
        self.check_running()

        if tool not in allowed:
            raise GuardViolation(f"{agent} is not permitted to call {tool}")

        amount = float(args.get("amount_usd") or args.get("amount") or 0.0)
        if amount > self.limits.max_payment_without_approval_usd:
            raise GuardViolation(
                f"{tool} would move ${amount:.2f}; approval limit is "
                f"${self.limits.max_payment_without_approval_usd:.2f}"
            )

        recipients = args.get("to") or args.get("recipients") or []
        if isinstance(recipients, str):
            recipients = [recipients]
        if len(recipients) > self.limits.max_recipients_without_approval:
            raise GuardViolation(
                f"{tool} targets {len(recipients)} recipients; limit is "
                f"{self.limits.max_recipients_without_approval}"
            )
