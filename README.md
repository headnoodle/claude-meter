# claude-meter

macOS menu bar tool that tracks Claude Code API spend in real time, reading directly from local transcripts — no API key required.

---

## Install

```bash
brew tap headnoodle/tap
brew install claude-meter
brew services start claude-meter
```

The brain icon appears in your menu bar within a few seconds. Right-click it for **Settings**, **Refresh**, or **Restart**.

To upgrade:

```bash
brew upgrade claude-meter   # also restarts the service automatically
```

---

## Menu bar

By default only the icon is shown. Open **Settings → Title Shows** to add any combination of:

| Option | Example |
|--------|---------|
| Today's cost | `$1.24` |
| Month's total | `$18.40 month` |
| Burn rate | `$0.43/hr` |
| Request count | `14r` |

Active items are joined with `·`, e.g. `$1.24  ·  $18.40 month  ·  14r`.

---

## Settings

Right-click the icon → **Settings**.

### Appearance

- **Theme** — System / Light / Dark
- **Icon** — choose from 8 SF Symbols: three brain variants, sparkles, cpu, terminal, chat bubbles, waveform
- **Title Shows** — toggle each title component on/off independently

### General

- **Enable daily budget alerts** — off by default; when on, shows a macOS notification at 50 %, 75 %, and 100 % of your limit and adds an `!` badge to the icon when over budget
- **Daily Budget** — USD / day limit (only used when budget alerts are enabled)
- **Refresh Every** — polling interval in seconds (default 60)

### Paths

Read-only display of the Claude transcript directory and database location.

### Database

Read-only stats: record count, session count, date range covered, and file size.

---

## Menu

Left-click the icon to open the main menu:

- **Spend summary** — today, month, burn rate
- **Budget bar** — progress towards your daily limit (when enabled)
- **Cache efficiency** — hit rate and estimated savings
- **Active sessions** — per-project spend for today vs full session
- **Model breakdown** — proportional bar charts with thinking token costs
- **7-day sparkline** — visual history of the last week
- **Last 7 days** — daily breakdown
- **Top projects** — this week and all time
- **Top branches** — cost per git branch
- **All-time totals** — model breakdown over the full database history

---

## Configuration

Settings are stored in `~/.claude-meter.conf` (JSON). The app writes this file when you save from the Settings panel. You can also edit it directly:

```json
{
  "icon": "brain",
  "theme": "system",
  "show_today": false,
  "show_month": false,
  "show_burn": false,
  "show_reqs": false,
  "budget_enabled": false,
  "daily_budget": 50,
  "refresh_interval": 60
}
```

---

## Database

All data is stored in `~/.claude-meter.db` (SQLite). It persists permanently — through Claude Code's ~30-day transcript rotation, upgrades, and reinstalls.

```bash
# Daily totals
sqlite3 ~/.claude-meter.db \
  "SELECT day, COUNT(*), ROUND(SUM(cost),4) FROM requests GROUP BY day ORDER BY day DESC LIMIT 14"

# Model usage
sqlite3 ~/.claude-meter.db \
  "SELECT model, COUNT(*), ROUND(SUM(cost),4) FROM requests GROUP BY model ORDER BY SUM(cost) DESC"

# All distinct models seen
sqlite3 ~/.claude-meter.db \
  "SELECT DISTINCT model FROM requests ORDER BY model"
```

---

## Pricing

Pricing lives in the `PRICING` dict in `monitor.py`. Update it when Anthropic releases new models or changes rates. To find the exact model ID string after using a new model:

```bash
sqlite3 ~/.claude-meter.db \
  "SELECT DISTINCT model FROM requests ORDER BY rowid DESC LIMIT 10"
```

---

## Development

```bash
git clone https://github.com/headnoodle/claude-meter.git
cd claude-meter

# Install dependencies
pip3 install rumps pyobjc-core pyobjc-framework-Cocoa

# Run
python3 monitor.py
```

The app enforces a single instance — a second launch exits immediately if one is already running (via an `fcntl` lock on `~/.claude-meter.lock`).

---

## Releasing

1. Make changes to `monitor.py`
2. Commit and tag: `git tag vX.Y.Z && git push origin main vX.Y.Z`
3. Create a GitHub release for the tag
4. Get the tarball sha256:
   ```bash
   curl -sL https://github.com/headnoodle/claude-meter/archive/refs/tags/vX.Y.Z.tar.gz | shasum -a 256
   ```
5. Update `url` and `sha256` in both:
   - `Formula/claude-meter.rb` (this repo)
   - `headnoodle/homebrew-tap` → `Formula/claude-meter.rb`

---

## Requirements

- macOS 12+
- Python 3.12 (installed automatically by Homebrew)

## License

MIT
