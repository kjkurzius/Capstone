-- agentstack schema (PostgreSQL 16+)
--
-- Port of the SQLite schemas in ledger.py and escalation.py. Epoch columns
-- stay `double precision` so the application code is unchanged; switch them to
-- timestamptz later if you want range queries, and adjust the readers then.
--
-- Two things here are load-bearing and have no SQLite equivalent:
--
--   1. The event log is append-only, enforced by GRANT. The app role has no
--      UPDATE or DELETE on `events` at all. An audit trail the application can
--      rewrite is not an audit trail, and this is the artifact your insurer and
--      your customers' security reviews will ask to see.
--
--   2. The COMMITMENT invariant is a CHECK constraint as well as a constructor
--      guard. A row that would execute on silence cannot be stored, even by a
--      future code path that forgets. Defense in depth on the one rule that
--      protects actions binding the company.

BEGIN;

CREATE TABLE IF NOT EXISTS events (
    id         text PRIMARY KEY,
    ts         double precision NOT NULL,
    task_id    text NOT NULL,
    agent      text NOT NULL DEFAULT '',
    kind       text NOT NULL,
    payload    jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS events_task ON events (task_id);
CREATE INDEX IF NOT EXISTS events_kind ON events (kind, ts);

CREATE TABLE IF NOT EXISTS decisions (
    task_id     text PRIMARY KEY,
    ts          double precision NOT NULL,
    agent       text NOT NULL,
    confidence  double precision NOT NULL
                CHECK (confidence >= 0 AND confidence <= 1),
    disposition text NOT NULL
                CHECK (disposition IN ('shipped', 'revised', 'escalated',
                                       'rejected', 'deterministic')),
    usd         double precision NOT NULL CHECK (usd >= 0),
    seconds     double precision NOT NULL CHECK (seconds >= 0),
    -- NULL until reality reports back. Calibration is impossible without it,
    -- which makes this the most commonly skipped and most important column.
    outcome     text CHECK (outcome IN ('held', 'refunded', 'complained'))
);
CREATE INDEX IF NOT EXISTS decisions_agent ON decisions (agent, ts);
CREATE INDEX IF NOT EXISTS decisions_calibration ON decisions (confidence)
    WHERE disposition = 'shipped' AND outcome IS NOT NULL;

CREATE TABLE IF NOT EXISTS escalations (
    id          text PRIMARY KEY,
    task_id     text NOT NULL,
    klass       text NOT NULL
                CHECK (klass IN ('novel', 'low_confidence', 'threshold',
                                 'conflict', 'commitment')),
    agent       text NOT NULL DEFAULT '',
    summary     text NOT NULL,
    proposed    text NOT NULL,
    alternative text NOT NULL,
    evidence    jsonb NOT NULL DEFAULT '{}'::jsonb,
    raised_at   double precision NOT NULL,
    notified_at double precision,
    resolved_at double precision,
    resolution  text CHECK (resolution IN ('proposed', 'alternative',
                                           'custom', 'timeout')),
    note        text,

    -- A COMMITMENT must never carry a default, or silence would execute it.
    CONSTRAINT commitment_has_no_default
        CHECK (klass <> 'commitment' OR proposed = ''),
    -- Everything else must carry one, or the operator becomes the bottleneck.
    CONSTRAINT other_classes_have_a_default
        CHECK (klass = 'commitment' OR length(proposed) > 0),
    -- A COMMITMENT can only be resolved by a person, never by a timeout.
    CONSTRAINT commitment_never_times_out
        CHECK (klass <> 'commitment' OR resolution IS DISTINCT FROM 'timeout'),
    CONSTRAINT resolved_rows_have_a_resolution
        CHECK ((resolved_at IS NULL) = (resolution IS NULL))
);
CREATE INDEX IF NOT EXISTS esc_open ON escalations (raised_at)
    WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS esc_undelivered ON escalations (raised_at)
    WHERE resolved_at IS NULL AND notified_at IS NULL;
CREATE INDEX IF NOT EXISTS esc_volume ON escalations (klass, raised_at);

-- Backup verification results. A backup nobody restores is a hope, not a
-- backup; restore-test.sh writes here and the harness escalates on staleness.
CREATE TABLE IF NOT EXISTS restore_tests (
    id         bigserial PRIMARY KEY,
    ts         double precision NOT NULL,
    ok         boolean NOT NULL,
    rows_seen  bigint NOT NULL DEFAULT 0,
    detail     text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS restore_tests_ts ON restore_tests (ts DESC);

COMMIT;
