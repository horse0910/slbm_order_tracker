#!/usr/bin/env bash
# Install the launchd agent that starts the SLBM tracker at login and keeps it
# running (Phase 7.2). Run it yourself from the folder that stays on main:
#
#   scripts/install_launch_agent.sh            # install and start
#   scripts/install_launch_agent.sh --print    # only print the plist; touch nothing
#
# The agent runs this folder's .venv uvicorn on 127.0.0.1:8010 (the backend
# serves frontend/dist), restarts it if it exits (KeepAlive), and logs to
# ~/Library/Logs/slbm-tracker/. Remove it with scripts/uninstall_launch_agent.sh.
set -euo pipefail

LABEL="com.harsh.slbm-tracker"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/slbm-tracker"

xml() { # escape a value for a plist <string>
  local s="$1"
  s="${s//&/&amp;}"; s="${s//</&lt;}"; s="${s//>/&gt;}"
  printf '%s' "$s"
}

print_plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml "$PY")</string>
    <string>-m</string>
    <string>uvicorn</string>
    <string>slbm_tracker.api.app:app</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>8010</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$(xml "$REPO")</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$(xml "$LOG_DIR/slbm-tracker.out.log")</string>
  <key>StandardErrorPath</key>
  <string>$(xml "$LOG_DIR/slbm-tracker.err.log")</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
    <key>PATH</key>
    <string>/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
</dict>
</plist>
EOF
}

if [[ "${1:-}" == "--print" ]]; then
  print_plist
  exit 0
fi

fail() { echo "install_launch_agent: $*" >&2; exit 1; }

[[ "$(uname)" == "Darwin" ]] || fail "launchd agents are macOS-only."
branch="$(git -C "$REPO" branch --show-current 2>/dev/null || true)"
[[ "$branch" == "main" ]] || fail "$REPO is on '${branch:-?}', not main. Run this from the folder that stays on main."
[[ -x "$PY" ]] || fail "no $PY; create the .venv first."
[[ -f "$REPO/frontend/dist/index.html" ]] || fail "no frontend build; run: npm --prefix \"$REPO/frontend\" run build"
if lsof -nP -iTCP:8010 -sTCP:LISTEN >/dev/null 2>&1 && ! launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  fail "something is already listening on 127.0.0.1:8010; stop it first (Ctrl+C in its terminal)."
fi

mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"
tmp="$(mktemp)"
print_plist > "$tmp"
plutil -lint "$tmp" >/dev/null || fail "generated plist failed plutil -lint ($tmp)"
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
mv "$tmp" "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Installed $PLIST"
echo "  runs:  $PY -m uvicorn slbm_tracker.api.app:app --host 127.0.0.1 --port 8010"
echo "  in:    $REPO"
echo "  logs:  $LOG_DIR"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8010/api/health >/dev/null 2>&1; then
    echo "Up: http://127.0.0.1:8010"
    exit 0
  fi
  sleep 1
done
echo "Not answering yet; check $LOG_DIR/slbm-tracker.err.log" >&2
