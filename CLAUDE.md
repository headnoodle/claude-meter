# claude-meter — Claude Code Guidance

Single-file Python menu bar app (`monitor.py`) using `rumps`. No build step beyond `pip install rumps`.

## Key facts

- **Version**: defined as `VERSION` constant in `monitor.py` — bump it and the formula `url`/`sha256` together when releasing
- **Data source**: `~/.claude/projects/**/*.jsonl` — read-only, never written to
- **Database**: `~/.claude-meter.db` — SQLite, lives outside the repo
- **Config**: `~/.claude-meter.conf` — JSON, written by the "Set Budget…" menu item
- **Schema**: `requests (request_id PK, ts, day, model, cost, thinking_cost, cache_read_tokens, cache_total_tokens, cache_savings, git_branch, cwd)`
- **Pricing**: `PRICING` dict in `monitor.py` — keys are exact model ID strings as they appear in the JSONL `message.model` field
- **Refresh**: `@rumps.timer(60)` fires `_refresh` every 60 seconds; also called once in `__init__`

## Architecture

```
ClaudeMeterApp(rumps.App)
  __init__          → calls _refresh(None) for immediate first render
  _refresh (timer)  → open_db → ingest → report → active_sessions → check_budget → _build
  _build            → clears self.menu and rebuilds from report data
  _set_budget       → rumps.Window dialog → saves to ~/.claude-meter.conf
```

## Common tasks

### Add a new model

Add an entry to `PRICING` in `monitor.py`:
```python
"claude-new-model-id": {"i": X.X, "o": X.X, "cw": X.X, "cr": X.X},
```
Rates are USD per million tokens: `i` = input, `o` = output, `cw` = cache write, `cr` = cache read.

To find the exact model ID string Claude Code uses, query the DB after a session with that model:
```bash
sqlite3 ~/.claude-meter.db "SELECT DISTINCT model FROM requests ORDER BY ts DESC LIMIT 20"
```

### Bump the version

1. Update `VERSION` in `monitor.py`
2. Tag the release: `git tag v0.x.0 && git push origin v0.x.0`
3. Create a GitHub release from the tag
4. Update `url` and `sha256` in `Formula/claude-meter.rb`
5. Push the formula update to `headnoodle/homebrew-tap`

### Test locally

```bash
pip3 install rumps
python3 ~/repos/claude-meter/monitor.py
```

### Check what's in the database

```bash
sqlite3 ~/.claude-meter.db "SELECT day, COUNT(*), ROUND(SUM(cost),2) FROM requests GROUP BY day ORDER BY day"
```

## DB schema migrations

New columns are added via `ALTER TABLE ... ADD COLUMN` in `open_db()`, with backfill logic in `ingest()`. Follow the pattern already used for `cwd`, `thinking_cost`, `cache_read_tokens`, and `git_branch`.

## What NOT to do

- Do not add error handling for missing JSONL files — they come and go by design; `INSERT OR IGNORE` handles duplicates
- Do not store prompt or response text — cost and metadata only
- Do not change the DB path without also updating `DB_PATH` and the README
- Do not bump to v1.0.0 until the tool has had broader testing
