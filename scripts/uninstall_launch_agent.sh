#!/usr/bin/env bash
# Stop and remove the SLBM tracker launchd agent (Phase 7.2). Leaves the
# database, backups and logs where they are.
set -euo pipefail

LABEL="com.harsh.slbm-tracker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

[[ "$(uname)" == "Darwin" ]] || { echo "uninstall_launch_agent: macOS-only." >&2; exit 1; }
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && echo "Stopped $LABEL" || echo "$LABEL was not running"
if [[ -f "$PLIST" ]]; then
  rm "$PLIST"
  echo "Removed $PLIST"
else
  echo "No $PLIST"
fi
echo "Logs kept in $HOME/Library/Logs/slbm-tracker; database and backups untouched."
