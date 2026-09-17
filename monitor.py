#!/usr/bin/env python3
"""
claude-meter — tracks Claude Code API spend per request using local JSONL transcripts.

Persists every request to ~/.claude-meter.db so data survives the ~30-day
transcript rotation Claude Code applies to ~/.claude/projects.

Designed to be called by xbar every minute; outputs xbar menu format.
"""

import json
import sqlite3
from datetime import date
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude" / "projects"
DB_PATH    = Path.home() / ".claude-meter.db"

PRICING = {
    "claude-sonnet-4-6":         {"i": 3.0,  "o": 15.0, "cw": 3.75, "cr": 0.30},
    "claude-sonnet-5":           {"i": 3.0,  "o": 15.0, "cw": 3.75, "cr": 0.30},
    "claude-opus-5":             {"i": 5.0,  "o": 25.0, "cw": 6.25, "cr": 0.50},
    "claude-opus-4-8":           {"i": 5.0,  "o": 25.0, "cw": 6.25, "cr": 0.50},
    "claude-haiku-4-5-20251001": {"i": 0.80, "o": 4.0,  "cw": 1.0,  "cr": 0.08},
    "<synthetic>":               {"i": 0.0,  "o": 0.0,  "cw": 0.0,  "cr": 0.0 },
}
DEFAULT_P = {"i": 3.0, "o": 15.0, "cw": 3.75, "cr": 0.30}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def open_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            request_id  TEXT PRIMARY KEY,
            ts          TEXT NOT NULL,
            day         TEXT NOT NULL,
            model       TEXT,
            cost        REAL NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_day ON requests(day)")
    db.commit()
    return db


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def request_cost(usage: dict, model: str) -> float:
    p = PRICING.get(model, DEFAULT_P)
    return (
        usage.get("input_tokens", 0)                * p["i"]  +
        usage.get("output_tokens", 0)               * p["o"]  +
        usage.get("cache_creation_input_tokens", 0) * p["cw"] +
        usage.get("cache_read_input_tokens", 0)     * p["cr"]
    ) / 1e6


def ingest(db: sqlite3.Connection) -> None:
    """Walk JSONL transcripts and upsert every assistant request into the DB."""
    if not CLAUDE_DIR.exists():
        return

    for jsonl in CLAUDE_DIR.rglob("*.jsonl"):
        try:
            with open(jsonl, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "input_tokens" not in line or "timestamp" not in line:
                        continue
                    try:
                        d = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    msg = d.get("message", {})
                    u   = msg.get("usage", {})
                    if not u or "input_tokens" not in u:
                        continue

                    rid   = d.get("requestId") or d.get("uuid", "")
                    ts    = d.get("timestamp", "")
                    day   = ts[:10] if ts else ""
                    model = msg.get("model", "")
                    cost  = request_cost(u, model)

                    if not rid or not day or cost == 0:
                        continue

                    db.execute(
                        "INSERT OR IGNORE INTO requests (request_id, ts, day, model, cost) VALUES (?,?,?,?,?)",
                        (rid, ts, day, model, cost),
                    )
        except OSError:
            pass

    db.commit()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report(db: sqlite3.Connection) -> dict:
    today = date.today().isoformat()
    return {
        "today_cost":  db.execute("SELECT COALESCE(SUM(cost),0) FROM requests WHERE day=?", (today,)).fetchone()[0],
        "today_reqs":  db.execute("SELECT COUNT(*) FROM requests WHERE day=?", (today,)).fetchone()[0],
        "all_cost":    db.execute("SELECT COALESCE(SUM(cost),0) FROM requests").fetchone()[0],
        "all_reqs":    db.execute("SELECT COUNT(*) FROM requests").fetchone()[0],
        "since":       db.execute("SELECT MIN(day) FROM requests").fetchone()[0],
        "by_day":      db.execute(
            "SELECT day, ROUND(SUM(cost),2) FROM requests GROUP BY day ORDER BY day DESC LIMIT 7"
        ).fetchall(),
    }


# ---------------------------------------------------------------------------
# xbar output
# ---------------------------------------------------------------------------

def fmt(v: float) -> str:
    return f"${v:.2f}"


def main() -> None:
    db = open_db()
    ingest(db)
    r  = report(db)
    db.close()

    print(f"🤖 {fmt(r['today_cost'])} today")
    print("---")
    print(f"Today: {fmt(r['today_cost'])} ({r['today_reqs']} requests)")
    print("---")
    print("Last 7 days")
    for day, cost in r["by_day"]:
        marker = " ◀" if day == date.today().isoformat() else ""
        print(f"  {day}  {fmt(cost)}{marker}")
    print("---")
    print(f"All time: {fmt(r['all_cost'])} ({r['all_reqs']} requests)")
    if r["since"]:
        print(f"Tracked since: {r['since']}")
    print("---")
    print(f"DB: {DB_PATH}")


if __name__ == "__main__":
    main()
