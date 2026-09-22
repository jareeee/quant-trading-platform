#!/bin/sh
set -eu

LABEL="com.quant-platform.core"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
TEMPLATE="$SCRIPT_DIR/$LABEL.plist"
RENDERER="$SCRIPT_DIR/render_plist.py"
EXECUTABLE="$ROOT/.venv/bin/trading-core"
PYTHON="$ROOT/.venv/bin/python"
DOMAIN="gui/$(id -u)"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET="$TARGET_DIR/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/quant-platform"
RUNTIME_DIR="$HOME/Library/Application Support/quant-platform"
LOCK_PATH="$RUNTIME_DIR/core.lock"

die() {
  printf 'install: %s\n' "$1" >&2
  exit 1
}

[ "$(uname -s)" = "Darwin" ] || die "macOS is required"
[ -f "$TEMPLATE" ] || die "launchd template is missing"
[ -f "$RENDERER" ] || die "plist renderer is missing"
[ -x "$EXECUTABLE" ] || die "install the project virtualenv; trading-core is not executable"
[ -x "$PYTHON" ] || die "install the project virtualenv; Python is not executable"

# This safety gate must run before directories, plists, or launchd state are changed.
# Run from the repository root so Settings loads that checkout's .env.
if ! (cd "$ROOT" && "$EXECUTABLE" check) >/dev/null 2>&1; then
  die "trading-core check failed; verify paper mode and run 'alembic upgrade head'"
fi

mkdir -p "$TARGET_DIR" "$LOG_DIR" "$RUNTIME_DIR"
chmod 700 "$LOG_DIR" "$RUNTIME_DIR"

temporary=$(mktemp "${TMPDIR:-/tmp}/$LABEL.XXXXXX") || die "could not create a temporary plist"
backup=""
staged="$TARGET.new.$$"
cleanup() {
  rm -f "$temporary" "$staged"
  if [ -n "$backup" ]; then
    rm -f "$backup"
  fi
}
trap cleanup EXIT HUP INT TERM

if ! "$PYTHON" "$RENDERER" "$TEMPLATE" "$temporary" \
  --project-root "$ROOT" \
  --home "$HOME" \
  --executable "$EXECUTABLE" \
  --log-dir "$LOG_DIR" \
  --lock-path "$LOCK_PATH"; then
  die "could not render the launchd plist"
fi
if ! plutil -lint "$temporary" >/dev/null; then
  die "rendered launchd plist is invalid"
fi
chmod 600 "$temporary"
if ! cp "$temporary" "$staged" || ! chmod 600 "$staged"; then
  die "could not stage the launchd plist"
fi

was_loaded=0
if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  was_loaded=1
fi
if [ -f "$TARGET" ]; then
  backup=$(mktemp "${TMPDIR:-/tmp}/$LABEL.backup.XXXXXX") || die "could not back up existing plist"
  cp -p "$TARGET" "$backup"
fi

if [ "$was_loaded" -eq 1 ]; then
  if ! launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    die "could not stop the existing user agent"
  fi
fi

if ! mv -f "$staged" "$TARGET"; then
  if [ "$was_loaded" -eq 1 ]; then
    launchctl bootstrap "$DOMAIN" "$TARGET" >/dev/null 2>&1 || true
  fi
  die "could not install the launchd plist"
fi

rollback() {
  launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  if [ -n "$backup" ]; then
    cp -p "$backup" "$TARGET"
    if [ "$was_loaded" -eq 1 ]; then
      launchctl bootstrap "$DOMAIN" "$TARGET" >/dev/null 2>&1 || true
    fi
  else
    rm -f "$TARGET"
  fi
}

if ! launchctl bootstrap "$DOMAIN" "$TARGET" >/dev/null 2>&1; then
  rollback
  die "launchctl bootstrap failed; inspect the plist and system log"
fi
if ! launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  rollback
  die "launchd did not report the user agent after bootstrap"
fi

printf 'Installed %s\n' "$TARGET"
