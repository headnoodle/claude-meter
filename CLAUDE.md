# claude-meter — Claude Code Guidance

Single-file Python tool (`monitor.py`) with a thin xbar wrapper. No build step, no dependencies beyond the standard library.

## Key facts

- **Data source**: `~/.claude/projects/**/*.jsonl` — read-only, never written to
- **Database**: `~/.claude-meter.db` — SQLite, lives outside the repo
- **Schema**: one table `requests (request_id PK, ts, day, model, cost)`
- **Pricing**: `PRICING` dict in `monitor.py` — keys are exact model ID strings as they appear in the JSONL `message.model` field
- **xbar contract**: `monitor.py` must print a valid xbar menu to stdout; first line = menu bar title, `---` = separator, subsequent lines = dropdown items

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

### Check what's in the database

```bash
sqlite3 ~/.claude-meter.db "SELECT day, COUNT(*), ROUND(SUM(cost),2) FROM requests GROUP BY day ORDER BY day"
```

### Test the xbar output locally

```bash
python3 ~/repos/claude-meter/monitor.py
```

### Re-symlink the xbar plugin after moving the repo

```bash
ln -sf ~/repos/claude-meter/xbar-plugin/claude_tokens.1m.py \
       ~/Library/Application\ Support/xbar/plugins/claude_tokens.1m.py
```

## What NOT to do

- Do not add error handling for missing JSONL files — they come and go by design; `INSERT OR IGNORE` handles duplicates
- Do not store prompt or response text — cost and metadata only
- Do not change the DB path without also updating the `DB_PATH` constant and the README
