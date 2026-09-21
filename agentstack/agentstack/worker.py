"""Claude worker — the reasoning plane.

Claude Opus 5 runs the subagent: it plans, generates, and calls tools. Every
tool call it proposes is screened by Jev and then by the deterministic governor
before it executes, so this module never acts on its own authority.

Streaming is on so the harness can watch partial state and kill a bad run
early rather than paying for the whole generation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

MODEL = "claude-opus-5"

# Blended estimate for budget enforcement only; bill from usage, not from this.
USD_PER_INPUT_TOKEN = 5.00 / 1_000_000
USD_PER_OUTPUT_TOKEN = 25.00 / 1_000_000


@dataclass
class Run:
    text: str = ""
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    stopped_for: str = ""               # "done" | "budget" | "guard" | "refusal"
    blocked: list[str] = field(default_factory=list)

    @property
    def usd(self) -> float:
        return (self.input_tokens * USD_PER_INPUT_TOKEN
                + self.output_tokens * USD_PER_OUTPUT_TOKEN)


class ClaudeWorker:
    def __init__(self, client: anthropic.Anthropic | None = None, *, fast: bool = False):
        self._c = client or anthropic.Anthropic()
        # Fast mode is up to 2.5x output tokens/sec at $10/$50 per MTok. Worth
        # it only when a person is actually waiting for this reply.
        self._fast = fast

    def run(
        self,
        agent,                                  # roster.Subagent
        task: str,
        tool_schemas: list[dict],
        execute: Callable[[str, dict], Any],    # runs a call the guards cleared
        gate: Callable[[str, dict], str | None],  # returns a refusal reason, or None
    ) -> Run:
        started = time.perf_counter()
        run = Run()
        messages: list[dict] = [{"role": "user", "content": task}]

        kwargs: dict[str, Any] = {
            "model": MODEL,
            "max_tokens": 16_000,
            # Stable prefix first so the cache survives across every task this
            # agent handles; the volatile task text stays in messages.
            "system": [{
                "type": "text",
                "text": agent.charter,
                "cache_control": {"type": "ephemeral"},
            }],
            "tools": tool_schemas,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": agent.effort},
            # Opus 5 can decline with stop_reason "refusal"; server-side
            # fallbacks route by category so a decline does not become an outage.
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
        }
        if self._fast:
            kwargs["speed"] = "fast"
            kwargs["betas"] = kwargs["betas"] + ["fast-mode-2026-02-01"]

        while True:
            if run.usd > agent.budget.max_usd:
                run.stopped_for = "budget"
                break
            if time.perf_counter() - started > agent.budget.max_seconds:
                run.stopped_for = "budget"
                break
            if run.tool_calls >= agent.budget.max_tool_calls:
                run.stopped_for = "budget"
                break

            with self._c.beta.messages.stream(messages=messages, **kwargs) as stream:
                message = stream.get_final_message()

            run.input_tokens += message.usage.input_tokens
            run.output_tokens += message.usage.output_tokens

            if message.stop_reason == "refusal":
                run.stopped_for = "refusal"
                break

            # Append the whole content list, not just the text: thinking blocks
            # and compaction state have to survive the round trip intact.
            messages.append({"role": "assistant", "content": message.content})

            uses = [b for b in message.content if b.type == "tool_use"]
            if not uses:
                run.text = "".join(b.text for b in message.content if b.type == "text")
                run.stopped_for = "done"
                break

            # Every tool result for one assistant turn goes back in a SINGLE
            # user message. Splitting them teaches Claude to stop parallelising.
            results = []
            for use in uses:
                run.tool_calls += 1
                refusal = gate(use.name, use.input)
                if refusal:
                    run.blocked.append(f"{use.name}: {refusal}")
                    results.append({
                        "type": "tool_result", "tool_use_id": use.id,
                        "is_error": True,
                        "content": f"Blocked before execution: {refusal}",
                    })
                    continue
                try:
                    out = execute(use.name, use.input)
                    results.append({
                        "type": "tool_result", "tool_use_id": use.id,
                        "content": json.dumps(out, default=str),
                    })
                except Exception as exc:
                    results.append({
                        "type": "tool_result", "tool_use_id": use.id,
                        "is_error": True, "content": str(exc),
                    })

            messages.append({"role": "user", "content": results})

        run.seconds = time.perf_counter() - started
        return run
