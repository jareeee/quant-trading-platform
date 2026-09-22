#!/bin/sh
set -eu

LABEL="com.quant-platform.core"
DOMAIN="gui/$(id -u)"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

die() {
  printf 'uninstall: %s\n' "$1" >&2
  exit 1
}

stop_loaded_agent() {
  launchctl kill SIGTERM "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  attempts=0
  while [ "$attempts" -lt 50 ]; do
    state=$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null || true)
    case "$state" in
      *"pid ="*)
        sleep 0.1
        attempts=$((attempts + 1))
        ;;
      *) return 0 ;;
    esac
  done
  return 1
}

[ "$(uname -s)" = "Darwin" ] || die "macOS is required"

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  if ! stop_loaded_agent; then
    die "user agent did not stop gracefully"
  fi
  if ! launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    die "could not stop the user agent"
  fi
fi

if [ -e "$TARGET" ] && ! rm -f "$TARGET"; then
  die "could not remove the launchd plist"
fi

printf 'Uninstalled %s (user data and logs were preserved)\n' "$LABEL"
