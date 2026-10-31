# Toggl Track Tray Timer for Linux

A lightweight system tray app that lets you start and stop [Toggl Track](https://toggl.com/track/) time entries with a single keyboard shortcut. Built for Linux desktops (GNOME, Cinnamon, MATE, XFCE) running X11.

**Press `Ctrl+Shift+T` anywhere to toggle time tracking on or off.** That's it.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Linux%20X11-orange)

## Why This Exists

Toggl's official desktop app doesn't work on Linux anymore. This replaces it with a small Python tray app built around the one thing you actually need: **toggle tracking with a hotkey**.

The tray icon shows the Toggl logo in the **running project's colour** while tracking and **grey when stopped**, so you always know at a glance.

## Features

- **Global hotkey** (`Ctrl+Shift+T`) — works everywhere, overrides all apps (X11 key grab)
- **Tray icon** — the running project's colour while tracking, grey when stopped
- **Optional panel text** — independently show or hide elapsed time and the task description on Cinnamon
- **Tooltip** — hover to see elapsed time and description
- **Offline mode** — works without internet, syncs to Toggl when back online
- **Single instance** — launching twice won't create duplicate icons
- **Sound feedback** — subtle system sounds on toggle (no popup notifications)
- **View today's entries** — right-click menu shows your timesheet
- **Doctor and audit diagnostics** — tray menu and terminal checks for missing/pending time
- **Edit & delete** — modify entries directly from the tray menu
- **Persists across restarts** — remembers state and running timers
- **Rate limit handling** — respects Toggl's API limits (30 req/hr on free plan)

## Requirements

- **Python 3.11+**
- **Linux with X11** (Wayland is not supported — uses `XGrabKey` for the global hotkey)
- **GTK 3** system libraries (usually pre-installed on most Linux desktops)
- A free [Toggl Track](https://toggl.com/track/) account

### System packages

On Debian/Ubuntu/Linux Mint:

```bash
sudo apt install libgirepository-2.0-dev gir1.2-ayatanaappindicator3-0.1 gir1.2-xapp-1.0 libsecret-tools gir1.2-webkit2-4.1
```

On Fedora:

```bash
sudo dnf install gobject-introspection-devel libappindicator-gtk3 libsecret
```

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/testy-cool/toggl-linux.git
cd toggl-linux
```

### 2. Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Or with [uv](https://docs.astral.sh/uv/) (faster):

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 3. Add your Toggl API token

Get your API token from your [Toggl Profile page](https://track.toggl.com/profile) (scroll to the bottom).

**Option A** — Store in GNOME Keyring (recommended, persistent):

```bash
secret-tool store --label='Toggl API Token' service toggl username api_token application toggl-tray
# Paste your token, press Enter, then Ctrl+D
```

**Option B** — Environment variable (per-session):

```bash
export TOGGL_API_TOKEN=your_token_here
```

### OpenTickly / self-hosted Toggl-compatible API

For OpenTickly, register in the web UI first, then copy your token from avatar > Profile > API Token.

Point the tray app at your instance:

```bash
python3 toggl_tray.py set-api-base https://opentickly.example.com/api/v9
```

This clears saved provider-specific workspace/project/entry IDs. Local pending time ranges stay in the queue and can sync after the app learns the OpenTickly workspace.

Start the tray, choose **Set API token…**, and paste the OpenTickly token. Custom-server
credentials are stored separately for each server origin. The old Toggl token stays
separate and will not be used against OpenTickly. Existing installations with the former
shared `service=opentickly` key migrate it to the currently configured server once.

Check that the app is using OpenTickly:

```bash
python3 toggl_tray.py doctor --cloud
```

### 4. Run it

```bash
source .venv/bin/activate
python3 toggl_tray.py
```

## Usage

| Action | How |
|---|---|
| **Start/stop tracking** | `Ctrl+Shift+T` (anywhere on your desktop) |
| **Set description** | Right-click tray icon > "Set description..." |
| **View today's entries** | Right-click tray icon > "Today's entries" |
| **Run diagnostics** | Right-click tray icon > "Doctor" or "Audit today" |
| **Edit/delete entries** | From the "Today's entries" window |
| **Check elapsed time** | Hover over the tray icon |
| **Configure panel text** | Toggle "Show timer in panel" and "Show description in panel" independently (Cinnamon) |

### Terminal recovery commands

Install the `toggl` command once (this also refreshes the desktop launcher):

```bash
.venv/bin/python toggl_tray.py install-app
```

The command uses this checkout's virtual environment automatically and works from any directory. These commands do not need the tray window to be visible:

```bash
toggl status          # show current state and local pending entries
toggl local           # list today's local/offline entries
toggl entries         # list today's Toggl + local entries
toggl doctor          # inspect local state, token, pending queue, and ledger
toggl doctor --cloud  # also check Toggl's current timer (uses one API request)
toggl audit 2026-05-01 2026-05-31
                     # summarize totals, blanks, local pending entries, and large gaps
toggl start "Task"    # start tracking from a terminal
toggl stop            # stop tracking from a terminal
toggl set-start 09:00 # correct the current timer start time
toggl set-api-base https://opentickly.example.com/api/v9
                     # use OpenTickly or another Toggl-compatible API
toggl sync            # push pending local entries now
```

## Autostart on Login

To make the app searchable from your desktop app launcher:

```bash
.venv/bin/python toggl_tray.py install-app
```

This also installs `~/.local/bin/toggl` for terminal use.

To also start it automatically on login:

```bash
python3 toggl_tray.py install-app --autostart
```

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
- `state.json` and `pending.json` use atomic writes and keep `.bak` backups for recovery

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

## Troubleshooting

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

**No tray icon appears**
- Make sure your desktop has a system tray / notification area. On GNOME, you may need the [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/).
- Install the appindicator library: `sudo apt install gir1.2-ayatanaappindicator3-0.1`

**Ctrl+Shift+T doesn't work**
- This app only works on X11, not Wayland. Check with `echo $XDG_SESSION_TYPE`.
- Another app may have already grabbed that key combo.
- If the tray app cannot grab the hotkey, it prints an error and plays the error sound. You can still use the tray menu or terminal commands.

**"Another instance is already running"**
- The message includes the PID of the running copy. The lock is released automatically when that process exits, so quit or stop that process instead of deleting the lock file; deleting it while the app runs allows a second copy to start.

**Tray entries are empty but tracking looks active**
- Run `python3 toggl_tray.py status`. Local/offline entries are shown from `~/.local/share/toggl-tray/pending.json`.
- Run `python3 toggl_tray.py local` to list only local pending entries for today.

**Nothing is being tracked**
- Run `python3 toggl_tray.py status` to check whether a local timer or pending queue exists.
- Run `python3 toggl_tray.py doctor` for state file, pending file, token, ledger, and rate-limit health.
- Run `python3 toggl_tray.py entries` to check today's Toggl entries.
- If both are empty, the hotkey likely did not reach the app. Use the tray menu or `python3 toggl_tray.py start "Task"` until the hotkey conflict is resolved.

**Need to review missing time**
- Run `python3 toggl_tray.py audit 2026-05-01 2026-05-31`.
- The audit shows per-day totals, blank descriptions, local pending entries, and large gaps.
- Use `--local-only` to inspect only unsynced local entries without spending API requests.

**No sound on toggle**
- Sounds use `paplay` (PulseAudio). Make sure it's installed: `sudo apt install pulseaudio-utils`

## License

MIT

### Project colors

Right-click the tray icon and open **Project** to select a saved project or **No project** directly. The current choice is checked. Selection updates the current interval, or the remembered project while stopped, without starting a new interval. **Refresh projects** reloads the choices. Pending offline timers remain editable; if a cloud edit cannot be saved, the checked choice stays as it was.

The same submenu includes **New Project…**, **Edit Selected Project…**, and **Manage Projects…**. New/Edit opens a name and color form; Manage opens your account's project page for archives, deletion, and advanced settings. The timer window's **Projects** button and Vicinae's **Projects** section offer the same actions. Creating a project adds it to the choices without assigning it to the timer. Saving a name or color immediately refreshes shared project lists and the selected project's tray color while keeping existing time entries attached. Creation/editing requires a connection; failed or uncertain writes keep the form text and ask you to refresh before retrying.

While tracking a project, the tray icon uses that project’s saved six-digit hex color. Stopped timers stay grey; timers without a project or a valid color keep the default active icon. Project metadata is cached locally in `~/.local/share/toggl-tray/projects.json`, scoped to the account, and refreshed at most hourly while API quota is available. Cached colors work offline, and rendering never makes network requests. Changing a project or its color updates the panel icon automatically after the next cloud/project refresh.

### Vicinae extension

The [`vicinae/`](vicinae/) extension manages the running timer, project selection, descriptions, and today's cloud/pending entries. See its README for installation, upgrades, and removal. It uses the tray's private `control.sock` (owner access only), with the same state and offline queue. The tray must be running; the extension does not require a second API token.

### Edit the current timer quickly

Choose **Open Timer** in the tray to edit the project, real task, description, and start time together. **Save Changes** (Enter or Ctrl+Enter) keeps the same interval; **Start New Entry** (Ctrl+Shift+Enter) closes it and starts another. A task is reusable OpenTickly metadata, while the description is optional detail for one time entry. Start time includes the date; HH:MM means today, while a full date and time can correct an older interval. Stop and sync preserve unsaved drafts. Start/Stop and today's entries stay visible; **More** holds sync, project refresh, account connection, and panel visibility in the same window. Vicinae offers the same Start New Entry action. See [`docs/timer-scenarios.md`](docs/timer-scenarios.md) for the scenario tree.

Use **Resume Recent Activity** in the timer window, or **Start Again** on a completed Today entry in Vicinae, to return to earlier work. The previous description, project, task, and workspace are copied into a new interval when the saved task is still active; a running timer is stopped first. Today's cached and pending entries are supported, including offline. Resume never changes the earlier interval.

### Work window

Left-click opens an offline HTML/CSS work window built with React, ReUI and shadcn.
The timer, queue, API credentials and sync remain in the Python tray process. A
separate pywebview process talks only to its private control socket. Closing the
window hides it, so reopening preserves search, scroll and any unfinished draft.
The current timer stays visible while browsing; editing happens in the same window.
Work and Tasks show an expanded project tree, with locally bundled Inter,
14px project and task names, and supporting text at 12px or larger.
Only projects with work and rows with today's intervals show an expansion arrow.
Empty rows keep their Start and Edit actions; named empty projects keep New Task.
Click an expandable name or press Enter to view today's intervals; this opens no form and
never starts or switches a timer. Each row shows its **Today** total, including
repeat sessions and the running interval. A saved task's pencil renames it; an
interval's pencil edits that session's description, project and times. Play starts
work explicitly. Recent activities are labeled **Recent** and their menu offers
**New timer with these details**, **Save as task**, and **Hide suggestion**.
The current row is labeled **Running**. Collapse a project
with its arrow; search reveals matches without losing your browsing choices.
The plus beside a project creates a task in that project. A running timer uses a
compact row; when stopped, the tree begins directly below search and navigation.
**Open timer** and **Edit description** open this window's editor directly.
**Today's entries** opens its Today tab. Stop keeps an unfinished description
attached to the completed interval so it can still be saved without restarting.
Drafts keep their edits when offline sync assigns a cloud ID. Escape hides the
window and preserves the draft, just like its close button.
The Today tab groups sessions by task or activity with durations and subtotals.
Totals count only the selected workspace and the part of each interval within
today's local midnight boundaries, including daylight-saving days and overnight
pending entries. The current timer is added once. Unknown or incomplete totals
show a dash or an approximation rather than a false zero.

Daily entries load in the background once per account/workspace and local date;
the timer controls remain available during the read. The footer shows when data
was fetched. **Refresh** updates it, subject to the existing one-minute throttle
and request budget. The compatible API's [modified-since entry filter](https://engineering.toggl.com/docs/api/time_entries/)
also includes intervals that began earlier but were completed today. A failed or
capped read is marked incomplete; local pending work remains editable.
Entry edits include start and stop times. Older pending intervals without a cloud
baseline require explicit approval of every displayed field before saving.

The prebuilt interface in `toggl_web_assets/` ships with the Python source. Node
is needed only to develop or rebuild it:

```bash
npm ci --prefix ui
npm test --prefix ui
npm run build --prefix ui
```

The web engine is WebKit2GTK 4.1 on Linux. If the engine or bundle cannot start,
the tray keeps working and opens its GTK compatibility interface; details go to
the private `~/.local/share/toggl-tray/work-ui.log` and normal debug log. No API
token is passed to the UI process. The asset server serves only the compiled UI
directory; the window uses no CDN, remote scripts or remote fonts.

The project tree, status badges, editor frames and alerts use ReUI. Controls,
menus, tabs, scrolling and Today's entry list use shadcn components. Application
code supplies layout, grouping and the Python bridge.

CI builds and checks the shipped bundle, then exercises real React/WebKit against
private offline state under Xvfb using `scripts/webview_smoke.py`. It covers starting,
editing the same interval, stopping, and computed font sizes and overflow.

### GTK compatibility panel

Left-click the tray icon to open a small window under it. Projects contain real
OpenTickly tasks; choosing one starts it with a blank description. Historical
time-entry descriptions appear under **Recent work**, visible immediately. The five
most recent descriptions per project appear first; **Show more** reveals the rest.
Every work row has **Edit**. For running work, this edits its description, project,
and task inside the panel; **Save changes** keeps the same interval. For earlier
work, **Edit** lets you change the description/project before starting a new interval,
save the description as a reusable task, or hide the suggestion. Earlier time entries
stay unchanged. Saved tasks can be renamed in place; OpenTickly's task API keeps
them in their original project.

Clicking a project opens a new-timer form. It never silently reassigns existing
time. **New task** creates a saved task in place; **Manage Tasks** opens the advanced
rename, archive, and restore window. **New timer** starts with no project selected.
Type to search everything; Enter starts the first matching task or activity.
Starting and stopping keep the panel open. Search, expanded groups, and scroll
position survive closing and reopening it. During editing, focus loss keeps the
draft visible, Escape cancels the form, and failed saves preserve the inputs.

The running timer stays pinned above the list with its description or task name,
project, elapsed time, and **Stop** and **Edit** controls. Search, capped history,
hidden activities, and archived tasks cannot hide it. It updates every second while
the popup is open; matching task/activity rows also say **Running**.

Right-click the tray icon for the full menu. **Projects, tasks, and activities…** at the top
opens the same window (use it on desktops where left click opens the menu).
**Project** lists the saved projects and **No project**: choosing one changes only
the current interval's project, without starting a new one. It also holds
**New Project…**, **Edit Selected Project…**, **Manage Projects…** and
**Refresh projects and tasks**.

While tracking, the tray icon takes the project's colour. The active marker follows
the running real task. Projects, tasks, and activities are scoped to the account,
API server, and workspace so labels cannot leak across account switches.

Real tasks are cached in `~/.local/share/toggl-tray/tasks.json`. Recent activities
use the last 30 days, limited to 1,000 fetched entries plus local pending intervals,
and persist in `task-history.json`. Both work offline and refresh at most hourly.
Servers without the task endpoint keep the recent-activity fallback available.
**Refresh projects and tasks** requests a manual refresh.
