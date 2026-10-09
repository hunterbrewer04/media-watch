"""Usage log: one SQLite row per watch call (cost, tokens, latency, cache hits, errors, prompt version).
This is what `mw stats` reads and what model/prompt changes get judged against."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              REAL    NOT NULL,
  caller          TEXT,
  source          TEXT,
  site            TEXT,
  item_key        TEXT,
  video_seconds   REAL,
  model           TEXT,
  detail          TEXT,
  prompt_version  TEXT,
  question        TEXT,
  prompt_tokens   INTEGER,
  completion_tokens INTEGER,
  cost_usd        REAL,
  latency_s       REAL,
  fetch_s         REAL,
  prep_s          REAL,
  cache_hit       INTEGER NOT NULL DEFAULT 0,
  fallback_used   INTEGER NOT NULL DEFAULT 0,
  ok              INTEGER NOT NULL,
  error           TEXT,
  extra           TEXT
);
CREATE INDEX IF NOT EXISTS calls_ts ON calls(ts);
"""


@dataclass
class CallRecord:
    caller: str | None = None
    source: str | None = None
    site: str | None = None
    item_key: str | None = None
    video_seconds: float | None = None
    model: str | None = None
    detail: str | None = None
    prompt_version: str | None = None
    question: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    latency_s: float | None = None
    fetch_s: float | None = None
    prep_s: float | None = None
    cache_hit: bool = False
    fallback_used: bool = False
    ok: bool = False
    error: str | None = None
    extra: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


def _connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=10)
    conn.executescript(SCHEMA)
    return conn


def log_call(db: Path, rec: CallRecord) -> None:
    row = asdict(rec)
    row["extra"] = json.dumps(row["extra"]) if row["extra"] else None
    row["cache_hit"], row["fallback_used"], row["ok"] = int(rec.cache_hit), int(rec.fallback_used), int(rec.ok)
    cols = ", ".join(row)
    with closing(_connect(db)) as conn, conn:
        conn.execute(f"INSERT INTO calls ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))


def stats(db: Path, days: float = 7) -> dict:
    since = time.time() - days * 86400
    with closing(_connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        tot = conn.execute(
            """SELECT COUNT(*) n, SUM(ok) ok, COALESCE(SUM(cost_usd),0) cost, SUM(cache_hit) hits,
                      SUM(fallback_used) fallbacks, AVG(CASE WHEN ok AND NOT cache_hit THEN latency_s END) lat,
                      COALESCE(SUM(video_seconds),0) secs
               FROM calls WHERE ts >= ?""", (since,)).fetchone()
        by_model = conn.execute(
            """SELECT model, COUNT(*) n, COALESCE(SUM(cost_usd),0) cost, AVG(latency_s) lat
               FROM calls WHERE ts >= ? AND ok GROUP BY model ORDER BY n DESC""", (since,)).fetchall()
        by_site = conn.execute(
            """SELECT site, COUNT(*) n, SUM(ok) ok FROM calls WHERE ts >= ? GROUP BY site ORDER BY n DESC""",
            (since,)).fetchall()
        errors = conn.execute(
            """SELECT datetime(ts,'unixepoch') at, site, error FROM calls
               WHERE ts >= ? AND NOT ok ORDER BY ts DESC LIMIT 5""", (since,)).fetchall()
    return {
        "days": days, "calls": tot["n"], "ok": tot["ok"] or 0, "cost_usd": round(tot["cost"], 5),
        "cache_hits": tot["hits"] or 0, "fallbacks": tot["fallbacks"] or 0,
        "avg_latency_s": round(tot["lat"], 2) if tot["lat"] else None,
        "video_minutes": round(tot["secs"] / 60, 1),
        "by_model": [dict(r) for r in by_model], "by_site": [dict(r) for r in by_site],
        "recent_errors": [dict(r) for r in errors],
    }
