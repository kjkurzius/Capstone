# The Plan

Model topology, escalation design, and the first 90 days.

Companion to `ARCHITECTURE.md`. Written 2026-09-21.

---

## Part 1 — Parent/child: there almost isn't one

The instinct is a hierarchy of models: a manager model that briefs worker
models, which brief sub-workers. **Don't build that.** It is the single most
common way agent systems fail, and there is now evidence.

Anthropic's [Project Vend](https://www.anthropic.com/research/project-vend-2)
ran Claude as an actual business. Phase one lost its entire $1,000 float.
Phase two became consistently profitable, and one of the three changes that
did it was that **single-purpose agents with clear boundaries outperformed
general-purpose agents with broad mandates**. Separately, ~88% of autonomous
agent pilots fail before production, attributed to governance and
observability gaps rather than model quality.

### Why model-to-model delegation fails

- **Errors compound multiplicatively.** Two 95% steps in series is 90%. Four
  is 81%. Depth is the enemy.
- **Blame becomes unattributable.** When output is wrong, you cannot tell
  whether the parent briefed badly or the child executed badly. You lose the
  ability to fix the actual cause.
- **Context pollutes downward.** A child inherits the parent's reasoning as
  premises, so it cannot catch the parent's mistake — it is standing on it.
- **A brief cannot be verified before it is acted on**, except by another
  model, which is the same problem one level up.
- **Cost and latency stack** with no corresponding gain in quality.

### The rule

> **The parent is code. Models are leaves. Maximum depth is 1.**

```
                        YOU  (principal — the only authority)
                         ▲
                         │  escalation: typed, batched, defaulted
                         │
                    ┌────┴─────────────────────────────┐
                    │   HARNESS  (plain Python)        │
                    │   the actual parent              │
                    │   owns: order, context, budget,  │
                    │   retries, policy, the record    │
                    └──┬──────────┬──────────┬─────────┘
                       │          │          │
                  ┌────▼───┐ ┌────▼────┐ ┌───▼────┐
                  │  Jev   │ │ Planner │ │ Worker │   ← siblings, never
                  │ sensor │ │ Opus 5  │ │ Opus 5 │     parent and child
                  └────────┘ └────┬────┘ └───┬────┘
                                  │          │
                             plan as DATA   output
                             (validated     (verified by a
                              by code)       sibling verifier)
```

Three relationship types, and only the last is vertical:

**1. Orchestration — code calls model.** 90% of the system. The harness picks
what runs, in what order, with what context and what budget. No model ever
calls another model. Already implemented in `control.py`.

**2. Adversarial pairing — model ↔ model, mediated.** Deliver and Verify are
**siblings, not parent and child**, and they never share context. The verifier
sees charter + task + output. It never sees the deliverer's reasoning, because
a verifier that watched the work get made inherits the same errors and will
wave them through. The harness sits between them; neither can address the
other.

**3. Escalation — model → code → you.** The only upward edge, and Part 2.

### The one place delegation is warranted

Novel, multi-step tasks need decomposition, and that is genuine reasoning. So
there is a **Planner**: one Opus 5 call at `effort: "high"` that breaks a task
into bounded steps.

But the critical distinction:

> **The planner emits a plan, not instructions.**

It returns a structured, typed object — a DAG of steps, each naming an agent,
a tool set, acceptance criteria and a budget. Code validates that object
against the roster and the governor *before* anything executes: unknown agent,
unpermitted tool, budget overrun, or cycle in the graph all reject the plan
without running a thing. Then the harness executes the steps itself.

The planner never hands a prompt to a worker. It proposes; code disposes.
That single constraint is what keeps the system auditable, because every
executed step traces to a validated plan rather than to something a model
said to another model.

### Jev is a sensor, not a node

Worth stating explicitly, because it matters legally as much as technically:
**Jev has no authority.** It produces numbers. Code applies policy to those
numbers and produces decisions.

If Jev "decides," it sits in your liability chain in a way that is hard to
defend. If Jev *measures* and a documented, versioned policy acts on the
measurement, you have a defensible process with an audit trail — which is the
same artifact your customers' security teams and your insurer want to see.

### Depth limit is a hard rule

A worker cannot spawn a worker. If a step turns out to need decomposition, it
returns to the planner, which emits more steps into the queue. The runtime
shape is therefore **a flat queue driven by a validated plan, not a tree**.
Flat queues are debuggable, resumable, and can be reasoned about when they go
wrong at 3am. Trees cannot.

---

## Part 2 — Escalation

### The thing to understand first

Escalation is a product, and it has exactly one user: you.

Its failure mode is **not** too few escalations. It is too many — because past
a certain volume you will start rubber-stamping, and a queue you rubber-stamp
is strictly worse than no queue, since it manufactures the appearance of
oversight while providing none. Design for a small number of real decisions,
not for coverage.

Five rules:

1. **Bounded volume.** Target ≤10 per day. Exceeding it means the *system* is
   broken, not that you are behind. Overflow is itself an alert.
2. **Every item carries a recommended default and a deadline.** Silence
   executes the default. Without this you are the bottleneck and the autonomy
   thesis dies. **The default is never the irreversible option.**
3. **Typed classes, not one queue.** Different classes need different SLAs,
   different defaults, and different handling.
4. **Batched**, at fixed times. Not interrupt-per-item. One class is exempt.
5. **Decidable in one tap.** If answering requires you to go investigate, the
   escalation was malformed. It must carry the evidence with it.

