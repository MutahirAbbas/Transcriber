#!/usr/bin/env bash
# Run the Mac transcription worker as a launchd agent: starts at login, restarts if it exits.
#
#   scripts/install_worker_mac.sh            write the plist and (re)start the worker
#   scripts/install_worker_mac.sh --dry-run  print the plist, change nothing
#
# Settings: worker.env in the repo root (copy worker.env.example).
# Logs: ~/Library/Logs/call-worker.log    Remove: scripts/uninstall_worker_mac.sh
set -euo pipefail

LABEL=ai.tqc4.call-worker
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/call-worker.log"
DOMAIN="gui/$(id -u)"

xml() { printf %s "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml "$PY")</string>
    <string>-m</string>
    <string>worker.remote_worker</string>
  </array>
  <key>WorkingDirectory</key><string>$(xml "$REPO")</string>
  <key>EnvironmentVariables</key>
  <dict>
    <!-- launchd's default PATH has no Homebrew: ffmpeg / ffprobe live there -->
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ExitTimeOut</key><integer>30</integer>
  <key>StandardOutPath</key><string>$(xml "$LOG")</string>
  <key>StandardErrorPath</key><string>$(xml "$LOG")</string>
</dict>
</plist>
EOF
}

if [[ "${1:-}" == "--dry-run" ]]; then
  plist
  exit 0
fi

[[ -x "$PY" ]] || { echo "No venv python at $PY: create .venv first (README → Setup)." >&2; exit 1; }
[[ -f "$REPO/worker.env" ]] || { echo "Missing $REPO/worker.env: copy worker.env.example and fill it in." >&2; exit 1; }
chmod 600 "$REPO/worker.env"
(cd "$REPO" && "$PY" -c "import httpx, mlx_whisper, worker.remote_worker") \
  || { echo "The venv is missing worker dependencies: pip install -r requirements-mac.txt" >&2; exit 1; }

case "$REPO" in
  "$HOME"/Documents/*|"$HOME"/Desktop/*|"$HOME"/Downloads/*)
    real_py="$("$PY" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
    echo "Note: the repo is in a folder macOS protects. If the log shows 'Operation not permitted'," >&2
    echo "      add $real_py to System Settings → Privacy & Security → Full Disk Access," >&2
    echo "      or move the repo outside Documents/Desktop/Downloads and re-run this script." >&2
    ;;
esac

mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
plist > "$PLIST"
plutil -lint "$PLIST" >/dev/null

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "$DOMAIN/$LABEL" || true
  for _ in $(seq 20); do launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break; sleep 0.5; done
fi
launchctl bootstrap "$DOMAIN" "$PLIST"

echo "Installed and started $LABEL."
echo "Log:  tail -f $LOG"
