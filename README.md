# claude-meter

macOS menu bar tool that tracks Claude Code API spend in real time.

## How it works

Claude Code writes a JSONL transcript for every session under `~/.claude/projects/`. Each assistant response entry contains a `timestamp`, model name, and token counts. claude-meter reads those entries every minute, calculates cost using Anthropic's published per-token pricing, and persists each request to a local SQLite database (`~/.claude-meter.db`).

Because data is persisted immediately, it survives the ~30-day transcript rotation Claude Code applies automatically.

## Files

```
claude-meter/
  monitor.py              # core logic — ingestion, DB, xbar output
  xbar-plugin/
    claude_tokens.1m.py   # thin xbar wrapper (1m = refresh every minute)
```

The database lives at `~/.claude-meter.db` (outside the repo).

## Setup

### 1. Install xbar

```bash
brew install --cask xbar
```

### 2. Symlink the plugin

```bash
ln -sf ~/repos/claude-meter/xbar-plugin/claude_tokens.1m.py \
       ~/Library/Application\ Support/xbar/plugins/claude_tokens.1m.py
```

### 3. Launch xbar

```bash
open -a xbar
```

xbar is configured to launch at login via macOS Login Items.

## Pricing table

Pricing is defined in `monitor.py` in the `PRICING` dict. Update it when Anthropic releases new models or changes rates.

## Extending

- **New models** — add an entry to `PRICING` in `monitor.py`
- **Slack/webhook alerts** — add a check in `main()` after `report()` and post if `today_cost` exceeds a threshold
- **Web dashboard** — query `~/.claude-meter.db` directly; schema is a single `requests` table with `(request_id, ts, day, model, cost)`
