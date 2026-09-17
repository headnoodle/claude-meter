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
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

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
    # cwd added in v5.1 — add column to existing databases that predate it
    cols = {row[1] for row in db.execute("PRAGMA table_info(requests)")}
    if "cwd" not in cols:
        db.execute("ALTER TABLE requests ADD COLUMN cwd TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cwd ON requests(cwd)")
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

    # Collect request_ids that are missing cwd so we can backfill them
    needs_cwd = {row[0] for row in db.execute("SELECT request_id FROM requests WHERE cwd IS NULL OR cwd = ''")}

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
                    cwd   = d.get("cwd", "")

                    if not rid or not day or cost == 0:
                        continue

                    db.execute(
                        "INSERT OR IGNORE INTO requests (request_id, ts, day, model, cost, cwd) VALUES (?,?,?,?,?,?)",
                        (rid, ts, day, model, cost, cwd),
                    )

                    # Backfill cwd on rows that predate the column
                    if rid in needs_cwd and cwd:
                        db.execute("UPDATE requests SET cwd=? WHERE request_id=?", (cwd, rid))
                        needs_cwd.discard(rid)

        except OSError:
            pass

    db.commit()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report(db: sqlite3.Connection) -> dict:
    today        = date.today().isoformat()
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    seven_days_ago = (date.today() - timedelta(days=7)).isoformat()

    # Average daily spend over the last 7 complete days (excludes today)
    prev_days = db.execute("""
        SELECT day, SUM(cost)
        FROM requests
        WHERE day >= ? AND day < ?
        GROUP BY day
    """, (seven_days_ago, today)).fetchall()
    avg_daily = (sum(c for _, c in prev_days) / len(prev_days)) if prev_days else 0.0

    today_cost = db.execute("SELECT COALESCE(SUM(cost),0) FROM requests WHERE day=?", (today,)).fetchone()[0]

    if avg_daily > 0:
        pct = (today_cost - avg_daily) / avg_daily * 100
    else:
        pct = None

    return {
        "today_cost":  today_cost,
        "today_reqs":  db.execute("SELECT COUNT(*) FROM requests WHERE day=?", (today,)).fetchone()[0],
        "burn_rate":   db.execute(
            "SELECT COALESCE(SUM(cost),0) FROM requests WHERE ts >= ?", (one_hour_ago,)
        ).fetchone()[0],
        "avg_daily":   avg_daily,
        "trend_pct":   pct,
        "all_cost":    db.execute("SELECT COALESCE(SUM(cost),0) FROM requests").fetchone()[0],
        "all_reqs":    db.execute("SELECT COUNT(*) FROM requests").fetchone()[0],
        "since":       db.execute("SELECT MIN(day) FROM requests").fetchone()[0],
        "by_day":      db.execute(
            "SELECT day, ROUND(SUM(cost),2) FROM requests GROUP BY day ORDER BY day DESC LIMIT 7"
        ).fetchall(),
        # Monthly rollups
        "month_rows": db.execute("""
            SELECT strftime('%Y-%m', day) AS month,
                   ROUND(SUM(cost), 2),
                   COUNT(*)
            FROM requests
            GROUP BY month
            ORDER BY month DESC
            LIMIT 6
        """).fetchall(),
        # Model breakdown for today and all time
        "models_today": db.execute("""
            SELECT model, ROUND(SUM(cost),2), COUNT(*)
            FROM requests WHERE day=? AND model != '' AND model != '<synthetic>'
            GROUP BY model ORDER BY SUM(cost) DESC
        """, (today,)).fetchall(),
        "models_all": db.execute("""
            SELECT model, ROUND(SUM(cost),2), COUNT(*)
            FROM requests WHERE model != '' AND model != '<synthetic>'
            GROUP BY model ORDER BY SUM(cost) DESC
        """).fetchall(),
        # Top projects by cost this week and all time, excluding blanks
        "top_projects_week": db.execute("""
            SELECT cwd, ROUND(SUM(cost),2) AS total
            FROM requests
            WHERE day >= ? AND cwd != ''
            GROUP BY cwd ORDER BY total DESC LIMIT 5
        """, (seven_days_ago,)).fetchall(),
        "top_projects_all": db.execute("""
            SELECT cwd, ROUND(SUM(cost),2) AS total
            FROM requests
            WHERE cwd != ''
            GROUP BY cwd ORDER BY total DESC LIMIT 5
        """).fetchall(),
    }


