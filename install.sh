#!/usr/bin/env bash
# claude-meter installer — sets up the xbar plugin symlink and optionally
# adds xbar to macOS Login Items so tracking starts at login.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
XBAR_PLUGINS="$HOME/Library/Application Support/xbar/plugins"
PLUGIN_SRC="$SCRIPT_DIR/xbar-plugin/claude_tokens.1m.py"
PLUGIN_DST="$XBAR_PLUGINS/claude_tokens.1m.py"

echo "claude-meter installer"
echo "======================"

# Check xbar is installed
if ! [ -d "$HOME/Library/Application Support/xbar" ]; then
  echo "xbar not found. Install it first:"
  echo "  brew install --cask xbar"
  exit 1
fi

# Create plugins dir if needed and symlink the plugin
mkdir -p "$XBAR_PLUGINS"
ln -sf "$PLUGIN_SRC" "$PLUGIN_DST"
echo "✓ xbar plugin symlinked"

# Make monitor.py executable
chmod +x "$SCRIPT_DIR/monitor.py"
echo "✓ monitor.py is executable"

# Add xbar to Login Items if not already present
osascript - <<'APPLESCRIPT' 2>/dev/null && echo "✓ xbar added to Login Items" || echo "  (skipped Login Items — approve manually in System Settings › General › Login Items)"
tell application "System Events"
  set xbarPath to POSIX file "/Applications/xbar.app"
  if not (exists login item "xbar") then
    make new login item at end with properties {path:xbarPath, hidden:false}
  end if
end tell
APPLESCRIPT

echo ""
echo "Done. Run 'open -a xbar' to start tracking."
