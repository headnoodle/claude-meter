#!/usr/bin/env python3
"""
claude-meter — tracks Claude Code API spend per request using local JSONL transcripts.

Runs as a persistent macOS menu bar app via rumps.
Start on login with: brew services start claude-meter
"""

import json
import os
import sqlite3
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import rumps

VERSION     = "0.2.0"
CLAUDE_DIR  = Path.home() / ".claude" / "projects"
DB_PATH     = Path.home() / ".claude-meter.db"
CONFIG_PATH = Path.home() / ".claude-meter.conf"

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
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def get_budget() -> float:
    cfg = load_config()
    if "daily_budget" in cfg:
        return float(cfg["daily_budget"])
    return float(os.environ.get("CLAUDE_METER_DAILY_BUDGET", 50.0))


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
    cols = {row[1] for row in db.execute("PRAGMA table_info(requests)")}
    for col, defn in [
        ("cwd",                "TEXT"),
        ("thinking_cost",      "REAL DEFAULT 0"),
        ("cache_read_tokens",  "INTEGER DEFAULT 0"),
        ("cache_total_tokens", "INTEGER DEFAULT 0"),
        ("cache_savings",      "REAL DEFAULT 0"),
        ("git_branch",         "TEXT DEFAULT ''"),
    ]:
        if col not in cols:
            db.execute(f"ALTER TABLE requests ADD COLUMN {col} {defn}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cwd    ON requests(cwd)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_branch ON requests(git_branch)")
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


def thinking_cost(usage: dict, model: str) -> float:
    details = usage.get("output_tokens_details") or {}
    tokens  = details.get("thinking_tokens", 0)
    return tokens * PRICING.get(model, DEFAULT_P)["o"] / 1e6