And the framing that makes items fast to answer: each one states a
counterfactual, not a question. Not *"should I refund this?"* but *"I will
refund $240 in 4 hours unless you say otherwise. Here is why. Here is what I
would do instead."*

### The five classes

Distinguished by *why* code could not decide:

| Class | Trigger | Default on timeout | SLA | Decays? |
|---|---|---|---|---|
| **NOVEL** | No policy covers this situation | hold | 8h | **Yes** — should approach zero |
| **LOW_CONFIDENCE** | Policy covers it; Jev is unsure | hold | 8h | **Yes** — as θ calibrates |
| **THRESHOLD** | A deterministic limit was hit | **deny** | 4h | No — permanent |
| **CONFLICT** | Agents or signals disagree; verifier keeps failing | hold | 8h | **Yes** — means a spec bug |
| **COMMITMENT** | Anything that binds the company | **none — never times out** | ∞ | **No — permanent** |

**COMMITMENT is not a tuning parameter.** Contracts, prices below floor,
refunds above limit, claims made to a customer, public statements, anything
legally binding. No default, no timeout, no exception, at any confidence
level. You are the principal; this class is the boundary of what an agent may
do in your name. Everything else in this system is adjustable. This is not.

**NOVEL produces a policy, not a decision.** When you answer one, you are
writing a rule so that case never escalates again. If you find yourself
answering the same NOVEL twice, the first answer failed to become policy.

### The metric

`ARCHITECTURE.md` §3 says to watch autonomy rate. This is its complement:

> **If escalation volume in the decaying classes is not falling month over
> month, the system is not learning. It is just running.**

Three of five classes should shrink toward zero. Two should not. If THRESHOLD
and COMMITMENT are all that is left, the system is finished — and that is a
reachable end state, not an aspiration.

### Delivery

Answerable by replying to a message. Slack or SMS, not a web app you have to
log into — a dashboard you must visit is a dashboard you will stop visiting.
Two scheduled digests a day, plus immediate delivery for COMMITMENT and for
any THRESHOLD involving money.

---

## Part 3 — The first 90 days

Against the $75k, one operator, no employees.

### Gate 0 — before any code (week 0)

Non-negotiable, and mostly not technical:

- [ ] Entity, EIN, registered agent, business bank account
- [ ] **Settle the Asset Panda question in writing** — IP assignment, non-compete, conflict of interest. Do this before writing a line of product code, not after.
- [ ] E&O + cyber insurance bound
- [ ] MSA, contingency agreement, ToS, DPA drafted by an actual lawyer
- [ ] CPA engaged, books open

*Budget: ~$26k. Nothing after this point works without it.*

### Weeks 1–3 — one agent, end to end

- Pick the niche. File-based, contingency-priced, no customer credentials.
- Postgres, not SQLite. Off-box backup. Restore tested — the backup is not the thing, the restore is.
- One agent: intake → verify → deliver. `θ_ship` at 0.97, deliberately too tight.
- Escalation queue live in Slack from day one, before any customer exists.
- **Gate:** one real deliverable, produced end to end, verified, that you would send to a paying customer.

### Weeks 4–6 — first revenue

- Three to five prospects. Hand-sold, by you. No agent does this yet.
- **One paying customer. This is the only thing that matters in this phase.**
- Start recording the `outcome` column. Nothing else about calibration works without it.
- **Gate:** money received, and the work that earned it cleared θ without your intervention.

### Weeks 7–9 — prove the loop, not the scale

- First calibration check. Does Jev's 0.90 mean 90%? If not, fix the acceptance criteria — do **not** lower θ.
- Convert every NOVEL escalation from weeks 1–6 into policy.
- Add the second agent only if autonomy rate on the first is stable and rising.
- **Gate:** autonomy rate ≥ 60% on the first agent, escalation volume falling.

### Weeks 10–13 — decide, honestly

Three to five customers. Enough data to answer one question:

**Is autonomy rate rising, flat, or falling?**

- **Rising** → the thesis holds. Scale the niche, plan SOC 2 from revenue.
- **Flat** → the work is less verifiable than it looked. Change the niche, keep the machine.
- **Falling** → you have built a queue with extra steps. Stop and rethink before spending more.

### Budget

| | |
|---|---|
| Legal, entity, contracts | $15k |
| Insurance | $6k |
| CPA, bookkeeping, tax | $5k |
| Infra + inference, 12 months | $20k |
| Reserve | $15k |
| Go-to-market | $14k |

No SOC 2 in year one — see `ARCHITECTURE.md`. It is a scaling cost funded by
revenue, and the signal to start is the *second* customer who asks, not the
first.

### What kills this

Ranked by probability, not by drama:

1. **No demand.** The agents work and nobody wants the output. By far the most likely. Weeks 4–6 exist to find this out cheaply.
2. **The work is less verifiable than it looked.** Autonomy rate plateaus low, you become the review queue, and the whole thesis collapses into a job.
3. **A confidently wrong output at volume**, before anyone notices. This is what θ, the guards and the ledger are for.
4. **You spend the budget on infrastructure before revenue.** Including on SOC 2.
5. **Escalation fatigue** — the queue grows, you rubber-stamp, oversight becomes theatre.

Only #3 is a technical problem. The architecture cannot save you from the
other four, and pretending otherwise is how the $75k disappears.
