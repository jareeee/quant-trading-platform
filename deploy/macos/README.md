# macOS launchd service

This package installs the paper-only `trading-core run` process as a per-user launch agent. It never uses `sudo`, does not install a system daemon, and does not put credentials in the plist.

## Prerequisites

From the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env                 # if .env does not already exist
.venv/bin/alembic upgrade head
TRADING_MODE=paper .venv/bin/trading-core check
```

The installer requires macOS, an executable `.venv/bin/trading-core`, a valid `.env`/Settings configuration, an existing current Alembic schema, and `TRADING_MODE=paper`. The current core supports paper mode only. The installer runs the non-mutating `trading-core check`; it does **not** run Alembic, process queued commands, execute a trading iteration, submit an order, or publish a heartbeat.

## Install or update

Run as the logged-in desktop user from any directory:

```sh
./deploy/macos/install.sh
```

The script derives the repository path from its own location, so spaces and XML-special characters in paths are supported. It renders and validates an absolute-path plist, installs it as:

```text
~/Library/LaunchAgents/com.quant-platform.core.plist
```

It then uses `launchctl bootstrap gui/$UID` and verifies the exact service with `launchctl print`. Re-running the installer safely replaces and re-bootstraps that same label.

## Status and control

```sh
launchctl print "gui/$UID/com.quant-platform.core"
launchctl kickstart "gui/$UID/com.quant-platform.core"
launchctl kickstart -k "gui/$UID/com.quant-platform.core"  # controlled restart
```

`RunAtLoad` starts the service when the user launch-agent domain loads (normally at login). After a reboot it starts only after this user logs in; it is not a root/system boot daemon. A nonzero/crash exit is restarted by launchd, with `ThrottleInterval=30` to limit restart loops. A clean exit is not restarted automatically.

**Sleep limitation:** launchd does not execute this process while the Mac is asleep. On wake, the core's recovery rules apply; launchd is not an exchange-side protection mechanism. Once trading modes can hold positions, exchange-native protection orders remain required.

## Logs

```sh
tail -f "$HOME/Library/Logs/quant-platform/core.stdout.log"
tail -f "$HOME/Library/Logs/quant-platform/core.stderr.log"
```

The single-instance lock is under `~/Library/Application Support/quant-platform/`. Both locations are user-owned.

## Uninstall

```sh
./deploy/macos/uninstall.sh
```

The uninstaller uses `launchctl bootout gui/$UID/com.quant-platform.core` only when that exact agent is loaded, then removes only its installed plist. It is idempotent. It does not remove `.env`, the database, logs, lock files, credentials, the virtual environment, or the project.
