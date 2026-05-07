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
- Icons: `~/.local/share/toggl-tray/icons/` (pre-rendered active/inactive PNGs)

## API Token

- Stored in GNOME keyring via `secret-tool` (key: `application=toggl-tray`)
- Fallback: `TOGGL_API_TOKEN` env var
- Get token from: https://track.toggl.com/profile (bottom of page)
- Store manually: `echo -n "TOKEN" | secret-tool store --label="Toggl API Token" application toggl-tray`

## Known Issues & Gotchas

- **Token loss breaks everything silently.** If the keyring loses the token, all API calls fail and entries pile up in pending.json. No user-visible error — it just silently queues offline.
- **Stale lock file after kill -9.** fcntl locks don't survive process death. Must `rm ~/.local/share/toggl-tray/toggl-tray.lock` before relaunching.
- **GTK theme warning** (`'border-spacing' is not a valid property name`) is cosmetic, harmless.
- **X11 BadAccess errors** on startup are from hotkey grab conflicts (another instance or app grabbed Ctrl+Shift+T). Harmless if the previous instance is dead.

## Offline Sync Resilience

- **404/410 on delete = success.** If a pending delete targets an already-deleted entry, it's cleared from the queue.
- **4xx client errors drop the item.** Bad payloads that will never succeed get removed instead of poisoning the queue.
- **One bad item doesn't block others.** Sync continues past failed items (was: `break` on any error).
- **7-day expiry.** Pending items older than 7 days are garbage-collected on sync.
- **Invalid items auto-cleaned.** Malformed queue items are removed on sync.
- **Cloud sync runs every 5 min.** Not just after pending drains — catches entries started/stopped from web/mobile.
- **Pending deletes don't block cloud sync.** Only pending start/stop operations (which conflict with tracking state) defer cloud sync.
- **Desktop notifications on offline.** User sees "Offline — start/stop queued locally" when API calls fail.
- **No-description nudge.** Starting with empty description shows a notification to set one.

## Tests

```bash
source .venv/bin/activate
python -m pytest test_toggl_tray.py -v
```

Tests mock Xlib, GTK, and pystray at import time. Covers: elapsed_str, tooltip, state persistence, offline queue, sync_pending (including 404 handling, 4xx drop, error isolation, expiry), API retry logic, toggle_tracking (online + offline paths), start_entry payload.
