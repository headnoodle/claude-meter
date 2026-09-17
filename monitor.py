#!/usr/bin/env python3
"""
claude-meter — tracks Claude Code API spend per request using local JSONL transcripts.

Runs as a persistent macOS menu bar app via rumps.
Start on login with: brew services start claude-meter
"""

import fcntl
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import rumps

# ── Single-instance lock ─────────────────────────────────────────────────────
_LOCK_PATH = Path.home() / ".claude-meter.lock"
_LOCK_FH   = open(_LOCK_PATH, "w")
try:
    fcntl.flock(_LOCK_FH, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _LOCK_FH.write(str(os.getpid()))
    _LOCK_FH.flush()
except BlockingIOError:
    print("claude-meter is already running — exiting.", file=sys.stderr)
    sys.exit(0)

try:
    from AppKit import (NSAttributedString, NSMutableAttributedString,
                        NSForegroundColorAttributeName,
                        NSColor, NSFont, NSFontAttributeName,
                        NSObject, NSMenu, NSMenuItem as NSRawMenuItem,
                        NSWindow, NSTextField, NSButton, NSBox, NSApplication,
                        NSBezierPath, NSGraphicsContext, NSImage, NSImageView,
                        NSPopUpButton, NSSegmentedControl,
                        NSTextAttachment,
                        NSCompositingOperationSourceIn,
                        NSCompositingOperationSourceOver,
                        NSWorkspace)

    class _ClickHandler(NSObject):
        """Objective-C target for the right-click context menu items."""
        _app = None  # ClaudeMeterApp

        def doRefresh_(self, sender):
            if self._app:
                self._app._refresh(None)

        def doSettings_(self, sender):
            if self._app:
                self._app._set_budget(None)

        def doAbout_(self, sender):
            _show_about_panel()

        def doQuit_(self, sender):
            NSApplication.sharedApplication().terminate_(None)

    class _SettingsHandler(NSObject):
        """Button target for the Settings panel modal."""
        _saved = False

        def cancel_(self, sender):
            NSApplication.sharedApplication().stopModal()

        def save_(self, sender):
            self._saved = True
            NSApplication.sharedApplication().stopModal()

    class _WinDelegate(NSObject):
        """Window delegate that stops the modal when the red close button is clicked."""
        def windowShouldClose_(self, sender):
            NSApplication.sharedApplication().stopModal()
            return True

    class _AboutHandler(NSObject):
        """Close button target for the About panel."""
        def close_(self, sender):
            NSApplication.sharedApplication().stopModal()

    class _LinkHandler(NSObject):
        """GitHub link button target for the About panel."""
        def click_(self, sender):
            from AppKit import NSURL
            NSWorkspace.sharedWorkspace().openURL_(
                NSURL.URLWithString_("https://github.com/headnoodle/claude-meter")
            )

    class _NumericDelegate(NSObject):
        """NSTextFieldDelegate that strips non-numeric characters after each change."""
        _allow_floats = False

        def controlTextDidChange_(self, notification):
            field   = notification.object()
            text    = str(field.stringValue())
            allowed = "0123456789" + ("." if self._allow_floats else "")
            cleaned = "".join(c for c in text if c in allowed)
            if cleaned != text:
                field.setStringValue_(cleaned)

    _HAS_APPKIT = True
except ImportError:
    _HAS_APPKIT = False


def _show_about_panel() -> None:
    """Show a small modal About window."""
    if not _HAS_APPKIT:
        return
    try:
        from AppKit import NSBezelStyleRounded as _BEZEL
    except ImportError:
        _BEZEL = 1

    try:
        from AppKit import NSTextAlignmentCenter as _CENTER
    except ImportError:
        _CENTER = 2

    W, H = 360, 430
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (W, H)), 3, 2, False
    )
    win.setTitle_("About claude-meter")
    win.center()
    win.setReleasedWhenClosed_(False)
    cv = win.contentView()

    # ── Brain icon ───────────────────────────────────────────────────
    cfg       = load_config()
    icon_name = cfg.get("icon", "brain")
    sym = NSImage.imageWithSystemSymbolName_accessibilityDescription_(icon_name, None)
    if sym:
        iv = NSImageView.alloc().initWithFrame_(((W // 2 - 80, 256), (160, 160)))
        iv.setImage_(sym)
        iv.setImageScaling_(3)  # NSImageScaleProportionallyUpOrDown
        cv.addSubview_(iv)

    # ── Text fields ──────────────────────────────────────────────────
    PAD = 20
    def lbl(text, y, size=13, bold=False, secondary=False):
        tf = NSTextField.alloc().initWithFrame_(((PAD, y), (W - PAD * 2, size + 12)))
        tf.setStringValue_(text)
        tf.setBezeled_(False)
        tf.setDrawsBackground_(False)
        tf.setEditable_(False)
        tf.setSelectable_(False)
        tf.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
        tf.setAlignment_(_CENTER)
        if secondary:
            tf.setTextColor_(NSColor.secondaryLabelColor())
        cv.addSubview_(tf)

    lbl("claude-meter", 218, size=22, bold=True)
    lbl(f"Version {VERSION}", 194, size=13, secondary=True)
    lbl("Tracks Claude Code API spend in real time.", 168, size=12, secondary=True)
    lbl("Reads local transcripts — no API key required.", 148, size=12, secondary=True)

    # ── Separator ────────────────────────────────────────────────────
    box = NSBox.alloc().initWithFrame_(((PAD, 134), (W - PAD * 2, 1)))
    box.setBoxType_(2)
    cv.addSubview_(box)

    lbl("MIT License  ·  © headnoodle", 98, size=11, secondary=True)

    # ── GitHub link button ───────────────────────────────────────────
    link = NSButton.alloc().initWithFrame_(((W // 2 - 130, 64), (260, 22)))
    link.setTitle_("github.com/headnoodle/claude-meter")
    link.setBordered_(False)
    link.setBezelStyle_(0)
    link_attrs = {
        NSFontAttributeName: NSFont.systemFontOfSize_(12.0),
        NSForegroundColorAttributeName: NSColor.linkColor(),
    }
    link.setAttributedTitle_(
        NSAttributedString.alloc().initWithString_attributes_(
            "github.com/headnoodle/claude-meter", link_attrs
        )
    )

    _lh = _LinkHandler.alloc().init()
    link.setTarget_(_lh)
    link.setAction_("click:")
    cv.addSubview_(link)

    # ── Close button ─────────────────────────────────────────────────
    sh = _AboutHandler.alloc().init()
    sh._link_handler = _lh  # prevent GC

    close_btn = NSButton.alloc().initWithFrame_(((W // 2 - 44, 12), (88, 28)))
    close_btn.setTitle_("Close")
    close_btn.setBezelStyle_(_BEZEL)
    close_btn.setKeyEquivalent_("\r")
    close_btn.setTarget_(sh)
    close_btn.setAction_("close:")
    cv.addSubview_(close_btn)

    _wd = _WinDelegate.alloc().init()
    win.setDelegate_(_wd)
    sh._win_delegate = _wd

    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    NSApplication.sharedApplication().runModalForWindow_(win)
    win.orderOut_(None)


def _show_settings_panel(app_instance) -> None:
    """Display a proper AppKit settings panel modally."""
    try:
        from AppKit import NSBezelStyleRounded as _BEZEL
    except ImportError:
        _BEZEL = 1  # NSBezelStyleRounded

    budget      = get_budget()
    cfg         = load_config()
    interval    = int(cfg.get("refresh_interval", 60))
    cur_show_today     = cfg.get("show_today", False)
    cur_show_month     = cfg.get("show_month", False)
    cur_show_burn      = cfg.get("show_burn",  False)
    cur_show_reqs      = cfg.get("show_reqs",  False)
    cur_theme          = cfg.get("theme", "system")
    cur_icon           = cfg.get("icon", "brain")
    cur_budget_enabled = cfg.get("budget_enabled", False)

    # ── Database stats (queried once at panel open) ──────────────────
    _db_stat_records  = "—"
    _db_stat_sessions = "—"
    _db_stat_range    = "—"
    _db_stat_size     = "—"
    try:
        with sqlite3.connect(DB_PATH) as _sdb:
            row = _sdb.execute(
                "SELECT COUNT(*), MIN(day), MAX(day) FROM requests"
            ).fetchone()
            if row:
                _db_stat_records = f"{row[0]:,}"
                if row[1] and row[2]:
                    _db_stat_range = (
                        row[1] if row[1] == row[2] else f"{row[1]}  →  {row[2]}"
                    )
            n_sess = _sdb.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            _db_stat_sessions = f"{n_sess:,}"
        _sz = os.path.getsize(DB_PATH)
        _db_stat_size = (
            f"{_sz / 1024 / 1024:.1f} MB" if _sz >= 1024 * 1024 else f"{_sz / 1024:.0f} KB"
        )
    except Exception:
        pass

    W, H  = 440, 718
    PAD   = 20
    LBL_W = 120
    FLD_X = PAD + LBL_W + 10
    FLD_W = W - FLD_X - PAD

    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (W, H)), 3, 2, False  # Titled|Closable, NSBackingStoreBuffered
    )
    win.setTitle_("claude-meter — Settings")
    win.center()
    win.setReleasedWhenClosed_(False)
    cv = win.contentView()

    def lbl(text, x, y, w=None, h=18, bold=False, size=13, secondary=False):
        tf = NSTextField.alloc().initWithFrame_(((x, y), (w or W - x - PAD, h)))
        tf.setStringValue_(text)
        tf.setBezeled_(False)
        tf.setDrawsBackground_(False)
        tf.setEditable_(False)
        tf.setSelectable_(False)
        tf.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
        if secondary:
            tf.setTextColor_(NSColor.secondaryLabelColor())
        cv.addSubview_(tf)
        return tf

    def fld(text, x, y, w, h=22, editable=True, mono=False):
        tf = NSTextField.alloc().initWithFrame_(((x, y), (w, h)))
        tf.setStringValue_(text)
        tf.setEditable_(editable)
        tf.setSelectable_(True)
        if mono:
            f = NSFont.fontWithName_size_("Menlo", 10)
            if f:
                tf.setFont_(f)
        if not editable:
            tf.setTextColor_(NSColor.secondaryLabelColor())
        cv.addSubview_(tf)
        return tf

    def add_sep(y):
        box = NSBox.alloc().initWithFrame_(((PAD, y), (W - 2 * PAD, 1)))
        box.setBoxType_(2)  # NSBoxSeparator
        cv.addSubview_(box)

    def chk(title, x, y, state):
        b = NSButton.alloc().initWithFrame_(((x, y), (W - x - PAD, 22)))
        b.setButtonType_(3)  # NSButtonTypeSwitch
        b.setTitle_(title)
        b.setState_(1 if state else 0)
        cv.addSubview_(b)
        return b

    def seg_row(label_text, label_y, seg_y, seg_keys, seg_icons, cur_val):
        lbl(label_text, PAD, label_y, w=LBL_W)
        s = NSSegmentedControl.alloc().initWithFrame_(((FLD_X, seg_y), (FLD_W, 44)))
        s.setSegmentCount_(len(seg_keys))
        s.setTrackingMode_(0)  # NSSegmentSwitchTrackingSelectOne
        for i, sym_name in enumerate(seg_icons):
            sym_img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(sym_name, None)
            if sym_img:
                s.setImage_forSegment_(sym_img, i)
            s.setLabel_forSegment_("", i)
            s.setWidth_forSegment_(FLD_W / len(seg_keys), i)
        s.setSelectedSegment_(seg_keys.index(cur_val) if cur_val in seg_keys else 0)
        cv.addSubview_(s)
        return s

    # ── Appearance ───────────────────────────────────────────────────
    lbl("APPEARANCE", PAD, 680, bold=True, size=10, secondary=True)

    _theme_keys  = ["system", "light", "dark"]
    _theme_icons = ["circle.lefthalf.filled", "sun.max", "moon"]
    theme_seg = seg_row("Theme", 643, 630, _theme_keys, _theme_icons, cur_theme)

    _icon_keys  = [
        "brain", "brain.head.profile", "brain.filled.head.profile",
        "sparkles", "cpu", "terminal", "bubble.left.and.bubble.right", "waveform",
    ]
    icon_seg = seg_row("Icon", 590, 576, _icon_keys, _icon_keys, cur_icon)

    lbl("Title Shows", PAD, 544, w=LBL_W)
    today_chk = chk("Today's cost",    FLD_X, 544, cur_show_today)
    month_chk = chk("Month's total",   FLD_X, 520, cur_show_month)
    burn_chk  = chk("Burn rate",       FLD_X, 496, cur_show_burn)
    reqs_chk  = chk("Request count",   FLD_X, 472, cur_show_reqs)

    # ── General ──────────────────────────────────────────────────────
    add_sep(424)
    lbl("GENERAL", PAD, 404, bold=True, size=10, secondary=True)

    budget_chk = chk("Enable daily budget alerts", PAD, 380, cur_budget_enabled)

    lbl("Daily Budget", PAD, 352, w=LBL_W)
    raw_budget = get_budget() or float(cfg.get("daily_budget", os.environ.get("CLAUDE_METER_DAILY_BUDGET", 50.0)))
    budget_field = fld(
        str(int(raw_budget) if raw_budget == int(raw_budget) else raw_budget),
        FLD_X, 350, 70,
    )
    _budget_del = _NumericDelegate.alloc().init()
    _budget_del._allow_floats = True
    budget_field.setDelegate_(_budget_del)
    lbl("USD / day", FLD_X + 78, 352, w=100, secondary=True)

    lbl("Refresh Every", PAD, 322, w=LBL_W)
    interval_field = fld(str(interval), FLD_X, 320, 70)
    _interval_del = _NumericDelegate.alloc().init()
    _interval_del._allow_floats = False
    interval_field.setDelegate_(_interval_del)
    lbl("seconds", FLD_X + 78, 322, w=100, secondary=True)

    # ── Paths ────────────────────────────────────────────────────────
    add_sep(306)
    lbl("PATHS", PAD, 286, bold=True, size=10, secondary=True)

    lbl("Claude Data", PAD, 256, w=LBL_W)
    fld(str(CLAUDE_DIR), FLD_X, 254, FLD_W, editable=False, mono=True)

    lbl("Database", PAD, 226, w=LBL_W)
    fld(str(DB_PATH), FLD_X, 224, FLD_W, editable=False, mono=True)

    # ── Database stats ───────────────────────────────────────────────
    add_sep(210)
    lbl("DATABASE", PAD, 190, bold=True, size=10, secondary=True)
    lbl("Records",    PAD, 166, w=LBL_W)
    lbl(_db_stat_records,  FLD_X, 166, w=FLD_W, secondary=True)
    lbl("Sessions",   PAD, 144, w=LBL_W)
    lbl(_db_stat_sessions, FLD_X, 144, w=FLD_W, secondary=True)
    lbl("Date Range", PAD, 122, w=LBL_W)
    lbl(_db_stat_range,    FLD_X, 122, w=FLD_W, secondary=True)
    lbl("File Size",  PAD, 100, w=LBL_W)
    lbl(_db_stat_size,     FLD_X, 100, w=FLD_W, secondary=True)

    # ── Buttons ──────────────────────────────────────────────────────
    add_sep(50)

    sh = _SettingsHandler.alloc().init()

    cancel_btn = NSButton.alloc().initWithFrame_(((W - 196, 14), (88, 28)))
    cancel_btn.setTitle_("Cancel")
    cancel_btn.setBezelStyle_(_BEZEL)
    cancel_btn.setKeyEquivalent_("\x1b")
    cancel_btn.setTarget_(sh)
    cancel_btn.setAction_("cancel:")
    cv.addSubview_(cancel_btn)

    save_btn = NSButton.alloc().initWithFrame_(((W - 100, 14), (80, 28)))
    save_btn.setTitle_("Save")
    save_btn.setBezelStyle_(_BEZEL)
    save_btn.setKeyEquivalent_("\r")
    save_btn.setTarget_(sh)
    save_btn.setAction_("save:")
    cv.addSubview_(save_btn)

    # Pin delegates and controls to sh so they aren't garbage-collected during the modal
    sh._budget_del   = _budget_del
    sh._interval_del = _interval_del
    sh._budget_chk   = budget_chk
    sh._today_chk    = today_chk
    sh._month_chk    = month_chk
    sh._burn_chk     = burn_chk
    sh._reqs_chk     = reqs_chk
    sh._icon_seg     = icon_seg
    sh._theme_seg    = theme_seg

    _wd = _WinDelegate.alloc().init()
    win.setDelegate_(_wd)
    sh._win_delegate = _wd  # prevent GC during modal

    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    NSApplication.sharedApplication().runModalForWindow_(win)
    win.orderOut_(None)

    if sh._saved:
        cfg = load_config()
        try:
            cfg["daily_budget"] = float(
                budget_field.stringValue().strip().lstrip("$")
            )
        except ValueError:
            pass
        try:
            new_iv = int(interval_field.stringValue().strip())
            if new_iv >= 15:
                cfg["refresh_interval"] = new_iv
        except ValueError:
            pass
        cfg["budget_enabled"] = bool(budget_chk.state())
        cfg["show_today"]     = bool(today_chk.state())
        cfg["show_month"]     = bool(month_chk.state())
        cfg["show_burn"]      = bool(burn_chk.state())
        cfg["show_reqs"]      = bool(reqs_chk.state())
        cfg["icon"]           = _icon_keys[icon_seg.selectedSegment()]
        cfg["theme"]          = _theme_keys[theme_seg.selectedSegment()]
        save_config(cfg)
        app_instance._refresh(None)

VERSION       = "0.4.1"
CLAUDE_DIR    = Path.home() / ".claude" / "projects"
DB_PATH       = Path.home() / ".claude-meter.db"
CONFIG_PATH   = Path.home() / ".claude-meter.conf"
_REFRESH_LAST = 0.0  # epoch time of last actual data refresh

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
    if not cfg.get("budget_enabled", False):
        return 0.0
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

    def _parse_jsonl(path: Path, seen: set):
        title = None; cwd = None
        today_cost = total_cost = 0.0
        today_reqs = total_reqs = 0
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    t = d.get("type", "")
                    if t == "custom-title":
                        title = d.get("customTitle") or title
                        continue
                    if t != "assistant" or "input_tokens" not in line:
                        continue
                    rid = d.get("requestId") or d.get("uuid", "")
                    if rid in seen:
                        continue
                    seen.add(rid)
                    msg = d.get("message", {})
                    u   = msg.get("usage", {})
                    if not u or "input_tokens" not in u:
                        continue
                    if cwd is None:
                        cwd = d.get("cwd") or None
                    cost = request_cost(u, msg.get("model", ""))
                    ts   = d.get("timestamp", "")
                    total_cost += cost
                    total_reqs += 1
                    if ts[:10] == today:
                        today_cost += cost
                        today_reqs += 1
        except OSError:
            pass
        return today_cost, today_reqs, total_cost, total_reqs, title, cwd

    results = []
    for jsonl in CLAUDE_DIR.rglob("*.jsonl"):
        if "subagents" in jsonl.parts:
            continue
        try:
            mtime = jsonl.stat().st_mtime
            if datetime.fromtimestamp(mtime).date() != date.today():
                continue
        except OSError:
            continue

        seen = set()
        tc, tr, tot_c, tot_r, title, cwd = _parse_jsonl(jsonl, seen)
        last_ts   = mtime
        sub_tc    = 0.0
        sub_count = 0
        sub_peak  = 0.0

        sub_dir = jsonl.parent / jsonl.stem / "subagents"
        if sub_dir.exists():
            for sub in sub_dir.glob("*.jsonl"):
                s_tc, s_tr, s_tot_c, s_tot_r, s_title, s_cwd = _parse_jsonl(sub, seen)
                if s_tr > 0:
                    sub_count += 1
                    sub_peak = max(sub_peak, s_tc)
                tc += s_tc; tr += s_tr
                sub_tc  += s_tc
                tot_c   += s_tot_c; tot_r += s_tot_r
                if title is None:
                    title = s_title
                if cwd is None:
                    cwd = s_cwd
                try:
                    last_ts = max(last_ts, sub.stat().st_mtime)
                except OSError:
                    pass

        if tr == 0:
            continue

        dir_name = Path(cwd).name if cwd else ""
        label = title or dir_name or jsonl.stem[:8]
        results.append({
            "label":      label,
            "cwd":        cwd or "",
            "total_cost": tot_c,
            "today_cost": tc,
            "sub_cost":   sub_tc,
            "sub_peak":   sub_peak,
            "sub_count":  sub_count,
            "total_reqs": tot_r,
            "today_reqs": tr,
            "last_ts":    last_ts,
        })

    return sorted(results, key=lambda x: x["last_ts"], reverse=True)


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


_MENLO: object = None


def _menlo_font():
    global _MENLO
    if _HAS_APPKIT and _MENLO is None:
        _MENLO = NSFont.fontWithName_size_("Menlo", 12.0)
    return _MENLO


def _ns_color(color):
    if not _HAS_APPKIT:
        return None
    if not isinstance(color, str):
        return color  # already an NSColor
    r = int(color[1:3], 16) / 255.0
    g = int(color[3:5], 16) / 255.0
    b = int(color[5:7], 16) / 255.0
    return NSColor.colorWithRed_green_blue_alpha_(r, g, b, 1.0)


def _make_status_icon(has_alert: bool, icon_name: str = "brain",
                       is_dark: bool = True) -> "NSImage | None":
    """Return the menu bar SF Symbol, optionally with a small orange badge dot.

    For the no-alert case we return a template image so macOS handles the
    light/dark colour automatically.  For the badge case we must draw the
    symbol explicitly — we use white on dark bars, black on light bars.
    Always draws into a fresh owned NSImage so setTemplate_ is reliable.
    """
    if not _HAS_APPKIT:
        return None
    sym = NSImage.imageWithSystemSymbolName_accessibilityDescription_(icon_name, None)
    if sym is None:
        return None
    if not has_alert:
        # SF Symbol images are already template images — system handles
        # light/dark colouring automatically, no drawing needed.
        return sym

    # Badge case: fresh owned image with explicit tinting + orange dot.
    size = 18.0
    fresh = NSImage.alloc().initWithSize_((size, size))
    fresh.lockFocus()
    # Explicitly clear to transparent so there is no opaque background.
    NSColor.clearColor().setFill()
    NSBezierPath.bezierPathWithRect_(((0, 0), (size, size))).fill()
    # Draw symbol (black shape on transparent).
    sym.drawInRect_fromRect_operation_fraction_(
        ((0, 0), (size, size)), ((0, 0), (0, 0)), 2, 1.0
    )
    # Tint to the correct menu-bar foreground colour.
    # NSColor.set() only affects path fills — drawn NSImage pixels are unaffected.
    # SourceIn (4): result = fill_colour × dest_alpha → only recolours where symbol is.
    ctx = NSGraphicsContext.currentContext()
    ctx.setCompositingOperation_(NSCompositingOperationSourceIn)
    (NSColor.whiteColor() if is_dark else NSColor.blackColor()).setFill()
    NSBezierPath.bezierPathWithRect_(((0, 0), (size, size))).fill()
    ctx.setCompositingOperation_(NSCompositingOperationSourceOver)
    # Orange badge circle (bottom-right corner)
    d = 9.0
    cx, cy = size - d / 2 - 0.5, d / 2 + 0.5
    NSColor.systemOrangeColor().setFill()
    NSBezierPath.bezierPathWithOvalInRect_(((cx - d / 2, cy - d / 2), (d, d))).fill()
    # White "!" centred inside the badge
    attrs = {
        NSFontAttributeName: NSFont.boldSystemFontOfSize_(7.0),
        NSForegroundColorAttributeName: NSColor.whiteColor(),
    }
    astr = NSAttributedString.alloc().initWithString_attributes_("!", attrs)
    tw, th = astr.size().width, astr.size().height
    astr.drawAtPoint_((cx - tw / 2, cy - th / 2))
    fresh.unlockFocus()
    return fresh


def _palette(theme: str = "system") -> dict:
    """Return color palette for the given theme.

    'system' uses adaptive NSColor values that flip automatically between
    light and dark. 'light' and 'dark' use fixed hex palettes.
    """
    if theme == "dark":
        return {
            "green":  "#51cf66",
            "orange": "#ffa94d",
            "red":    "#ff6b6b",
            "dim":    "#868e96",
            "label":  "#f1f3f5",
            "bar_dim": "#4a5568",
        }
    if theme == "light":
        return {
            "green":  "#2f9e44",
            "orange": "#e8590c",
            "red":    "#c92a2a",
            "dim":    "#6c757d",
            "label":  "#212529",
            "bar_dim": "#adb5bd",
        }
    # system — adaptive NSColor (works in light and dark automatically)
    if _HAS_APPKIT:
        return {
            "green":  NSColor.systemGreenColor(),
            "orange": NSColor.systemOrangeColor(),
            "red":    NSColor.systemRedColor(),
            "dim":    NSColor.secondaryLabelColor(),
            "label":  NSColor.labelColor(),
            "bar_dim": NSColor.tertiaryLabelColor(),
        }
    return {
        "green": "#51cf66", "orange": "#ffa94d", "red": "#ff6b6b",
        "dim": "#868e96",   "label":  "#f1f3f5", "bar_dim": "#4a5568",
    }


def _styled(title: str, color: str = None, mono: bool = False) -> rumps.MenuItem:
    """Display-only menu item with optional color and monospace font."""
    item = rumps.MenuItem(title, callback=lambda _: None)
    if not _HAS_APPKIT:
        return item
    attrs = {}
    if color:
        attrs[NSForegroundColorAttributeName] = _ns_color(color)
    if mono:
        f = _menlo_font()
        if f:
            attrs[NSFontAttributeName] = f
    if attrs:
        item._menuitem.setAttributedTitle_(
            NSAttributedString.alloc().initWithString_attributes_(title, attrs)
        )
    return item


def _rich_item(*segments) -> rumps.MenuItem:
    """Multi-segment menu item. Each segment: (text, color=None, bold=False, mono=False).

    Lets different parts of one line have different colours and weights —
    e.g. a grey label alongside a bold, coloured value.
    """
    full_text = "".join(seg[0] for seg in segments)
    item = rumps.MenuItem(full_text, callback=lambda _: None)
    if not _HAS_APPKIT:
        return item
    ns_str = NSMutableAttributedString.alloc().initWithString_attributes_(full_text, {})
    pos = 0
    for seg in segments:
        text  = seg[0]
        color = seg[1] if len(seg) > 1 else None
        bold  = seg[2] if len(seg) > 2 else False
        mono  = seg[3] if len(seg) > 3 else False
        n     = len(text)
        rng   = (pos, n)
        if color:
            ns_str.addAttribute_value_range_(NSForegroundColorAttributeName, _ns_color(color), rng)
        font = None
        if mono:
            font = NSFont.fontWithName_size_("Menlo-Bold" if bold else "Menlo", 12.0)
        elif bold:
            font = NSFont.boldSystemFontOfSize_(13.0)
        if font:
            ns_str.addAttribute_value_range_(NSFontAttributeName, font, rng)
        pos += n
    item._menuitem.setAttributedTitle_(ns_str)
    return item


def _sparkline_item(values: list, today_color) -> rumps.MenuItem:
    """Sparkline drawn as an NSImage rect chart — avoids font-baseline artefacts in light mode."""
    if not values:
        if _HAS_APPKIT:
            return _styled("7 days  (no data)", color=NSColor.secondaryLabelColor())
        return rumps.MenuItem("7 days  (no data)", callback=lambda _: None)

    if not _HAS_APPKIT:
        blocks = "▁▂▃▄▅▆▇█"
        max_v = max(values) or 1
        bars = "".join(blocks[min(7, int(v / max_v * 7.999))] * 2 for v in values)
        return rumps.MenuItem("7 days  " + bars, callback=lambda _: None)

    n = len(values)
    bar_w, gap = 6, 2
    img_h      = 26.0
    label_w    = 68.0
    img_w      = label_w + n * (bar_w + gap) - gap + 6.0

    img = NSImage.alloc().initWithSize_((img_w, img_h))
    img.lockFocus()
    NSColor.clearColor().setFill()
    NSBezierPath.bezierPathWithRect_(((0, 0), (img_w, img_h))).fill()

    # "7 days" label, vertically centred
    lbl_attrs = {
        NSFontAttributeName: NSFont.systemFontOfSize_(11.0),
        NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
    }
    lbl_str = NSAttributedString.alloc().initWithString_attributes_("7 days", lbl_attrs)
    lsz = lbl_str.size()
    lbl_str.drawAtPoint_((0, (img_h - lsz.height) / 2))

    # bars — grow from bottom; today is highlighted
    max_v    = max(values) or 1
    dim_c    = NSColor.tertiaryLabelColor()
    today_ns = _ns_color(today_color)
    for i, v in enumerate(values):
        bh = max(2.0, v / max_v * (img_h - 2.0))
        x  = label_w + i * (bar_w + gap)
        (today_ns if i == len(values) - 1 else dim_c).setFill()
        NSBezierPath.bezierPathWithRect_(((x, 1.0), (bar_w, bh))).fill()

    img.unlockFocus()

    try:
        attachment = NSTextAttachment.alloc().init()
        attachment.setImage_(img)
        astr = NSAttributedString.attributedStringWithAttachment_(attachment)
        item = rumps.MenuItem("", callback=lambda _: None)
        item._menuitem.setAttributedTitle_(astr)
    except Exception:
        item = rumps.MenuItem(" 7 days", callback=lambda _: None)
        item._menuitem.setImage_(img)
    return item


def _mi(title: str) -> rumps.MenuItem:
    return rumps.MenuItem(title)


# ---------------------------------------------------------------------------
# Menu bar app
# ---------------------------------------------------------------------------

class ClaudeMeterApp(rumps.App):
    def __init__(self):
        super().__init__(" ", quit_button=None)
        if _HAS_APPKIT:
            # Hide from Dock and Cmd+Tab — menu bar only
            NSApplication.sharedApplication().setActivationPolicy_(1)
        self._click_setup_done = False
        self._status_btn = None
        self._refresh(None)

    @rumps.timer(1)
    def _setup_click_handler(self, _):
        """Wire up right-click context menu on the status bar button.

        Runs once, 1 s after the event loop starts (when nsstatusitem exists).
        Left-click  → native setMenu_ behaviour (works on first click always).
        Right-click → intercepted by a local NSEvent monitor; shows a small
                      context menu without disturbing the native left-click path.
        """
        if self._click_setup_done or not _HAS_APPKIT:
            return
        self._click_setup_done = True
        try:
            from AppKit import NSEvent
            si = self._nsapp.nsstatusitem

            # Monochrome SF Symbol template icon — adapts to light/dark menu bar
            try:
                _icon = load_config().get("icon", "brain")
                img = _make_status_icon(False, _icon)
                if img:
                    si.button().setImage_(img)
                    si.button().setImageScaling_(2)  # NSImageScaleProportionallyDown
                self._status_btn = si.button()  # keep ref for badge updates
            except Exception:
                self._status_btn = None

            # Ensure native menu is attached (left-click works on first click)
            si.setMenu_(self.menu._menu)

            # Build right-click context menu
            ctx = NSMenu.alloc().init()
            ctx.setAutoenablesItems_(False)

            handler = _ClickHandler.alloc().init()
            handler._app = self
            self._click_handler = handler  # prevent GC

            for title, sel in (
                ("↺  Refresh",  "doRefresh:"),
                ("⚙  Settings", "doSettings:"),
                ("ℹ  About",    "doAbout:"),
                ("↩  Restart",  "doQuit:"),
            ):
                mi = NSRawMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, "")
                mi.setEnabled_(True)
                mi.setTarget_(handler)
                ctx.addItem_(mi)

            # Intercept right-clicks before they reach the status item
            # (which would otherwise show the main menu on right-click too)
            def _on_right_click(event):
                si.popUpStatusItemMenu_(ctx)
                return None  # consume the event

            self._right_click_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                8,  # NSEventMaskRightMouseDown
                _on_right_click,
            )
        except Exception:
            pass
        # Now that the status item exists, apply the correct title and icon.
        self._refresh(None)

    @rumps.timer(15)
    def _refresh(self, sender):
        global _REFRESH_LAST
        # When called from the timer (sender is not None), respect the
        # configured interval; direct calls (sender=None) always run.
        if sender is not None and _REFRESH_LAST > 0:
            cfg      = load_config()
            interval = max(15, int(cfg.get("refresh_interval", 60)))
            if time.time() - _REFRESH_LAST < interval:
                return
        _REFRESH_LAST = time.time()
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

        cfg        = load_config()
        _parts = []
        if cfg.get("show_today", False):
            _parts.append(fmt(r["today_cost"]))
        if cfg.get("show_month", False):
            _parts.append(f"{fmt(month_cost)} month")
        if cfg.get("show_burn", False) and burn >= 0.01:
            _parts.append(f"{fmt(burn)}/hr")
        if cfg.get("show_reqs", False):
            _parts.append(f"{r['today_reqs']}r")
        self.title = (" " + "  ·  ".join(_parts)) if _parts else " "

        # Update icon badge (orange dot when over budget)
        if self._status_btn is not None:
            try:
                _icon_name = cfg.get("icon", "brain")
                try:
                    ea = self._status_btn.effectiveAppearance()
                    _is_dark = ea is not None and "Dark" in (ea.name() or "")
                except Exception:
                    _is_dark = True
                icon = _make_status_icon(over_budget, _icon_name, _is_dark)
                if icon:
                    self._status_btn.setImage_(icon)
                    self._status_btn.setImageScaling_(2)
            except Exception:
                pass

        # Palette — adaptive by default, overridable via theme setting
        theme = cfg.get("theme", "system")
        pal   = _palette(theme)
        C_GREEN  = pal["green"]
        C_ORANGE = pal["orange"]
        C_RED    = pal["red"]
        C_DIM    = pal["dim"]
        C_LABEL  = pal["label"]

        items = []

        # ── Today summary ────────────────────────────────────────────────
        today_val_c = C_RED if over_budget else C_LABEL
        think_segs  = [("  · ↯ ", C_ORANGE), (fmt(r["today_thinking"]), C_ORANGE, True), (" thinking", C_ORANGE)] \
                      if r["today_thinking"] >= 0.01 else []
        budget_segs = [("  (budget: ", C_DIM), (fmt(budget), C_DIM), (")", C_DIM)] \
                      if budget > 0 else []
        items.append(_rich_item(
            ("Today: ", C_DIM),
            (fmt(r["today_cost"]), today_val_c, True),
            (f" ({r['today_reqs']} requests)", C_DIM),
            *think_segs,
            *budget_segs,
        ))

        bar_color = C_DIM  # fallback if no budget
        if budget > 0:
            bar, pct_f = budget_bar(r["today_cost"], budget)
            bar_color  = C_RED if pct_f >= 0.85 else C_ORANGE if pct_f >= 0.6 else C_GREEN
            items.append(_rich_item(
                (bar, bar_color, False, True),
                (f"  {pct_f*100:.0f}%", bar_color, True),
                (" of ", C_DIM),
                (fmt(budget), C_DIM),
            ))

        burn_color = C_ORANGE if burn >= 0.01 else C_DIM
        items.append(_rich_item(
            ("Burn rate: ", C_DIM),
            (f"{fmt(burn)}/hr", burn_color, True),
            ("  (rolling 1h)", C_DIM),
        ))

        if r["trend_pct"] is not None:
            pct         = r["trend_pct"]
            arrow       = "▲" if pct >= 0 else "▼"
            trend_color = C_RED if pct >= 0 else C_GREEN
            items.append(_rich_item(
                ("Trend: ", C_DIM),
                (f"{arrow} {abs(pct):.0f}%", trend_color, True),
                (f" vs 7d avg (", C_DIM),
                (fmt(r["avg_daily"]), C_DIM),
                ("/day)", C_DIM),
            ))

        cr_tok, ct_tok, c_saved = r["cache_today"]
        if ct_tok > 0:
            hit_rate    = cr_tok / ct_tok
            cache_color = C_GREEN if hit_rate >= 0.70 else C_ORANGE if hit_rate >= 0.40 else C_RED
            bar         = cache_bar(hit_rate)
            items.append(_rich_item(
                ("Cache  ", C_DIM),
                (bar, cache_color, False, True),
                (f"  {hit_rate*100:.0f}%", cache_color, True),
                (" hit  · saved ", C_DIM),
                (fmt(c_saved), cache_color, True),
            ))

        # sparkline is rendered further down, just before Last 7 days
        day_costs = [c for _, c in reversed(r["by_day"])]
        today_hl  = bar_color if budget > 0 else C_ORANGE

        # ── Sessions (flat, not submenu) ─────────────────────────────────
        if sessions:
            items.append(None)
            items.append(_styled(f"{'Session':<18}{'total':>8}  {'main':>8}  {'agents':>8}  {'peak':>8}  {'subs':>4}", color=C_DIM, mono=True))
            for s in sessions:
                name      = s["label"][:18]
                sub_c     = s["sub_cost"]
                sub_n     = s["sub_count"]
                main_c    = s["today_cost"] - sub_c
                agent_str = fmt(sub_c)        if sub_n else "—"
                peak_str  = fmt(s["sub_peak"]) if sub_n else "—"
                subs_str  = str(sub_n)         if sub_n else "—"
                items.append(_rich_item(
                    (f"{name:<18}",               C_LABEL,  True,  True),
                    (f"{fmt(s['today_cost']):>8}", C_GREEN,  False, True),
                    ("  ",                         None,     False, True),
                    (f"{fmt(main_c):>8}",          C_DIM,    False, True),
                    ("  ",                         None,     False, True),
                    (f"{agent_str:>8}",            C_ORANGE, False, True),
                    ("  ",                         None,     False, True),
                    (f"{peak_str:>8}",             C_DIM,    False, True),
                    ("  ",                         None,     False, True),
                    (f"{subs_str:>4}",             C_DIM,    False, True),
                ))

        # ── Models today (flat, not submenu) ─────────────────────────────
        if r["models_today"]:
            items.append(None)
            items.append(_styled("Models today", color=C_DIM))
            total_today = sum(c for _, c, _, _ in r["models_today"])
            for model, cost, reqs, think in r["models_today"]:
                short = _short_model(model)
                bar   = model_bar(cost, total_today)
                pct_m = int(cost / total_today * 100) if total_today else 0
                mc    = C_RED if "opus" in model.lower() else C_GREEN if "haiku" in model.lower() else C_ORANGE
                # All Menlo — bold name for emphasis, fixed padding keeps columns locked
                segs = [
                    (f"{short:<12} ", mc,        True,  True),
                    (bar,             mc,        False, True),
                    (f"  {pct_m:>3}%",mc,        False, True),
                    (f"  {fmt(cost):>8}", C_LABEL, False, True),
                    (f"  ({reqs} reqs)", C_DIM,   False, True),
                ]
                if think >= 0.01:
                    segs += [("  ↯ ", C_ORANGE, False, True), (fmt(think), C_ORANGE, False, True)]
                items.append(_rich_item(*segs))

        # ── 7-day sparkline ──────────────────────────────────────────────
        if len(day_costs) > 1:
            items.append(None)
            items.append(_sparkline_item(day_costs, today_hl))

        # ── Last 7 days ──────────────────────────────────────────────────
        items.append(None)
        days_item = _mi("Last 7 days")
        today_iso = date.today().isoformat()
        for day, cost in r["by_day"]:
            marker = " ◀" if day == today_iso else ""
            days_item.add(_styled(f"{day}  {fmt(cost)}{marker}", color=C_GREEN if day == today_iso else None, mono=True))
        items.append(days_item)

        # ── Top projects ────────────────────────────────────────────────
        if r["top_projects_week"] or r["top_projects_all"]:
            proj_item = _mi("Top projects")
            for label, rows in (("7 days", r["top_projects_week"]), ("All time", r["top_projects_all"])):
                if not rows:
                    continue
                sub   = _mi(label)
                total = sum(c for _, c in rows)
                for cwd, cost in rows:
                    name  = (Path(cwd).name or cwd)[:14]
                    bar   = model_bar(cost, total)
                    pct_p = int(cost / total * 100) if total else 0
                    sub.add(_rich_item(
                        (f"{name:<14} ", C_LABEL,  True,  True),
                        (bar,            C_ORANGE,  False, True),
                        (f"  {pct_p:>3}%", C_ORANGE, False, True),
                        (f"  {fmt(cost):>8}", C_LABEL,  False, True),
                    ))
                proj_item.add(sub)
            items.append(proj_item)

        # ── Top branches ────────────────────────────────────────────────
        if r["top_branches_week"] or r["top_branches_all"]:
            br_item = _mi("Top branches")
            for label, rows in (("7 days", r["top_branches_week"]), ("All time", r["top_branches_all"])):
                if not rows:
                    continue
                sub   = _mi(label)
                total = sum(c for _, c in rows)
                for branch, cost in rows:
                    bar   = model_bar(cost, total)
                    pct_b = int(cost / total * 100) if total else 0
                    sub.add(_rich_item(
                        (f"{branch[:22]:<22} ", C_LABEL,  True,  True),
                        (bar,                   C_ORANGE,  False, True),
                        (f"  {pct_b:>3}%",       C_ORANGE, False, True),
                        (f"  {fmt(cost):>8}",    "#f1f3f5", False, True),
                    ))
                br_item.add(sub)
            items.append(br_item)

        # ── Models all time ──────────────────────────────────────────────
        if r["models_all"]:
            mod_all   = _mi("Models (all time)")
            total_all = sum(c for _, c, _, _ in r["models_all"])
            for model, cost, reqs, think in r["models_all"]:
                short = _short_model(model)
                bar   = model_bar(cost, total_all)
                pct_m = int(cost / total_all * 100) if total_all else 0
                mc    = C_RED if "opus" in model.lower() else C_GREEN if "haiku" in model.lower() else C_ORANGE
                segs  = [
                    (f"{short:<12} ", mc,        True,  True),
                    (bar,             mc,        False, True),
                    (f"  {pct_m:>3}%",mc,        False, True),
                    (f"  {fmt(cost):>8}", C_LABEL, False, True),
                    (f"  ({reqs} reqs)", C_DIM,   False, True),
                ]
                if think >= 0.01:
                    segs += [("  ↯ ", C_ORANGE, False, True), (fmt(think), C_ORANGE, False, True)]
                mod_all.add(_rich_item(*segs))
            items.append(mod_all)

        # ── Monthly ──────────────────────────────────────────────────────
        if r["month_rows"]:
            monthly = _mi("Monthly")
            for month, cost, reqs in r["month_rows"]:
                mc = C_GREEN if month == this_month else None
                monthly.add(_styled(f"{month}  {fmt(cost):>8}  ({reqs} reqs){' ◀' if month == this_month else ''}", color=mc, mono=True))
            items.append(monthly)

        # ── Footer ───────────────────────────────────────────────────────
        items.append(None)
        items.append(_styled(f"All time: {fmt(r['all_cost'])} ({r['all_reqs']} requests)", color=C_GREEN))
        if r["since"]:
            items.append(_styled(f"Tracked since: {r['since']}", color=C_GREEN))
        self.menu.clear()
        for item in items:
            self.menu.add(rumps.separator if item is None else item)

    def _set_budget(self, _) -> None:
        if _HAS_APPKIT:
            _show_settings_panel(self)
        else:
            budget = get_budget()
            disp   = str(int(budget)) if budget == int(budget) else str(budget)
            w      = rumps.Window(
                message="Daily budget in USD (0 to disable alerts):",
                title="Settings",
                default_text=disp,
                ok="Save",
                cancel="Cancel",
                dimensions=(200, 20),
            )
            response = w.run()
            if response.clicked:
                try:
                    cfg = load_config()
                    cfg["daily_budget"] = float(response.text.strip().lstrip("$"))
                    save_config(cfg)
                    self._refresh(None)
                except ValueError:
                    pass


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-v"):
        print(f"claude-meter {VERSION}")
        sys.exit(0)
    ClaudeMeterApp().run()
