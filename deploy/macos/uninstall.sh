#!/bin/sh
set -eu

LABEL="com.quant-platform.core"
DOMAIN="gui/$(id -u)"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

die() {
  printf 'uninstall: %s\n' "$1" >&2
  exit 1
}

[ "$(uname -s)" = "Darwin" ] || die "macOS is required"

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  if ! launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    die "could not stop the user agent"
  fi
fi

if [ -e "$TARGET" ] && ! rm -f "$TARGET"; then
  die "could not remove the launchd plist"
fi

printf 'Uninstalled %s (user data and logs were preserved)\n' "$LABEL"
