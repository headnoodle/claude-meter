#!/usr/bin/env python3
"""
claude-meter — tracks Claude Code API spend per request using local JSONL transcripts.

Persists every request to ~/.claude-meter.db so data survives the ~30-day
transcript rotation Claude Code applies to ~/.claude/projects.

Designed to be called by xbar every minute; outputs xbar menu format.
"""

import json
import os
import sqlite3
import subprocess
from datetime import date
from pathlib import Path

CLAUDE_DIR   = Path.home() / ".claude" / "projects"
DB_PATH      = Path.home() / ".claude-meter.db"

# Daily spend threshold for macOS notifications.
# Configurable via xbar Settings (right-click the menu bar item).
# Falls back to this value when run outside xbar. Set to 0 to disable.
DAILY_BUDGET = float(os.environ.get("CLAUDE_METER_DAILY_BUDGET", 50.0))

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
    # Tracks which budget thresholds have already triggered a notification,
    # keyed by (day, threshold) so each crossing fires exactly once.
    db.execute("""
        CREATE TABLE IF NOT EXISTS budget_alerts (
            day        TEXT NOT NULL,
            threshold  REAL NOT NULL,
            PRIMARY KEY (day, threshold)
        )
    """)
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
# Budget alerts
# ---------------------------------------------------------------------------

def check_budget(db: sqlite3.Connection, today_cost: float) -> None:
    """Fire a macOS notification the first time today's spend crosses DAILY_BUDGET."""
    if DAILY_BUDGET <= 0 or today_cost < DAILY_BUDGET:
        return

    today = date.today().isoformat()
    already_fired = db.execute(
        "SELECT 1 FROM budget_alerts WHERE day=? AND threshold=?", (today, DAILY_BUDGET)
    ).fetchone()

    if already_fired:
        return

    db.execute(
        "INSERT OR IGNORE INTO budget_alerts (day, threshold) VALUES (?,?)",
        (today, DAILY_BUDGET),
    )
    db.commit()

    subprocess.run([
        "osascript", "-e",
        f'display notification "You\'ve spent {fmt(today_cost)} today (budget: {fmt(DAILY_BUDGET)})" '
        f'with title "claude-meter" subtitle "Daily budget reached" sound name "Basso"',
    ], check=False)


# ---------------------------------------------------------------------------
# xbar output
# ---------------------------------------------------------------------------

def fmt(v: float) -> str:
    return f"${v:.2f}"


def main() -> None:
    db = open_db()
    ingest(db)
    r  = report(db)
    check_budget(db, r["today_cost"])
    db.close()

    over_budget = DAILY_BUDGET > 0 and r["today_cost"] >= DAILY_BUDGET
    title_suffix = " ⚠️" if over_budget else ""
    print(f"🤖 {fmt(r['today_cost'])} today{title_suffix}")
    print("---")
    budget_line = f"  (budget: {fmt(DAILY_BUDGET)})" if DAILY_BUDGET > 0 else ""
    print(f"Today: {fmt(r['today_cost'])} ({r['today_reqs']} requests){budget_line}")
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
