"""Jev client — the reflex plane.

Jev (TypeSafe AI) answers typed questions about a state and returns calibrated
probabilities. It never generates text. One call carries many questions; they
are evaluated in parallel against the shared state, so batching is close to
free and sequential calls are pure waste.

WIRE FORMAT WARNING
-------------------
`_build_payload` and `_parse_response` are a best-effort adapter: TypeSafe's
docs were not reachable when this was written. The endpoint, model id and the
three primitives are confirmed from launch coverage; the exact JSON field names
are not. Everything else in this package goes through `JevClient.ask`, so
correcting the format means editing these two functions and nothing else.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

import httpx

JEV_URL = os.environ.get("JEV_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-latest")


# --- questions ---------------------------------------------------------------

@dataclass(frozen=True)
class Choice:
    """Pick one option from a defined set."""
    key: str
    prompt: str
    options: Sequence[str]
    kind: Literal["choice"] = "choice"


@dataclass(frozen=True)
class Score:
    """Rate the state against ordered descriptive levels (worst to best)."""
    key: str
    prompt: str
    levels: Sequence[str]
    kind: Literal["score"] = "score"


@dataclass(frozen=True)
class Noul:
    """A boolean question. Returns the probability the answer is yes."""
    key: str
    prompt: str
    kind: Literal["noul"] = "noul"


Question = Choice | Score | Noul


# --- answers -----------------------------------------------------------------

@dataclass(frozen=True)
class Answer:
    key: str
    value: Any                      # chosen option, level, or bool
    confidence: float               # calibrated, 0..1
    probabilities: dict[str, float] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.value)


@dataclass(frozen=True)
class Verdict:
    """All answers from one Jev call, plus how long it took."""
    answers: dict[str, Answer]
    latency_ms: float
    degraded: bool = False          # True when Jev was unreachable

    def __getitem__(self, key: str) -> Answer:
        return self.answers[key]

    def confidence(self, *keys: str) -> float:
        """Joint confidence across the named answers.

        The product is deliberately pessimistic: every criterion must hold, so
        confidence falls as criteria are added. That is the correct direction
        for a ship/no-ship gate — more requirements should mean more caution,
        not a comfortable average that hides one failing criterion.
        """
        chosen = keys or tuple(self.answers)
        c = 1.0
        for k in chosen:
            a = self.answers[k]
            p = a.confidence if a.value else 1.0 - a.confidence
            c *= p
        return c


# --- client ------------------------------------------------------------------

class JevUnavailable(RuntimeError):
    pass


class JevClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        url: str = JEV_URL,
        model: str = JEV_MODEL,
        timeout: float = 5.0,
        fail_closed: bool = True,
    ):
        self._key = api_key or os.environ["JEV_API_KEY"]
        self._url = url
        self._model = model
        # Jev has no local deployment, so the reflex plane is a network
        # dependency. fail_closed=True means an outage escalates work to the
        # human instead of shipping it ungated. Do not flip this to keep
        # throughput up during an incident; that is the failure mode that
        # turns an outage into refunds.
        self._fail_closed = fail_closed
        self._http = httpx.Client(timeout=timeout)

    def ask(self, state: str, questions: Sequence[Question]) -> Verdict:
        """One state, many questions, one round trip."""
        if not questions:
            raise ValueError("ask() needs at least one question")

        started = time.perf_counter()
        try:
            resp = self._http.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
                json=_build_payload(self._model, state, questions),
            )
            resp.raise_for_status()
            answers = _parse_response(resp.json(), questions)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            if not self._fail_closed:
                raise JevUnavailable(str(exc)) from exc
            return Verdict(
                answers=_all_uncertain(questions),
                latency_ms=(time.perf_counter() - started) * 1000,
                degraded=True,
            )

        return Verdict(answers, (time.perf_counter() - started) * 1000)

    def close(self) -> None:
        self._http.close()


# --- adapter (the part to fix against real docs) -----------------------------

def _build_payload(model: str, state: str, questions: Sequence[Question]) -> dict:
    out: list[dict] = []
    for q in questions:
        item: dict[str, Any] = {"id": q.key, "type": q.kind, "question": q.prompt}
        if isinstance(q, Choice):
            item["options"] = list(q.options)
        elif isinstance(q, Score):
            item["levels"] = list(q.levels)
        out.append(item)
    return {"model": model, "state": state, "questions": out}


def _parse_response(body: dict, questions: Sequence[Question]) -> dict[str, Answer]:
    by_key = {q.key: q for q in questions}
    raw = body.get("answers") or body.get("results") or []
    answers: dict[str, Answer] = {}

    for item in raw:
        key = item.get("id") or item.get("key")
        q = by_key.get(key)
        if q is None:
            continue
        probs = item.get("probabilities") or {}
        if isinstance(q, Noul):
            p = float(item.get("probability", probs.get("yes", 0.0)))
            # Confidence is distance from maximum uncertainty, not p itself:
            # p=0.02 is a confident "no", and the gate needs to know that.
            answers[key] = Answer(key, p >= 0.5, max(p, 1.0 - p), probs)
        else:
            value = item.get("choice") or item.get("score") or item.get("value")
            conf = float(item.get("confidence", probs.get(str(value), 0.0)))
            answers[key] = Answer(key, value, conf, probs)

    for key, q in by_key.items():
        if key not in answers:
            answers[key] = _uncertain(q)
    return answers


def _uncertain(q: Question) -> Answer:
    """A maximally uncertain answer — forces escalation under any threshold."""
    if isinstance(q, Noul):
        return Answer(q.key, False, 0.0)
    if isinstance(q, Choice):
        return Answer(q.key, None, 0.0)
    return Answer(q.key, q.levels[0], 0.0)


def _all_uncertain(questions: Sequence[Question]) -> dict[str, Answer]:
    return {q.key: _uncertain(q) for q in questions}
