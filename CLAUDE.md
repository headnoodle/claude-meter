# claude-meter — Claude Code Guidance

Single-file Python tool (`monitor.py`) with a thin xbar wrapper. No build step, no dependencies beyond the standard library.

## Key facts

- **Version**: defined as `VERSION` constant in `monitor.py` — bump it and the xbar plugin `<xbar.version>` tag together
- **Data source**: `~/.claude/projects/**/*.jsonl` — read-only, never written to
- **Database**: `~/.claude-meter.db` — SQLite, lives outside the repo
- **Schema**: `requests (request_id PK, ts, day, model, cost, thinking_cost, cache_read_tokens, cache_total_tokens, cache_savings, git_branch, cwd)`
- **Pricing**: `PRICING` dict in `monitor.py` — keys are exact model ID strings as they appear in the JSONL `message.model` field
- **xbar contract**: `monitor.py` must print a valid xbar menu to stdout; first line = menu bar title, `---` = separator, `--` prefix = submenu item, `----` = nested submenu

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
2. Update `<xbar.version>` in `xbar-plugin/claude_tokens.1m.py`
3. Update `url` and `sha256` in `Formula/claude-meter.rb` after tagging the release

### Check what's in the database

```bash
sqlite3 ~/.claude-meter.db "SELECT day, COUNT(*), ROUND(SUM(cost),2) FROM requests GROUP BY day ORDER BY day"
```

### Test the xbar output locally

```bash
python3 ~/repos/claude-meter/monitor.py
python3 ~/repos/claude-meter/monitor.py --version
```

### Re-symlink the xbar plugin after moving the repo

```bash
ln -sf ~/repos/claude-meter/xbar-plugin/claude_tokens.1m.py \
       ~/Library/Application\ Support/xbar/plugins/claude_tokens.1m.py
```

## DB schema migrations

New columns are added via `ALTER TABLE ... ADD COLUMN` in `open_db()`, with backfill logic in `ingest()`. Follow the pattern already used for `cwd`, `thinking_cost`, `cache_read_tokens`, and `git_branch`.

## What NOT to do

- Do not add error handling for missing JSONL files — they come and go by design; `INSERT OR IGNORE` handles duplicates
- Do not store prompt or response text — cost and metadata only
- Do not change the DB path without also updating `DB_PATH` and the README
- Do not bump to v1.0.0 until the tool has had broader testing
