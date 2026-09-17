#!/usr/bin/env python3
# <xbar.title>Claude Meter</xbar.title>
# <xbar.version>v0.1.0</xbar.version>
# <xbar.desc>Tracks Claude Code API spend from local transcripts</xbar.desc>
# <xbar.var>number(CLAUDE_METER_DAILY_BUDGET=50): Daily spend budget in USD. Set to 0 to disable alerts.</xbar.var>

# Delegates entirely to the claude-meter monitor script.
# Refresh interval is set by the filename: 1m = every minute.
# xbar vars are passed as environment variables and inherited by the subprocess.
import subprocess, sys, os
script = os.path.expanduser("~/repos/claude-meter/monitor.py")
result = subprocess.run([sys.executable, script], capture_output=True, text=True)
print(result.stdout, end="")
if result.returncode != 0:
    print("⚠ claude-meter error")
    print("---")
    print(result.stderr[:200])
