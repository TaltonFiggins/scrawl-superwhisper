#!/bin/bash
# Removes scrawl-superwhisper. Captures keep whatever words they have; `undo` before uninstalling
# puts Apple's transcript back on a capture.
set -uo pipefail
LABEL="com.scrawl-superwhisper.watch"
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -rf "$HOME/Library/Application Support/scrawl-superwhisper"
echo "scrawl-superwhisper removed. Its log is at ~/Library/Logs/scrawl-superwhisper.log."
