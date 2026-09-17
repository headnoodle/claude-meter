# claude-meter

macOS menu bar tool that tracks Claude Code API spend in real time, reading directly from local transcripts.

![xbar menu bar screenshot showing today cost, monthly cost, burn rate, cache hit rate and sparkline](docs/screenshot.png)

## Features

- **Live spend tracking** — reads Claude Code JSONL transcripts every minute
- **Persistent storage** — SQLite database survives the ~30-day transcript rotation
- **Budget alerts** — macOS notifications at 50%, 75%, and 100% of your daily limit
- **Burn rate** — rolling 1-hour spend rate
- **Trend indicator** — today vs 7-day average, colour-coded
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
brew tap GITHUB_USER/tap
brew install claude-meter
```

Then follow the printed caveats to symlink the xbar plugin.

### Manual

```bash
# 1. Clone the repo
git clone https://github.com/GITHUB_USER/claude-meter.git ~/repos/claude-meter

# 2. Install xbar
brew install --cask xbar

# 3. Run the installer (symlinks the plugin and adds xbar to Login Items)
bash ~/repos/claude-meter/install.sh

# 4. Open xbar
open -a xbar
```

## Configuration

Right-click the menu bar item → **Open xbar Settings** to set your daily budget. The default is $50.

| Setting | Default | Description |
|---|---|---|
| `CLAUDE_METER_DAILY_BUDGET` | `50` | Daily spend limit in USD. Set to `0` to disable alerts. |

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
  monitor.py               # core logic — ingestion, DB, reporting, xbar output
  xbar-plugin/
    claude_tokens.1m.py    # thin xbar wrapper (1m refresh interval)
  Formula/
    claude-meter.rb        # Homebrew formula
  install.sh               # setup script
```

The database lives at `~/.claude-meter.db` (outside the repo).

## Development

```bash
# Test output locally
python3 ~/repos/claude-meter/monitor.py

# Check version
python3 ~/repos/claude-meter/monitor.py --version

# Re-symlink after moving the repo
ln -sf ~/repos/claude-meter/xbar-plugin/claude_tokens.1m.py \
       ~/Library/Application\ Support/xbar/plugins/claude_tokens.1m.py
```

## Requirements

- macOS
- [xbar](https://xbarapp.com)
- Python 3.9+ (ships with macOS)
- Claude Code CLI (`claude` command)

## License

MIT
