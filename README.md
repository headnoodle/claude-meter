# claude-meter

macOS menu bar tool that tracks Claude Code API spend in real time, reading directly from local transcripts.

## Features

- **Live spend tracking** — reads Claude Code JSONL transcripts every minute
- **Persistent storage** — SQLite database survives the ~30-day transcript rotation
- **Budget alerts** — macOS notifications at 50%, 75%, and 100% of your daily limit
- **In-app budget config** — set your daily limit via the menu without touching env vars
- **Burn rate** — rolling 1-hour spend rate
- **Trend indicator** — today vs 7-day average
- **Sparkline** — one-line visual of the last 7 days
- **Cache efficiency** — hit rate bar with estimated dollar savings
- **Active sessions** — per-project today cost vs full session cost
- **Model breakdown** — proportional bar charts with thinking token cost
- **Project breakdown** — top projects by spend this week and all time
- **Branch breakdown** — cost per git branch
- **Monthly rollup** — 6-month history

## Install

### Homebrew (recommended)

```bash
brew tap headnoodle/tap
brew install claude-meter
brew services start claude-meter
```

The 🤖 icon appears in your menu bar. To set a daily budget, click it → Preferences → Set Budget…

### Manual

```bash
git clone https://github.com/headnoodle/claude-meter.git ~/repos/claude-meter
bash ~/repos/claude-meter/install.sh
```

## Configuration

Click **🤖 → Preferences → Set Budget…** to set a daily spend limit. The default is $50.

You can also set it via environment variable or `~/.claude-meter.conf`:

```json
{ "daily_budget": 100 }
```

Set to `0` to disable budget alerts.

## Database

All data is stored in `~/.claude-meter.db`. The database persists permanently — it survives both Claude Code's ~30-day transcript rotation and reinstalls.

```bash
# Check daily totals
sqlite3 ~/.claude-meter.db \
  "SELECT day, COUNT(*), ROUND(SUM(cost),2) FROM requests GROUP BY day ORDER BY day"
```

## Pricing

Pricing is defined in the `PRICING` dict in `monitor.py`. Update it when Anthropic releases new models or changes rates.

To find the model ID string for a new model after using it:

```bash
sqlite3 ~/.claude-meter.db \
  "SELECT DISTINCT model FROM requests ORDER BY ts DESC LIMIT 10"
```

## Files

```
claude-meter/
  monitor.py            # core logic — ingestion, DB, reporting, menu bar app
  Formula/
    claude-meter.rb     # Homebrew formula
  install.sh            # manual install script
```

The database lives at `~/.claude-meter.db` (outside the repo).
Config lives at `~/.claude-meter.conf` (written by the app when you set a budget).

## Development

```bash
# Install rumps for local testing
pip3 install rumps

# Run the app
python3 ~/repos/claude-meter/monitor.py

# Check version
python3 ~/repos/claude-meter/monitor.py --version
```

## Requirements

- macOS
- Python 3.9+ (ships with macOS)
- `rumps` Python package (installed automatically by Homebrew or `install.sh`)

## License

MIT