# ---------------------------------------------------------------------------
# Current session
# ---------------------------------------------------------------------------

def active_sessions() -> list:
    """One entry per JSONL file modified today, with total-session and today-only costs."""
    if not CLAUDE_DIR.exists():
        return []

    today = date.today().isoformat()
    results = []

    for jsonl in CLAUDE_DIR.rglob("*.jsonl"):
        try:
            if datetime.fromtimestamp(jsonl.stat().st_mtime).date() != date.today():
                continue
        except OSError:
            continue

        total_cost = today_cost = 0.0
        total_reqs = today_reqs = 0
        cwd = None

        try:
            with open(jsonl, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "input_tokens" not in line:
                        continue
                    try:
                        d = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    msg = d.get("message", {})
                    u = msg.get("usage", {})
                    if not u or "input_tokens" not in u:
                        continue
                    cost = request_cost(u, msg.get("model", ""))
                    total_cost += cost
                    total_reqs += 1
                    if d.get("timestamp", "")[:10] == today:
                        today_cost += cost
                        today_reqs += 1
                    if cwd is None:
                        cwd = d.get("cwd", "")
        except OSError:
            continue

        if total_cost > 0:
            results.append({
                "cwd":        cwd or "",
                "total_cost": total_cost,
                "total_reqs": total_reqs,
                "today_cost": today_cost,
                "today_reqs": today_reqs,
            })

    return sorted(results, key=lambda x: x["today_cost"], reverse=True)


# ---------------------------------------------------------------------------
# Budget alerts
# ---------------------------------------------------------------------------

def check_budget(db: sqlite3.Connection, today_cost: float) -> None:
    """Fire a macOS notification when today's spend crosses 50%, 75%, or 100% of budget.

    Only the highest newly-crossed threshold fires. All lower thresholds are
    simultaneously marked as fired so they never fire in a later refresh.
    """
    if DAILY_BUDGET <= 0:
        return

    today  = date.today().isoformat()
    levels = [
        (DAILY_BUDGET * 0.50, "50%",  "Halfway through daily budget"),
        (DAILY_BUDGET * 0.75, "75%",  "75% of daily budget used"),
        (DAILY_BUDGET * 1.00, "100%", "Daily budget reached"),
    ]

    fired = {row[0] for row in db.execute(
        "SELECT threshold FROM budget_alerts WHERE day=?", (today,)
    )}

    # Highest threshold crossed that hasn't fired yet
    to_fire = None
    for amount, label, subtitle in reversed(levels):
        if today_cost >= amount and amount not in fired:
            to_fire = (amount, label, subtitle)
            break

    if not to_fire:
        return

    fire_amount, label, subtitle = to_fire

    # Mark this level and every lower one as fired so they never trigger later
    for amount, _, _ in levels:
        if amount <= fire_amount:
            db.execute(
                "INSERT OR IGNORE INTO budget_alerts (day, threshold) VALUES (?,?)",
                (today, amount),
            )
    db.commit()

    subprocess.run([
        "osascript", "-e",
        f'display notification "Spent {fmt(today_cost)} today ({label} of {fmt(DAILY_BUDGET)} budget)" '
        f'with title "claude-meter" subtitle "{subtitle}" sound name "Basso"',
    ], check=False)


# ---------------------------------------------------------------------------
# xbar output
# ---------------------------------------------------------------------------

def fmt(v: float) -> str:
    return f"${v:.2f}"


def sparkline(values: list, today_color: str = "") -> str:
    """Map a list of floats to Unicode block chars (oldest → newest, left → right).

    today_color: hex string like '#ffa94d' — colours the last (today) bar differently.
    Requires ansi=true in the xbar line params.
    """
    blocks = "▁▂▃▄▅▆▇█"
    if not values:
        return ""
    max_v = max(values) or 1
    chars = [blocks[min(7, int(v / max_v * 7.999))] for v in values]
    if today_color and chars:
        r = int(today_color[1:3], 16)
        g = int(today_color[3:5], 16)
        b = int(today_color[5:7], 16)
        chars[-1] = f"\x1b[38;2;{r};{g};{b}m{chars[-1]}\x1b[0m"
    return "".join(chars)


def budget_bar(current: float, limit: float, width: int = 20) -> str:
    """Return a filled block bar and a color hint based on how close to the limit."""
    pct   = min(1.0, current / limit) if limit > 0 else 0
    filled = round(pct * width)
    bar   = "█" * filled + "░" * (width - filled)
    if pct >= 0.85:
        color = "#ff6b6b"
    elif pct >= 0.6:
        color = "#ffa94d"
    else:
        color = "#51cf66"
    return bar, color


def _short_model(model: str) -> str:
    """Collapse verbose model IDs to a readable short name."""
    m = model.lower()
    if "fable"    in m: return "Fable 5"
    if "opus-5"   in m: return "Opus 5"
    if "opus-4-8" in m: return "Opus 4.8"
    if "opus-4"   in m: return "Opus 4"
    if "sonnet-5" in m: return "Sonnet 5"
    if "sonnet-4-6" in m: return "Sonnet 4.6"
    if "sonnet-4" in m: return "Sonnet 4"
    if "haiku-4-5" in m: return "Haiku 4.5"
    if "haiku"    in m: return "Haiku"
    return model


def _model_color(model: str) -> str:
    m = model.lower()
    if "opus"  in m: return "#ff6b6b"
    if "haiku" in m: return "#51cf66"
    return "#ffa94d"  # Sonnet / default


def model_bar(cost: float, total: float, width: int = 16) -> str:
    pct   = cost / total if total > 0 else 0
    filled = round(pct * width)
    return "█" * filled + "░" * (width - filled)


def main() -> None:
    db = open_db()
    ingest(db)
    r  = report(db)
    check_budget(db, r["today_cost"])
    db.close()

    over_budget = DAILY_BUDGET > 0 and r["today_cost"] >= DAILY_BUDGET
    burn        = r["burn_rate"]
    burn_str    = f" · {fmt(burn)}/hr" if burn >= 0.01 else ""
    alert_str   = " ⚠️" if over_budget else ""
    this_month  = date.today().strftime("%Y-%m")
    month_cost  = next((c for m, c, _ in r["month_rows"] if m == this_month), 0.0)

    # ── Title bar ────────────────────────────────────────────────────────────
    print(f"🤖 {fmt(r['today_cost'])} today  ·  {fmt(month_cost)} this month{burn_str}{alert_str}")
    print("---")

    # ── Today summary ────────────────────────────────────────────────────────
    budget_line = f"  (budget: {fmt(DAILY_BUDGET)})" if DAILY_BUDGET > 0 else ""
    today_color = " | color=#ff6b6b" if over_budget else ""
    print(f"Today: {fmt(r['today_cost'])} ({r['today_reqs']} requests){budget_line}{today_color}")

    if DAILY_BUDGET > 0:
        bar, bar_color = budget_bar(r["today_cost"], DAILY_BUDGET)
        pct_num = min(100, r["today_cost"] / DAILY_BUDGET * 100)
        print(f"{bar}  {pct_num:.0f}% of {fmt(DAILY_BUDGET)} | color={bar_color} font=Menlo size=11")

    burn_color = " | color=#ffa94d" if burn >= 0.01 else ""
    print(f"Burn rate: {fmt(burn)}/hr  (rolling 1h){burn_color}")

    pct = r["trend_pct"]
    if pct is not None:
        arrow       = "▲" if pct >= 0 else "▼"
        trend_color = " | color=#ff6b6b" if pct >= 0 else " | color=#51cf66"
        print(f"Trend: {arrow} {abs(pct):.0f}% vs 7d avg ({fmt(r['avg_daily'])}/day){trend_color}")

    # Sparkline: by_day is DESC, reverse for left=oldest right=newest
    day_costs = [c for _, c in reversed(r["by_day"])]
    if len(day_costs) > 1:
        today_hl = bar_color if DAILY_BUDGET > 0 else "#ffa94d"
        spark = sparkline(day_costs, today_color=today_hl)
        print(f"Week  {spark} | font=Menlo size=13 ansi=true")

    # ── Active sessions ───────────────────────────────────────────────────────
    sessions = [s for s in active_sessions() if s["today_reqs"] > 0]
    if sessions:
        print("---")
        print(f"{'Sessions':<24}{'today':>9}  {'session':>9} | font=Menlo size=11 color=#868e96")
        for s in sessions:
            name = (Path(s["cwd"]).name if s["cwd"] else "?")[:24]
            print(f"{name:<24}{fmt(s['today_cost']):>9}  {fmt(s['total_cost']):>9} | font=Menlo size=11")

    # ── Models today (visible — short and useful) ─────────────────────────────
    if r["models_today"]:
        print("---")
        print("Models today | color=#868e96")
        total_today_cost = sum(c for _, c, _ in r["models_today"])
        for model, cost, reqs in r["models_today"]:
            short = _short_model(model)
            bar   = model_bar(cost, total_today_cost)
            pct   = int(cost / total_today_cost * 100) if total_today_cost else 0
            color = _model_color(model)
            print(f"{short:<12} {bar}  {pct:>3}%  {fmt(cost):>8}  ({reqs} reqs) | font=Menlo size=11 color={color}")

    # ── Last 7 days (submenu) ─────────────────────────────────────────────────
    print("---")
    print("Last 7 days | color=#868e96")
    for day, cost in r["by_day"]:
        marker = " ◀" if day == date.today().isoformat() else ""
        print(f"-- {day}  {fmt(cost)}{marker}")

    # ── Top projects (submenu, 7 days and all time nested) ────────────────────
    if r["top_projects_week"] or r["top_projects_all"]:
        print("---")
        print("Top projects | color=#868e96")
        if r["top_projects_week"]:
            print("-- 7 days | color=#868e96")
            total_week = sum(c for _, c in r["top_projects_week"])
            for cwd, cost in r["top_projects_week"]:
                name = (Path(cwd).name or cwd)[:14]
                bar  = model_bar(cost, total_week)
                pct  = int(cost / total_week * 100) if total_week else 0
                print(f"---- {name:<14} {bar}  {pct:>3}%  {fmt(cost):>8} | font=Menlo size=11")
        if r["top_projects_all"]:
            print("-- All time | color=#868e96")
            total_all = sum(c for _, c in r["top_projects_all"])
            for cwd, cost in r["top_projects_all"]:
                name = (Path(cwd).name or cwd)[:14]
                bar  = model_bar(cost, total_all)
                pct  = int(cost / total_all * 100) if total_all else 0
                print(f"---- {name:<14} {bar}  {pct:>3}%  {fmt(cost):>8} | font=Menlo size=11")

    # ── Models all time (submenu) ─────────────────────────────────────────────
    if r["models_all"]:
        print("---")
        print("Models (all time) | color=#868e96")
        total_all_cost = sum(c for _, c, _ in r["models_all"])
        for model, cost, reqs in r["models_all"]:
            short = _short_model(model)
            bar   = model_bar(cost, total_all_cost)
            pct   = int(cost / total_all_cost * 100) if total_all_cost else 0
            color = _model_color(model)
            print(f"-- {short:<12} {bar}  {pct:>3}%  {fmt(cost):>8}  ({reqs} reqs) | font=Menlo size=11 color={color}")

    # ── Monthly (submenu) ─────────────────────────────────────────────────────
    if r["month_rows"]:
        print("---")
        print("Monthly | color=#868e96")
        for month, cost, reqs in r["month_rows"]:
            marker = " ◀" if month == this_month else ""
            print(f"-- {month}  {fmt(cost):>8}  ({reqs} reqs){marker}")

    # ── Footer ────────────────────────────────────────────────────────────────
    print("---")
    print(f"All time: {fmt(r['all_cost'])} ({r['all_reqs']} requests)")
    if r["since"]:
        print(f"Tracked since: {r['since']}")
    print("---")
    print(f"DB: {DB_PATH}")


if __name__ == "__main__":
    main()
