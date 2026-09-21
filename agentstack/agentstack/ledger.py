"""Append-only event log.

Two jobs, both essential: it is the dataset you tune thresholds against, and it
is your evidence if an agent does something you have to answer for. Nothing in
this file ever updates or deletes a row.

SQLite here keeps the reference implementation runnable anywhere. Move to
Postgres before real traffic — you want concurrent writers and WAL durability,
and the schema ports unchanged.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,
    ts         REAL NOT NULL,
    task_id    TEXT NOT NULL,
    agent      TEXT,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_task ON events(task_id);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, ts);

-- One row per delivered unit of work. `outcome` starts NULL and is filled in
-- later from refunds, complaints, and repeat purchases. Without that column
-- you cannot check calibration, and without calibration the thresholds are
-- guesses.
CREATE TABLE IF NOT EXISTS decisions (
    task_id     TEXT PRIMARY KEY,
    ts          REAL NOT NULL,
    agent       TEXT NOT NULL,
    confidence  REAL NOT NULL,
    disposition TEXT NOT NULL,     -- shipped | revised | escalated | rejected
    usd         REAL NOT NULL,
    seconds     REAL NOT NULL,
    outcome     TEXT               -- held | refunded | complained | NULL
);
"""


class Ledger:
    def __init__(self, path: str | Path = "ledger.db"):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def event(self, task_id: str, kind: str, payload: dict[str, Any], agent: str = "") -> None:
        self._db.execute(
            "INSERT INTO events (id, ts, task_id, agent, kind, payload) VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, time.time(), task_id, agent, kind,
             json.dumps(payload, default=str)),
        )
        self._db.commit()

    def decision(self, task_id: str, agent: str, confidence: float,
                 disposition: str, usd: float, seconds: float) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO decisions "
            "(task_id, ts, agent, confidence, disposition, usd, seconds) "
            "VALUES (?,?,?,?,?,?,?)",
            (task_id, time.time(), agent, confidence, disposition, usd, seconds),
        )
        self._db.commit()

    def record_outcome(self, task_id: str, outcome: str) -> None:
        self._db.execute("UPDATE decisions SET outcome=? WHERE task_id=?", (outcome, task_id))
        self._db.commit()

    # --- the metrics that matter -------------------------------------------

    def autonomy_rate(self, agent: str | None = None, since: float = 0.0) -> float:
        """Fraction of work that shipped without a human. The number to watch."""
        q = "SELECT disposition, COUNT(*) FROM decisions WHERE ts >= ?"
        args: list[Any] = [since]
        if agent:
            q, args = q + " AND agent = ?", args + [agent]
        rows = dict(self._db.execute(q + " GROUP BY disposition", args).fetchall())
        total = sum(rows.values())
        return (rows.get("shipped", 0) / total) if total else 0.0

    def calibration(self, bucket: float = 0.05) -> list[tuple[float, int, float]]:
        """(confidence bucket, n, fraction that held up).

        If Jev says 0.90 and roughly 90% of that bucket holds, the calibration
        is real and you can safely lower θ_ship. If not, fix the acceptance
        questions — lowering the threshold against bad questions just ships
        bad work faster.
        """
        rows = self._db.execute(
            "SELECT confidence, outcome FROM decisions "
            "WHERE disposition='shipped' AND outcome IS NOT NULL"
        ).fetchall()
        buckets: dict[float, list[bool]] = {}
        for conf, outcome in rows:
            b = round(conf / bucket) * bucket
            buckets.setdefault(b, []).append(outcome == "held")
        return sorted(
            (b, len(v), sum(v) / len(v)) for b, v in buckets.items()
        )
