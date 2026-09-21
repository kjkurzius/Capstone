# agentstack

A Jev-gated, Claude-powered agent fleet designed to run on one Mac mini with
one human at the helm.

Design rationale, cost model and build order: [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

## The one-paragraph version

Jev cannot be the manager — it returns typed decisions with calibrated
probabilities and cannot generate text, so it cannot plan, brief a subagent, or
synthesize a result. Claude Opus 5 is the manager. Jev is the nervous system:
it sits on *every* edge of the graph — triage, routing, pre-execution guards,
output verification — because at 70–500 ms and $0.042/M input it is cheap
enough to, and a frontier model is not. The harness decides whether Claude runs
at all, which is what makes the system fast.

## Layout

| File | Plane | Job |
|---|---|---|
| `agentstack/jev.py` | reflex | Jev client. `Choice` / `Score` / `Noul`, batched into one call |
| `agentstack/worker.py` | reasoning | Claude Opus 5: streaming, cached prefix, per-agent effort and budget |
| `agentstack/guards.py` | deterministic | Hard limits no model can talk its way past |
| `agentstack/control.py` | — | The loop: triage → guard → verify → ship/revise/escalate |
| `agentstack/roster.py` | — | Subagents as charter + tools + acceptance criteria + θ + budget |
| `agentstack/ledger.py` | — | Append-only event log, autonomy rate, calibration |

## Run the tests

Both planes are faked, so this costs nothing and needs no keys:

```bash
pip install -e ".[dev]"
pytest tests/ -q
```

They cover the four dispositions that matter: ship above θ, escalate below the
review floor, bounded revision in the middle band, and fail-closed when Jev is
unreachable (which must not cost any model spend).

## Before it touches real money

1. **Fix the Jev wire format.** `_build_payload` / `_parse_response` in
   `jev.py` are a best-effort adapter — TypeSafe's docs were unreachable when
   this was written. Everything routes through `JevClient.ask`, so this is a
   two-function change.
2. **SQLite → Postgres.** The schema ports unchanged. You want concurrent
   writers and WAL durability, plus continuous off-box backup.
3. **Keys into the macOS Keychain**, not `.env`.
4. **Set the governor's real limits.** `max_payment_without_approval_usd`
   defaults to `0.00` on purpose. Raise it deliberately or not at all.
5. **Fill in `outcome`** on the `decisions` table from refunds and complaints.
   Until you do, `calibration()` returns nothing and every θ is a guess.

## The metric

```python
ledger.autonomy_rate("support")   # fraction shipped with no human involved
ledger.calibration()              # is Jev's 0.90 actually 90%?
```

Revenue is capped at `autonomy rate × your hours × price`. Watch autonomy rate
weekly, per agent — it tells you whether the fleet is getting more capable or
just busier.
