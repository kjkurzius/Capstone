# Mac Mini Agent Architecture

A local-first agent fleet for running a business with one human at the helm.

Status: design + reference implementation. Written 2026-09-21.

---

## 0. The correction that shapes everything

The original plan was "Jev is the manager model for each subagent." Jev cannot
do that job, and understanding why produces a better architecture than the one
we set out to build.

[Jev](https://www.typesafe.ai) (TypeSafe AI, released 2026-09-15) is a **System
One model**. You send it one *state* plus any number of *typed questions*, and
it returns typed decisions with calibrated probabilities. Three primitives:

| Primitive | Question shape | Returns |
|---|---|---|
| `Choice` | pick one from a defined set | choice, per-option probabilities, confidence |
| `Score`  | rate against ordered descriptive levels | score, probabilities, confidence |
| `Noul`   | boolean | probability the answer is yes |

- Endpoint: `POST https://api.typesafe.ai/v1/systemone`, model id `jev-latest`
- Latency: 70–500 ms end to end
- Price: $0.042 / M input tokens, $0.00 / M output
- Questions are evaluated **in parallel and in isolation** against the same
  state, so asking ten questions costs roughly the latency of asking one.
- It **cannot** generate free-form text, write code, draft documents, hold a
  conversation, or plan. Output schemas must be defined in advance — which is
  also why it cannot hallucinate.

A manager agent must decompose a goal, write briefs for its subagents, and
synthesize their results. All three are generation. Jev does none of them.

**So: Claude Opus 5 is the manager. Jev is the nervous system.**

And the better idea: you do not need *one* manager. You need the *manager
function* — decide, route, gate, verify, escalate — applied at every edge of
the graph. Jev is cheap and fast enough to sit on every one of those edges,
which a frontier model is not. That inversion is the whole design.

---

## 1. Three planes

Every operation in the system belongs to exactly one plane. Putting work in the
wrong plane is the most expensive mistake available to you.

### Deterministic plane — plain Python on the Mac mini
Anything with a correct answer: arithmetic, API calls, invoicing, data
transforms, file operations, scheduling, retries, idempotency keys. **Never use
a model where a function will do.** Most of your margin lives here, and so does
all of your reliability.

### Reflex plane — Jev
Every decision whose answer space is known in advance:
route, triage, gate, score, verify, guard, escalate, rank, classify.
70–500 ms, effectively free at your volumes.

### Reasoning plane — Claude Opus 5
Everything that must be *generated*: plans, code, copy, emails, analysis,
synthesis, judgment on novel situations. Slow and expensive relative to the
other two planes, so it is invoked only when the reflex plane says it is needed.

**Target ratio: for every 1 Claude call, expect 5–20 Jev calls and 50+
deterministic operations.** If that ratio inverts, you are burning both money
and latency. Instrument it and watch it.

---

## 2. The control loop — this is "how Jev connects"

Jev is not wired to Claude. Jev is wired to the *harness*, and the harness
decides whether Claude runs at all. One loop, applied identically to every
subagent:

```
  task intake
      │
      ▼
  ┌─────────────────────────────────────────────┐
  │ JEV #1 — TRIAGE  (one call, many questions) │
  │  Choice: which subagent owns this?          │
  │  Choice: which effort tier?                 │
  │  Noul:   is this in scope / safe / legal?   │
  │  Noul:   can the deterministic path do it?  │
  │  Score:  business value                     │
  └─────────────────────────────────────────────┘
      │
      ├── deterministic path available ──► run code, done. No model spend.
      ├── out of scope / unsafe ─────────► reject or escalate to you
      ▼
  ┌─────────────────────────────────────────────┐
  │ CLAUDE WORKER  (Opus 5, streaming, cached)  │
  │  the subagent's charter as system prompt    │
  │  its tool set, its budget                   │
  └─────────────────────────────────────────────┘
      │  before every tool call:
      │  ┌───────────────────────────────────────┐
      │  │ JEV #2 — GUARD                        │
      │  │  Noul: is this call destructive?      │
      │  │  Noul: irreversible? spends money?    │
      │  │  Noul: outside the charter?           │
      │  └───────────────────────────────────────┘
      │     └─ blocks the call BEFORE it executes
      ▼
  ┌─────────────────────────────────────────────┐
  │ JEV #3 — VERIFY (the acceptance criteria)   │
  │  one Noul/Score per criterion, batched      │
  │  → a single calibrated confidence           │
  └─────────────────────────────────────────────┘
      │
      ├── c ≥ θ_ship ──────────────► SHIP. No human involved.
      ├── θ_review ≤ c < θ_ship ───► Claude critic → revise → re-verify (max N)
      └── c < θ_review ────────────► YOUR QUEUE
```

Three properties make this work:

1. **Batch every Jev question about one state into one call.** Because
   questions parallelize against a shared state, ten questions cost about what
   one costs. Never make five sequential Jev calls where one will do.
2. **Jev is on the hot path; Claude is not.** Perceived responsiveness is
   almost entirely "is a frontier model in this request." Most requests should
   resolve in the reflex or deterministic plane and never touch Claude.
3. **Guard before execute, not after.** Jev screens the tool call while it is
   still just a proposal. After execution you are writing an apology, not a
   guard.

---

## 3. θ — the thresholds are the business

This is the part that actually determines whether "$100M with one person" is
even expressible, so it deserves more attention than the orchestration.

Because Jev returns **calibrated** probabilities rather than a model's opinion
of itself, `θ_ship` is a real dial with real semantics. Set it, measure what
gets through, and move it against evidence.

- Start `θ_ship = 0.97`, `θ_review = 0.80`. Deliberately too conservative.
- Log every decision with its probability **and the eventual outcome** —
  refund, complaint, silent acceptance, repeat purchase.
- Weekly, check calibration: of the outputs Jev scored 0.90, did ~90% hold up?
  If yes, lower θ. If no, your acceptance questions are wrong — fix the
  questions, not the threshold.

**Your single most important metric is not revenue. It is _autonomy rate_:**
the fraction of delivered work that cleared `θ_ship` without you. Revenue is
capped at `autonomy rate × your available hours × price`. A business that needs
you at 5% of jobs can be 20x larger than one that needs you at 100%.

Track it weekly, per subagent. It is the only number that tells you whether the
system is getting more capable or just busier.

---

## 4. The Mac mini: what it is and is not

**It is the orchestrator and the system of record. It is not an inference box.**

Both Jev and Claude are hosted APIs. Do not try to run a local 70B as your
manager — it would be slower *and* worse than either, and it would put your
scarcest resource (unified memory) behind your least differentiated need.

What actually earns its place on the box:

| Component | Why local |
|---|---|
| The harness (one supervised process) | Sub-millisecond control decisions, no cold starts |
| Postgres | State, task queue, event log, ledger. Not SQLite — you want real concurrency and WAL durability |
| Queue (Postgres `LISTEN`/`NOTIFY`, or Redis) | One less service if you use Postgres |
| Local embedding model (MLX / Ollama) | The one genuinely good local-inference case: high volume, latency-sensitive, undifferentiated |
| Playwright + Chromium | Browser tools, persistent logged-in sessions |
| macOS Keychain | Secrets, never in `.env` in production |
| Tailscale | Reach the box from anywhere without exposing a port |
| `launchd` with `KeepAlive` | Supervision. Also: `sudo pmset -a sleep 0 disablesleep 1`, and turn off automatic macOS updates |

### The honest constraint

One Mac mini in your home is a single point of failure for a business you want
to take to nine figures. Power, ISP, a macOS update, a failed SSD.

Design so the Mac mini is *where this runs today*, not *what it depends on*:

- All state in Postgres, continuously backed up off-box (Litestream or
  pgBackRest to S3). Test the restore, not just the backup.
- The harness must be **idempotent and resumable from the event log**. Killing
  the process at any point and restarting must be safe. Assume it will happen.
- Keep every Mac-specific dependency (Keychain, `launchd`, MLX) behind an
  interface with a documented Linux fallback. Nothing Mac-only on the critical
  path.
- Rehearse the lift to a Linux VM once, early, while it is cheap.

---

## 5. Cost and margin — where the real risk is

The intuition that agent token spend threatens a 50% margin is worth checking
against the arithmetic, because it points the optimization in the wrong
direction.

At $100M revenue and a 50% net margin you have ~$50M of annual cost budget.
Claude Opus 5 is $5 / M input, $25 / M output. At a blended ~$12/M, spending
even **$10M a year buys roughly 800 billion tokens** — far more agent work than
the business plausibly consumes. Jev, at $0.042/M in and free out, is a rounding
error at any volume.

So inference cost is *not* your margin risk at scale. Your margin risks are:

1. **Forced human labor** — every job that lands in your queue. This is the
   real one, and it is what §3 is about.
2. Payment processing, ~2.9% + fixed, straight off the top.
3. Refunds, chargebacks and rework from autonomous output that was wrong.
4. Liability and compliance from unsupervised action.

**Optimize the architecture for not needing humans and not shipping wrong work
— not for token cost.** The cheapest possible system that requires you to
review everything is worth far less than an expensive one that does not.

One caveat: pre-revenue, token cost genuinely does matter, because your budget
is a fixed number rather than a fraction of revenue. The levers there, in order:
prompt caching (stable system prompt and tool list first, volatile content
last), `effort: low` for subagents, routing to the reflex plane, and Message
Batches at 50% for anything not latency-bound.

### Latency levers, in order of effect

1. Resolve in the deterministic or reflex plane — no frontier call at all.
2. Batch all Jev questions per state into one call.
3. Stream Claude, and let Jev evaluate partial state so you can kill a bad run
   early instead of paying for the whole generation.
4. **Fast mode** (`speed: "fast"`, beta `fast-mode-2026-02-01`, Opus 5) — up to
   2.5x output tokens/sec at $10/$50 per MTok. Worth it only when a human is
   actually waiting. Use standard pricing for background work.
5. Prompt caching — verify with `usage.cache_read_input_tokens`; if it is zero
   across repeated requests something in your prefix is changing.
6. `effort: "low"` on subagents, `high`/`xhigh` on the planner.

---

## 6. The $100M target, stated plainly once

Two things worth knowing, then we build toward it anyway.

**Revenue per employee.** $100M with one person is roughly 10–30x the best
revenue-per-employee figures ever recorded; the strongest performers land around
$3–5M per employee. That is not an argument against the goal — it is an argument
about what the architecture has to be judged on. The right test for every design
decision is: *does this remove the need for the second, tenth, and fiftieth
person?* Build so that adding a person is always possible and never required.

**The binding constraint is not orchestration.** It is distribution, trust, and
support load. An agent fleet that can produce $100M of work is useless without
$100M of demand reaching it, and support volume scales with customers whether or
not delivery is automated. Put agents on distribution and support from day one,
not just delivery.

**What kind of business this architecture favors.** Anything whose output is
**machine-verifiable**, because that is exactly where Jev can gate autonomously
and your autonomy rate can approach 1. Software, data products, structured
back-office work, content with objective specs. Businesses whose quality is
inherently subjective — design, consulting, relationship sales — will push
everything into your review queue and cap the whole system at your personal
hours, no matter how good the orchestration is.

---

## 7. The roster

A subagent is not a prompt. It is a five-tuple:

```python
Subagent(
    charter,             # system prompt: scope, voice, hard limits
    tools,               # least privilege — only what the charter needs
    acceptance,          # list of Jev questions defining "done well"
    thresholds,          # θ_ship, θ_review — per agent, tuned separately
    budget,              # hard caps: tokens, dollars, wall-clock, tool calls
)
```

The elegant part: **the acceptance criteria are the job description.** You
define a role by defining, as typed questions, what good output looks like.
That is also what makes the role measurable and improvable.

Starting roster — six functions, not six clones:

| Agent | Owns | Verifiable? |
|---|---|---|
| **Intake** | Qualify inbound, scope, price, reject | High |
| **Deliver** | The actual product work (domain-specific) | Depends on product — this is the one that decides your ceiling |
| **Verify** | Independent QA against spec; adversarial to Deliver | High |
| **Bill** | Invoice, collect, dun, reconcile | Total — keep this almost entirely deterministic |
| **Support** | Answer, triage, refund within policy | Medium |
| **Growth** | Content, outbound, experiments | Low — expect this one in your queue most |
| **Ledger** | Never acts. Records, reconciles, reports. | Deterministic |

Keep `Verify` structurally independent of `Deliver` — separate context, separate
charter, no shared history. A verifier that saw the work get made is not a
verifier.

---

## 8. Governance — non-negotiable with money moving

Model-judged limits are guidance. These must be **deterministic code**, outside
any model's reach:

- Max spend per task and per day. Hard stop, not a warning.
- No outbound payment above $X without your explicit approval.
- No email/message to more than N recipients without your approval.
- No irreversible action (delete, publish, send, pay, sign) without a Jev guard
  pass *and* a deterministic allowlist check.
- Full append-only event log: every state, question, probability, decision,
  action, and outcome. This is simultaneously your threshold-tuning dataset and
  your evidence if something goes wrong.
- A single kill switch that drains the queue and halts all agents.

---

## 9. Build order

The fastest path is not the one that builds the org chart first.

**Week 1** — Event log, task queue, and *one* subagent end to end, with Jev
verify and a real `θ`. Ship something to one real customer.

**Week 2** — The escalation queue and your review interface. This is your
actual interface to the business; build it before you build more agents.

**Week 3** — Second subagent. Start measuring autonomy rate per agent.

**After that** — Add an agent only when the autonomy rate on the existing ones
is stable and rising. An unstable agent added to an unstable fleet does not
compound, it multiplies the queue.

**The anti-pattern:** building all six agents before one of them has shipped
verified work to a paying customer. Every hour spent on the org chart before
then is an hour spent on a guess.

---

## 10. Open items

- **Jev wire format.** The endpoint, model id, primitives, latency and pricing
  above are confirmed from launch coverage, but TypeSafe's own docs were not
  reachable from this environment, so the exact JSON field names in
  `agentstack/jev.py` are a best-effort adapter. Confirm against
  `docs.typesafe.ai` and fix in one place — the adapter is isolated for exactly
  this reason.
- **Early access.** Jev is in early access; confirm rate limits and any
  concurrency ceiling before putting it on the hot path of every request.
- **No local Jev.** No on-device deployment is offered, so the reflex plane is a
  network dependency. Decide the degraded-mode behavior: fail closed (escalate
  everything to you) is the safe default and is what the reference
  implementation does.
- **Product choice** drives the `Deliver` agent and therefore the ceiling on
  autonomy rate. Unresolved, and the most important open question here.
