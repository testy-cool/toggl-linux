# Toggl Tray — Linux

System tray Toggl Track timer for Linux Mint (Cinnamon/X11).

## Stack

- Python 3.13, pystray (appindicator backend), Pillow, PyGObject (GTK3), python-xlib
- Core in `toggl_tray.py`; terminal commands in `toggl_cli.py`, control socket in `toggl_control.py`, projects in `toggl_projects.py`, task history in `toggl_task_tree.py`, left-click popup in `toggl_tray_popup.py`, windows in `toggl_timer_window.py`, `toggl_project_window.py`, `toggl_entries_window.py`. AGENTS.md has the module map.
- Tests: `test_*.py`; `conftest.py` fakes Xlib/GTK/pystray, gives each test a private state folder and blocks network

## Running

- Autostart: `~/.config/autostart/toggl-tray.desktop` (if configured)
- Manual: `source .venv/bin/activate && python toggl_tray.py`
- Single-instance via `fcntl` lock at `~/.local/share/toggl-tray/toggl-tray.lock`. The lock releases when the process dies; never delete the file, or a second copy can start next to a running one
- The process command line varies (`install-app` writes `... toggl_tray.py run`; an older hand-written autostart file omits `run`), so find the PID with `ps -eo pid,cmd | grep "[t]oggl_tray.py"` rather than matching `run`

## State & Data

- State: `~/.local/share/toggl-tray/state.json` (tracking status, entry_id, workspace_id)
- Offline queue: `~/.local/share/toggl-tray/pending.json` (actions queued when API unreachable)
- Event ledger: `~/.local/share/toggl-tray/events.jsonl` (append-only toggle/start/stop/pending audit trail)
- Backups: `state.json.bak` and `pending.json.bak` are maintained during atomic writes
- Icons: `~/.local/share/toggl-tray/icons/` (pre-rendered active/inactive PNGs)

## API Token

- Stored in GNOME keyring via `secret-tool`. Toggl uses `service=toggl username=api_token application=toggl-tray`; any other API base (OpenTickly) uses `service=opentickly`. `set-api-base` / `TOGGL_API_BASE` choose the server
- Fallback: `TOGGL_API_TOKEN` env var
- Get token from: https://track.toggl.com/profile (bottom of page)
- Store manually: `echo -n "TOKEN" | secret-tool store --label="Toggl API Token" service toggl username api_token application toggl-tray`
- Token lookup still supports the older `application=toggl-tray`-only key.

## Known Issues & Gotchas

- **GTK theme warning** (`'border-spacing' is not a valid property name`) is cosmetic, harmless.
- **X11 BadAccess errors** on startup are from hotkey grab conflicts (another instance or app grabbed Ctrl+Shift+T). The app prints an error and plays the error sound when the hotkey cannot be grabbed.

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
- **Desktop notifications are off.** `_notify()` returns immediately, by the user's request. Failures show through sounds, the tray icon and `doctor`.
- **Health check every sync cycle.** If tracking=True but entry_id=None with no pending items, the app tries to recover from cloud. If cloud has nothing running, it stops the local timer and plays the error sound.
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
python -m pytest -q
```

`conftest.py` mocks Xlib, GTK, and pystray at import time. Covers: elapsed_str, tooltip, state persistence, atomic JSON backups, event ledger, offline queue, sync_pending (including open-start sync, cloud adoption, 404 handling, 4xx preservation, error isolation, no-expiry behavior), API retry logic, toggle_tracking (online + offline/auth paths), start_entry payload, doctor/audit CLI diagnostics, hotkey failure reporting, rate-budgeted sync cycles, health check (recovery, phantom timer detection, skip conditions).
