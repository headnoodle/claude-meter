#!/usr/bin/env python3
# xbar plugin — delegates entirely to the claude-meter monitor script.
# Refresh interval is set by the filename: 1m = every minute.
import subprocess, sys, os
script = os.path.expanduser("~/repos/claude-meter/monitor.py")
result = subprocess.run([sys.executable, script], capture_output=True, text=True)
print(result.stdout, end="")
if result.returncode != 0:
    print("⚠ claude-meter error")
    print("---")
    print(result.stderr[:200])
