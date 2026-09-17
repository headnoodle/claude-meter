#!/usr/bin/env bash
# Manual install helper — only needed if you're not using Homebrew.
# Creates a local venv with rumps and registers a LaunchAgent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
PLIST="$HOME/Library/LaunchAgents/com.headnoodle.claude-meter.plist"

echo "Creating venv and installing rumps..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet rumps

echo "Writing LaunchAgent to $PLIST..."
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.headnoodle.claude-meter</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python3</string>
    <string>$SCRIPT_DIR/monitor.py</string>
  </array>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/.claude-meter.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/.claude-meter.log</string>
</dict>
</plist>
EOF

launchctl load "$PLIST"
echo "claude-meter started. Look for 🤖 in your menu bar."
echo ""
echo "To stop:    launchctl unload $PLIST"
echo "To restart: launchctl unload $PLIST && launchctl load $PLIST"