def ingest(db: sqlite3.Connection) -> None:
    if not CLAUDE_DIR.exists():
        return

    needs_cwd    = {row[0] for row in db.execute("SELECT request_id FROM requests WHERE cwd IS NULL OR cwd = ''")}
    needs_branch = {row[0] for row in db.execute("SELECT request_id FROM requests WHERE git_branch IS NULL OR git_branch = ''")}
    needs_cache  = {row[0] for row in db.execute("SELECT request_id FROM requests WHERE cache_total_tokens = 0")}

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

                    rid        = d.get("requestId") or d.get("uuid", "")
                    ts         = d.get("timestamp", "")
                    day        = ts[:10] if ts else ""
                    model      = msg.get("model", "")
                    p          = PRICING.get(model, DEFAULT_P)
                    cost       = request_cost(u, model)
                    think_cost = thinking_cost(u, model)
                    cwd        = d.get("cwd", "")
                    branch     = d.get("gitBranch", "") or ""
                    cr_tok     = u.get("cache_read_input_tokens", 0)
                    ct_tok     = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + cr_tok
                    c_savings  = cr_tok * (p["i"] - p["cr"]) / 1e6

                    if not rid or not day or cost == 0:
                        continue

                    db.execute(
                        """INSERT OR IGNORE INTO requests
                           (request_id, ts, day, model, cost, thinking_cost,
                            cache_read_tokens, cache_total_tokens, cache_savings,
                            git_branch, cwd)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (rid, ts, day, model, cost, think_cost,
                         cr_tok, ct_tok, c_savings, branch, cwd),
                    )

                    if rid in needs_cwd and cwd:
                        db.execute("UPDATE requests SET cwd=? WHERE request_id=?", (cwd, rid))
                        needs_cwd.discard(rid)
                    if rid in needs_branch and branch:
                        db.execute("UPDATE requests SET git_branch=? WHERE request_id=?", (branch, rid))
                        needs_branch.discard(rid)
                    if rid in needs_cache and ct_tok > 0:
                        db.execute(
                            "UPDATE requests SET cache_read_tokens=?, cache_total_tokens=?, cache_savings=? WHERE request_id=?",
                            (cr_tok, ct_tok, c_savings, rid),
                        )
                        needs_cache.discard(rid)

        except OSError:
            pass

    db.commit()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report(db: sqlite3.Connection) -> dict:
    today          = date.today().isoformat()
    one_hour_ago   = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    seven_days_ago = (date.today() - timedelta(days=7)).isoformat()

    prev_days = db.execute("""
        SELECT day, SUM(cost) FROM requests
        WHERE day >= ? AND day < ? GROUP BY day
    """, (seven_days_ago, today)).fetchall()
    avg_daily  = (sum(c for _, c in prev_days) / len(prev_days)) if prev_days else 0.0
    today_cost = db.execute("SELECT COALESCE(SUM(cost),0) FROM requests WHERE day=?", (today,)).fetchone()[0]
    pct        = ((today_cost - avg_daily) / avg_daily * 100) if avg_daily > 0 else None

    return {
        "today_cost":        today_cost,
        "today_thinking":    db.execute("SELECT COALESCE(SUM(thinking_cost),0) FROM requests WHERE day=?", (today,)).fetchone()[0],
        "today_reqs":        db.execute("SELECT COUNT(*) FROM requests WHERE day=?", (today,)).fetchone()[0],
        "burn_rate":         db.execute("SELECT COALESCE(SUM(cost),0) FROM requests WHERE ts >= ?", (one_hour_ago,)).fetchone()[0],
        "avg_daily":         avg_daily,
        "trend_pct":         pct,
        "all_cost":          db.execute("SELECT COALESCE(SUM(cost),0) FROM requests").fetchone()[0],
        "all_reqs":          db.execute("SELECT COUNT(*) FROM requests").fetchone()[0],
        "since":             db.execute("SELECT MIN(day) FROM requests").fetchone()[0],
        "by_day":            db.execute("SELECT day, ROUND(SUM(cost),2) FROM requests GROUP BY day ORDER BY day DESC LIMIT 7").fetchall(),
        "month_rows":        db.execute("""
            SELECT strftime('%Y-%m', day) AS month, ROUND(SUM(cost),2), COUNT(*)
            FROM requests GROUP BY month ORDER BY month DESC LIMIT 6
        """).fetchall(),
        "models_today":      db.execute("""
            SELECT model, ROUND(SUM(cost),2), COUNT(*), ROUND(SUM(thinking_cost),2)
            FROM requests WHERE day=? AND model != '' AND model != '<synthetic>'
            GROUP BY model ORDER BY SUM(cost) DESC
        """, (today,)).fetchall(),
        "models_all":        db.execute("""
            SELECT model, ROUND(SUM(cost),2), COUNT(*), ROUND(SUM(thinking_cost),2)
            FROM requests WHERE model != '' AND model != '<synthetic>'
            GROUP BY model ORDER BY SUM(cost) DESC
        """).fetchall(),
        "cache_today":       db.execute("""
            SELECT COALESCE(SUM(cache_read_tokens),0),
                   COALESCE(SUM(cache_total_tokens),0),
                   COALESCE(SUM(cache_savings),0)
            FROM requests WHERE day=?
        """, (today,)).fetchone(),
        "top_branches_week": db.execute("""
            SELECT git_branch, ROUND(SUM(cost),2) FROM requests
            WHERE day >= ? AND git_branch != '' AND git_branch IS NOT NULL
            GROUP BY git_branch ORDER BY SUM(cost) DESC LIMIT 7
        """, (seven_days_ago,)).fetchall(),
        "top_branches_all":  db.execute("""
            SELECT git_branch, ROUND(SUM(cost),2) FROM requests
            WHERE git_branch != '' AND git_branch IS NOT NULL
            GROUP BY git_branch ORDER BY SUM(cost) DESC LIMIT 7
        """).fetchall(),
        "top_projects_week": db.execute("""
            SELECT cwd, ROUND(SUM(cost),2) AS total FROM requests
            WHERE day >= ? AND cwd != '' GROUP BY cwd ORDER BY total DESC LIMIT 5
        """, (seven_days_ago,)).fetchall(),
        "top_projects_all":  db.execute("""
            SELECT cwd, ROUND(SUM(cost),2) AS total FROM requests
            WHERE cwd != '' GROUP BY cwd ORDER BY total DESC LIMIT 5
        """).fetchall(),
    }


# ---------------------------------------------------------------------------
# Active sessions
# ---------------------------------------------------------------------------

def active_sessions(db: sqlite3.Connection) -> list:
    if not CLAUDE_DIR.exists():
        return []

    today = date.today().isoformat()
    session_by_cwd: dict = {}

    for jsonl in CLAUDE_DIR.rglob("*.jsonl"):
        try:
            if datetime.fromtimestamp(jsonl.stat().st_mtime).date() != date.today():
                continue
        except OSError:
            continue

        total_cost, total_reqs, cwd = 0.0, 0, None
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
                    u   = msg.get("usage", {})
                    if not u or "input_tokens" not in u:
                        continue
                    total_cost += request_cost(u, msg.get("model", ""))
                    total_reqs += 1
                    if cwd is None:
                        cwd = d.get("cwd", "")
        except OSError:
            continue

        if not cwd or total_cost == 0:
            continue
        if cwd not in session_by_cwd:
            session_by_cwd[cwd] = {"total_cost": 0.0, "total_reqs": 0}
        session_by_cwd[cwd]["total_cost"] += total_cost
        session_by_cwd[cwd]["total_reqs"] += total_reqs

    results = []
    for cwd, sess in session_by_cwd.items():
        today_cost, today_reqs = db.execute(
            "SELECT COALESCE(SUM(cost),0), COUNT(*) FROM requests WHERE day=? AND cwd=?",
            (today, cwd),
        ).fetchone()
        if today_reqs == 0:
            continue
        results.append({
            "cwd":        cwd,
            "total_cost": sess["total_cost"],
            "total_reqs": sess["total_reqs"],
            "today_cost": today_cost,
            "today_reqs": today_reqs,
        })

    return sorted(results, key=lambda x: x["today_cost"], reverse=True)


# ---------------------------------------------------------------------------
# Budget alerts
# ---------------------------------------------------------------------------

def check_budget(db: sqlite3.Connection, today_cost: float, daily_budget: float) -> None:
    if daily_budget <= 0:
        return

    today  = date.today().isoformat()
    levels = [
        (daily_budget * 0.50, "50%",  "Halfway through daily budget"),
        (daily_budget * 0.75, "75%",  "75% of daily budget used"),
        (daily_budget * 1.00, "100%", "Daily budget reached"),
    ]
    fired = {row[0] for row in db.execute("SELECT threshold FROM budget_alerts WHERE day=?", (today,))}

    to_fire = None
    for amount, label, subtitle in reversed(levels):
        if today_cost >= amount and amount not in fired:
            to_fire = (amount, label, subtitle)
            break

    if not to_fire:
        return

    fire_amount, label, subtitle = to_fire
    for amount, _, _ in levels:
        if amount <= fire_amount:
            db.execute("INSERT OR IGNORE INTO budget_alerts (day, threshold) VALUES (?,?)", (today, amount))
    db.commit()

    rumps.notification(
        title="claude-meter",
        subtitle=subtitle,
        message=f"Spent {fmt(today_cost)} today ({label} of {fmt(daily_budget)} budget)",
        sound=True,
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt(v: float) -> str:
    return f"${v:.2f}"


def sparkline(values: list) -> str:
    blocks = "▁▂▃▄▅▆▇█"
    if not values:
        return ""
    max_v = max(values) or 1
    return "".join(blocks[min(7, int(v / max_v * 7.999))] for v in values)


def budget_bar(current: float, limit: float, width: int = 20) -> tuple:
    pct    = min(1.0, current / limit) if limit > 0 else 0
    filled = round(pct * width)
    return "█" * filled + "░" * (width - filled), pct


def cache_bar(hit_rate: float, width: int = 20) -> str:
    filled = round(hit_rate * width)
    return "█" * filled + "░" * (width - filled)


def model_bar(cost: float, total: float, width: int = 16) -> str:
    pct    = cost / total if total > 0 else 0
    filled = round(pct * width)
    return "█" * filled + "░" * (width - filled)


def _short_model(model: str) -> str:
    m = model.lower()
    if "fable"      in m: return "Fable 5"
    if "opus-5"     in m: return "Opus 5"
    if "opus-4-8"   in m: return "Opus 4.8"
    if "opus-4"     in m: return "Opus 4"
    if "sonnet-5"   in m: return "Sonnet 5"
    if "sonnet-4-6" in m: return "Sonnet 4.6"
    if "sonnet-4"   in m: return "Sonnet 4"
    if "haiku-4-5"  in m: return "Haiku 4.5"
    if "haiku"      in m: return "Haiku"
    return model


def _mi(title: str) -> rumps.MenuItem:
    return rumps.MenuItem(title)


# ---------------------------------------------------------------------------
# Menu bar app
# ---------------------------------------------------------------------------

class ClaudeMeterApp(rumps.App):
    def __init__(self):
        super().__init__("🤖", quit_button=rumps.MenuItem("Quit claude-meter"))
        self._refresh(None)

    @rumps.timer(60)
    def _refresh(self, _):
        try:
            budget   = get_budget()
            db       = open_db()
            ingest(db)
            r        = report(db)
            sessions = active_sessions(db)
            check_budget(db, r["today_cost"], budget)
            db.close()
            self._build(r, sessions, budget)
        except Exception as exc:
            self.title = "🤖 !"
            self.menu.clear()
            self.menu.add(rumps.MenuItem(f"Error: {exc}"))

    def _build(self, r: dict, sessions: list, budget: float) -> None:
        this_month  = date.today().strftime("%Y-%m")
        month_cost  = next((c for m, c, _ in r["month_rows"] if m == this_month), 0.0)
        over_budget = budget > 0 and r["today_cost"] >= budget
        burn        = r["burn_rate"]

        self.title = f"🤖 {fmt(r['today_cost'])}{'  ⚠️' if over_budget else ''}"

        items = []

        # Today summary
        budget_str = f"  (budget: {fmt(budget)})" if budget > 0 else ""
        think_str  = f"  · ↯ {fmt(r['today_thinking'])} thinking" if r["today_thinking"] >= 0.01 else ""
        items.append(_mi(f"Today:  {fmt(r['today_cost'])} ({r['today_reqs']} reqs){think_str}{budget_str}"))
        items.append(_mi(f"Month:  {fmt(month_cost)}"))

        if budget > 0:
            bar, pct_n = budget_bar(r["today_cost"], budget)
            items.append(_mi(f"{bar}  {pct_n*100:.0f}% of {fmt(budget)}"))

        items.append(_mi(f"Burn rate:  {fmt(burn)}/hr  (rolling 1h)"))

        if r["trend_pct"] is not None:
            pct   = r["trend_pct"]
            arrow = "▲" if pct >= 0 else "▼"
            items.append(_mi(f"Trend:  {arrow} {abs(pct):.0f}% vs 7d avg ({fmt(r['avg_daily'])}/day)"))

        cr_tok, ct_tok, c_saved = r["cache_today"]
        if ct_tok > 0:
            hit_rate = cr_tok / ct_tok
            bar      = cache_bar(hit_rate)
            items.append(_mi(f"Cache  {bar}  {hit_rate*100:.0f}% hit · saved {fmt(c_saved)}"))

        day_costs = [c for _, c in reversed(r["by_day"])]
        if len(day_costs) > 1:
            items.append(_mi(f"Week   {sparkline(day_costs)}"))

        # Sessions
        if sessions:
            items.append(None)
            hdr = _mi(f"{'Sessions':<24}{'today':>9}  {'session':>9}")
            for s in sessions:
                name = (Path(s["cwd"]).name if s["cwd"] else "?")[:24]
                hdr.add(_mi(f"{name:<24}{fmt(s['today_cost']):>9}  {fmt(s['total_cost']):>9}"))
            items.append(hdr)

        # Models today
        if r["models_today"]:
            items.append(None)
            mod_item    = _mi("Models today")
            total_today = sum(c for _, c, _, _ in r["models_today"])
            for model, cost, reqs, think in r["models_today"]:
                short   = _short_model(model)
                bar     = model_bar(cost, total_today)
                pct_m   = int(cost / total_today * 100) if total_today else 0
                think_s = f"  ↯ {fmt(think)}" if think >= 0.01 else ""
                mod_item.add(_mi(f"{short:<12} {bar}  {pct_m:>3}%  {fmt(cost):>8}  ({reqs} reqs){think_s}"))
            items.append(mod_item)

        # Last 7 days
        items.append(None)
        days_item = _mi("Last 7 days")
        for day, cost in r["by_day"]:
            marker = " ◀" if day == date.today().isoformat() else ""
            days_item.add(_mi(f"{day}  {fmt(cost)}{marker}"))
        items.append(days_item)

        # Top projects
        if r["top_projects_week"] or r["top_projects_all"]:
            proj_item = _mi("Top projects")
            if r["top_projects_week"]:
                week_sub = _mi("7 days")
                total    = sum(c for _, c in r["top_projects_week"])
                for cwd, cost in r["top_projects_week"]:
                    name  = (Path(cwd).name or cwd)[:14]
                    bar   = model_bar(cost, total)
                    pct_p = int(cost / total * 100) if total else 0
                    week_sub.add(_mi(f"{name:<14} {bar}  {pct_p:>3}%  {fmt(cost):>8}"))
                proj_item.add(week_sub)
            if r["top_projects_all"]:
                all_sub = _mi("All time")
                total   = sum(c for _, c in r["top_projects_all"])
                for cwd, cost in r["top_projects_all"]:
                    name  = (Path(cwd).name or cwd)[:14]
                    bar   = model_bar(cost, total)
                    pct_p = int(cost / total * 100) if total else 0
                    all_sub.add(_mi(f"{name:<14} {bar}  {pct_p:>3}%  {fmt(cost):>8}"))
                proj_item.add(all_sub)
            items.append(proj_item)

        # Top branches
        if r["top_branches_week"] or r["top_branches_all"]:
            br_item = _mi("Top branches")
            if r["top_branches_week"]:
                week_sub = _mi("7 days")
                total    = sum(c for _, c in r["top_branches_week"])
                for branch, cost in r["top_branches_week"]:
                    bar   = model_bar(cost, total)
                    pct_b = int(cost / total * 100) if total else 0
                    week_sub.add(_mi(f"{branch[:22]:<22} {bar}  {pct_b:>3}%  {fmt(cost):>8}"))
                br_item.add(week_sub)
            if r["top_branches_all"]:
                all_sub = _mi("All time")
                total   = sum(c for _, c in r["top_branches_all"])
                for branch, cost in r["top_branches_all"]:
                    bar   = model_bar(cost, total)
                    pct_b = int(cost / total * 100) if total else 0
                    all_sub.add(_mi(f"{branch[:22]:<22} {bar}  {pct_b:>3}%  {fmt(cost):>8}"))
                br_item.add(all_sub)
            items.append(br_item)

        # Models all time
        if r["models_all"]:
            mod_all   = _mi("Models (all time)")
            total_all = sum(c for _, c, _, _ in r["models_all"])
            for model, cost, reqs, think in r["models_all"]:
                short   = _short_model(model)
                bar     = model_bar(cost, total_all)
                pct_m   = int(cost / total_all * 100) if total_all else 0
                think_s = f"  ↯ {fmt(think)}" if think >= 0.01 else ""
                mod_all.add(_mi(f"{short:<12} {bar}  {pct_m:>3}%  {fmt(cost):>8}  ({reqs} reqs){think_s}"))
            items.append(mod_all)

        # Monthly
        if r["month_rows"]:
            monthly = _mi("Monthly")
            for month, cost, reqs in r["month_rows"]:
                marker = " ◀" if month == this_month else ""
                monthly.add(_mi(f"{month}  {fmt(cost):>8}  ({reqs} reqs){marker}"))
            items.append(monthly)

        # Preferences
        items.append(None)
        prefs = _mi("Preferences")
        prefs.add(rumps.MenuItem(f"Daily Budget: {fmt(budget)}", callback=None))
        prefs.add(rumps.MenuItem("Set Budget…", callback=self._set_budget))
        items.append(prefs)

        # Footer
        items.append(None)
        items.append(_mi(f"All time: {fmt(r['all_cost'])} ({r['all_reqs']} requests)"))
        if r["since"]:
            items.append(_mi(f"Tracked since: {r['since']}"))
        items.append(None)
        items.append(_mi(f"DB: {DB_PATH}"))

        self.menu.clear()
        for item in items:
            self.menu.add(rumps.separator if item is None else item)

    def _set_budget(self, _) -> None:
        budget = get_budget()
        disp   = str(int(budget)) if budget == int(budget) else str(budget)
        w      = rumps.Window(
            message="Enter daily budget in USD (0 to disable alerts):",
            title="Set Daily Budget",
            default_text=disp,
            ok="Save",
            cancel="Cancel",
            dimensions=(200, 20),
        )
        response = w.run()
        if response.clicked:
            try:
                new_budget = float(response.text.strip().lstrip("$"))
                cfg = load_config()
                cfg["daily_budget"] = new_budget
                save_config(cfg)
                self._refresh(None)
            except ValueError:
                rumps.alert("Invalid budget — please enter a number.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-v"):
        print(f"claude-meter {VERSION}")
        sys.exit(0)
    ClaudeMeterApp().run()
