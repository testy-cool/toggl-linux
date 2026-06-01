# Toggl Tray — Linux

System tray Toggl Track timer for Linux Mint (Cinnamon/X11).

## Stack

- Python 3.13, pystray (appindicator backend), Pillow, PyGObject (GTK3), python-xlib
- Single file: `toggl_tray.py` (~800 lines)
- Tests: `test_toggl_tray.py` (pytest, mocks Xlib/GTK/pystray at import time)

## Running

- Autostart: `~/.config/autostart/toggl-tray.desktop` (if configured)
- Manual: `source .venv/bin/activate && python toggl_tray.py`
- Single-instance via `fcntl` file lock at `~/.local/share/toggl-tray/toggl-tray.lock`
- If process dies, the lock file becomes stale — delete it before relaunching

## State & Data

- State: `~/.local/share/toggl-tray/state.json` (tracking status, entry_id, workspace_id)
- Offline queue: `~/.local/share/toggl-tray/pending.json` (actions queued when API unreachable)
- Event ledger: `~/.local/share/toggl-tray/events.jsonl` (append-only toggle/start/stop/pending audit trail)
- Backups: `state.json.bak` and `pending.json.bak` are maintained during atomic writes
- Icons: `~/.local/share/toggl-tray/icons/` (pre-rendered active/inactive PNGs)

## API Token

- Stored in GNOME keyring via `secret-tool` (attributes: `service=toggl username=api_token application=toggl-tray`)
- Fallback: `TOGGL_API_TOKEN` env var
- Get token from: https://track.toggl.com/profile (bottom of page)
- Store manually: `echo -n "TOKEN" | secret-tool store --label="Toggl API Token" service toggl username api_token application toggl-tray`
- Token lookup still supports the older `application=toggl-tray`-only key.

## Known Issues & Gotchas

- **Stale lock file after kill -9.** fcntl locks don't survive process death. Must `rm ~/.local/share/toggl-tray/toggl-tray.lock` before relaunching.
- **GTK theme warning** (`'border-spacing' is not a valid property name`) is cosmetic, harmless.
- **X11 BadAccess errors** on startup are from hotkey grab conflicts (another instance or app grabbed Ctrl+Shift+T). The app now notifies the user when the hotkey cannot be grabbed.

## Offline Sync Resilience

- **Open starts sync to Toggl.** Offline starts are pushed to the API on next sync cycle. If an entry is already running on Toggl, the app adopts it instead of creating a duplicate.
- **404/410 on delete = success.** If a pending delete targets an already-deleted entry, it's cleared from the queue.
- **Billable queue items are not auto-dropped.** Start/stop/create/update items stay pending after auth errors, 4xx errors, 5xx errors, network failures, and rate limits.
- **One bad item doesn't block others.** Sync continues past failed items (was: `break` on any error).
- **No pending expiry.** Pending billable items are never garbage-collected by age.
- **Invalid items are kept for manual recovery.** Malformed queue items are skipped but preserved.
- **Pending sync runs every 5 min.** Background work prioritizes queued local actions.
- **Idle cloud polling runs hourly.** This preserves Toggl's 30 req/hour free-plan budget for user actions.
- **Pending deletes don't block cloud sync.** Only pending start/stop operations (which conflict with tracking state) defer cloud sync.
- **Desktop notifications on offline.** User sees "Offline — start/stop queued locally" when API calls fail.
- **Desktop notifications on auth/rate-limit.** Missing token, auth failure, and active rate-limit states are visible and keep local data.
- **No-description nudge.** Starting with empty description shows a notification to set one.
- **Health check every sync cycle.** If tracking=True but entry_id=None with no pending items, the app tries to recover from cloud. If cloud has nothing running, it stops the local timer and notifies the user.
- **Sync failure notifications.** After 3 consecutive sync failures, the user gets a notification to check their connection.
- **Thread-safe state.** A `state_lock` protects all state mutations from race conditions between the sync thread and user-triggered toggles.
- **Atomic local writes.** `state.json` and `pending.json` write via temp-file + rename and fall back to `.bak` on corrupt primary JSON.
- **Append-only event ledger.** Toggle/CLI start/stop requests and pending queue writes append JSONL records before or alongside cloud writes.

## Diagnostics

- `python toggl_tray.py doctor` checks local state, token presence, pending queue, ledger count, and rate-limit status without spending Toggl requests.
- `python toggl_tray.py doctor --cloud` also checks Toggl's current timer and spends one API request.
- `python toggl_tray.py audit YYYY-MM-DD YYYY-MM-DD` summarizes daily totals, blank descriptions, local pending entries, and gaps larger than 120 minutes.
- `python toggl_tray.py audit YYYY-MM-DD YYYY-MM-DD --local-only` avoids Toggl API calls and audits only pending local entries.

## Tests

```bash
source .venv/bin/activate
python -m pytest test_toggl_tray.py -v
```

Tests mock Xlib, GTK, and pystray at import time. Covers: elapsed_str, tooltip, state persistence, atomic JSON backups, event ledger, offline queue, sync_pending (including open-start sync, cloud adoption, 404 handling, 4xx preservation, error isolation, no-expiry behavior), API retry logic, toggle_tracking (online + offline/auth paths), start_entry payload, doctor/audit CLI diagnostics, hotkey failure notification, rate-budgeted sync cycles, health check (recovery, phantom timer detection, skip conditions).
