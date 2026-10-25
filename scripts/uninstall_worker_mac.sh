#!/usr/bin/env bash
# Stop and remove the Mac transcription worker's launchd agent.
# worker.env and ~/Library/Logs/call-worker.log are left in place.
set -euo pipefail

LABEL=ai.tqc4.call-worker
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null; then
  echo "Stopped $LABEL (a job in progress is handed back to the server)."
else
  echo "$LABEL wasn't running."
fi
rm -f "$PLIST"
echo "Removed $PLIST"
