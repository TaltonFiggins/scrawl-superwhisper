#!/bin/bash
# Installs scrawl-superwhisper: a background watcher that gives new Scrawl captures Superwhisper's
# words. Run from a checkout, or: curl -fsSL https://raw.githubusercontent.com/TaltonFiggins/scrawl-superwhisper/main/install.sh | bash
set -euo pipefail

REPO="${SCRAWL_SUPERWHISPER_REPO:-TaltonFiggins/scrawl-superwhisper}"
BRANCH="${SCRAWL_SUPERWHISPER_BRANCH:-main}"
HOME_DIR="$HOME/Library/Application Support/scrawl-superwhisper"
CAPTURES="$HOME/Library/Application Support/scrawl/captures"
LABEL="com.scrawl-superwhisper.watch"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT="$HOME_DIR/scrawl_superwhisper.py"
# A local copy is used only when this file itself is run from a checkout. Piped through
# `curl | bash` there is no script file, and the current folder must never be trusted.
HERE=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

say() { printf '%s\n' "$*"; }

[[ "$(uname)" == "Darwin" ]] || { say "scrawl-superwhisper only runs on macOS."; exit 1; }
if ! xcode-select -p >/dev/null 2>&1; then
  say "Python needs Apple's Command Line Tools. Run: xcode-select --install"
  say "then run this installer again."
  exit 1
fi
/usr/bin/python3 -c 'import sys; assert sys.version_info >= (3, 9)' || { say "Python 3.9 or newer is required."; exit 1; }

mkdir -p "$HOME_DIR" "$CAPTURES" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
if [[ -n "$HERE" && -f "$HERE/scrawl_superwhisper.py" ]]; then
  cp "$HERE/scrawl_superwhisper.py" "$SCRIPT"
else
  curl -fsSL --proto '=https' --tlsv1.2 "https://raw.githubusercontent.com/$REPO/$BRANCH/scrawl_superwhisper.py" -o "$SCRIPT.tmp"
  /usr/bin/python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$SCRIPT.tmp" \
    || { rm -f "$SCRIPT.tmp"; say "The download didn't look right; nothing was installed."; exit 1; }
  mv "$SCRIPT.tmp" "$SCRIPT"
fi
chmod +x "$SCRIPT"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/python3</string><string>$SCRIPT</string><string>watch</string></array>
  <key>WatchPaths</key><array><string>$CAPTURES</string></array>
  <key>ThrottleInterval</key><integer>2</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/scrawl-superwhisper.err.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

say ""
say "scrawl-superwhisper $(/usr/bin/python3 "$SCRIPT" --version) is installed."
[[ -d /Applications/superwhisper.app || -d "$HOME/Applications/superwhisper.app" ]] \
  || say "  ! Superwhisper isn't installed yet: https://superwhisper.com"
[[ -d /Applications/Scrawl.app || -d "$HOME/Applications/Scrawl.app" ]] \
  || say "  ! Scrawl isn't installed yet: https://scrawl.kitlangton.com"
say "  Record with Scrawl as usual. A notification says when Superwhisper's words are in;"
say "  then paste with Option-Shift-V."
say "  Status:    /usr/bin/python3 \"$SCRIPT\" status"
say "  Uninstall: curl -fsSL https://raw.githubusercontent.com/$REPO/$BRANCH/uninstall.sh | bash"
