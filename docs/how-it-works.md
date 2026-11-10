# Inside the tray app

How the tray app works, where it keeps data and how it stays inside Toggl's free-plan limits. Back to the [README](../README.md).

## How It Works

- **Python core** (`toggl_tray.py`) with focused modules for terminal commands, the control socket, projects, server tasks, recent activity history, the popup, and windows
- Talks to [Toggl Track API v9](https://engineering.toggl.com/docs/track/) or a compatible OpenTickly instance using basic auth
- API base defaults to `https://api.track.toggl.com/api/v9`; override it with `python3 toggl_tray.py set-api-base <url>` or temporary `TOGGL_API_BASE`
- Global hotkey uses X11 `XGrabKey` — the key is grabbed at the X server level, so no other app sees `Ctrl+Shift+T`
- If the API is unreachable, auth is missing, or Toggl rate-limits requests, start/stop actions are saved locally and retried
- Pending local actions sync every 5 minutes; idle cloud polling is limited to once per hour to preserve the free-plan request budget
- State is saved to `~/.local/share/toggl-tray/state.json`
- Pending writes are saved to `~/.local/share/toggl-tray/pending.json`
- Every toggle/start/stop/pending action is appended to `~/.local/share/toggl-tray/events.jsonl`
- Recent Toggl request attempts are tracked in `~/.local/share/toggl-tray/request_budget.json`
- `state.json` and `pending.json` use atomic writes and keep `.bak` backups for recovery; unreadable JSON or UTF-8 falls back to the backup, and the next write repairs the primary file

## Dependencies

All installed via pip — no compiled extensions needed:

| Package | What it does |
|---|---|
| [pystray](https://pypi.org/project/pystray/) | System tray icon (uses appindicator on Linux) |
| [Pillow](https://pypi.org/project/Pillow/) | Icon image loading and processing |
| [PyGObject](https://pypi.org/project/PyGObject/) | GTK 3 dialogs (description input, entry viewer) |
| [requests](https://pypi.org/project/requests/) | HTTP calls to Toggl API |
| [python-xlib](https://pypi.org/project/python-xlib/) | X11 global hotkey grab |

## Toggl Free Plan Limits

Toggl documents the free-plan quota as **30 API requests/hour** for user-specific endpoints and **30 requests/hour/user/organization** for organization-scoped endpoints. The app preserves that budget by:

- Prioritizing user actions and pending local start/stop sync over background cloud polling
- Tracking local request attempts and reserving the last few requests for explicit user actions
- Retrying pending local entries every 5 minutes when there is something to sync
- Polling Toggl's current timer at most once per hour while idle
- Keeping billable pending start/stop data locally after auth errors, 4xx errors, 5xx errors, network failures, and rate limits

Pending billable entries are not expired or garbage-collected automatically.

## Debug log and offline recovery

CI also runs `scripts/gtk_smoke.py` with real GTK under Xvfb, independently of
the mocked-desktop unit suite. It exercises task switching, the timer editor,
task-form callbacks, the pinned running timer, font sizes, and GTK error logging.
All state is temporary and account/network requests are blocked. Run it locally with:

```bash
xvfb-run -a .venv/bin/python scripts/gtk_smoke.py
```

The running tray automatically keeps a timestamped debug log at
`~/.local/share/toggl-tray/debug.log`. It records startup, timer events,
command outcomes, API method/path/status/timing, stderr diagnostics, GTK warnings,
and uncaught Python exceptions with tracebacks, including worker threads.
It survives restarts and rotates at 2 MiB, retaining three older files
(`debug.log.1` through `debug.log.3`). Files are owner-only (0600) in the private
state directory. API tokens are redacted; headers, request bodies, and API query
parameters are excluded from request diagnostics.

To watch diagnostics live:

```bash
tail -F ~/.local/share/toggl-tray/debug.log
```

`events.jsonl` remains the separate timer/offline event ledger. `toggl doctor`
shows both paths. The debug log can contain project/entry IDs and error details;
review it before sharing. Native fatal signals such as a segmentation fault may
still require a system crash report.

The tray opens from saved local state before keyring and cloud refreshes run on
a background worker. Startup replies are discarded if the timer or account changes
while a request is in flight. If GTK stops handling its heartbeat for 30 seconds,
the debug log records Python thread stacks once per stall and notes recovery.
The watchdog does not restart the app or change timers, and stack dumps omit local variables.

Stops queued from restored cloud timers are reconciled against that specific entry
before replay. An earlier cloud stop is preserved, while explicit offline edits
remain applicable. This uses the [API v9 entry-by-ID endpoint](https://engineering.toggl.com/docs/track/api/time_entries/).
If a compatible server cannot provide that entry, the item is marked **Needs review**
in Today's Entries. It stays editable without blocking cloud updates or spending
requests on every background retry. **Sync Now** retries it; editing also enables
a retry. **Discard Local Changes** removes only that local correction and keeps
the cloud entry. Stops from older versions without a verification snapshot require
full review or local discard before replay. Their editor explains that **Save All Fields**
applies the displayed interval, including corrections made before upgrading.
Activity started during this launch's keyring lookup receives the resolved account
identity once it is known; older unscoped queue items are left untouched.

