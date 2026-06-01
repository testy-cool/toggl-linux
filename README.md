# Toggl Track Tray Timer for Linux

A lightweight system tray app that lets you start and stop [Toggl Track](https://toggl.com/track/) time entries with a single keyboard shortcut. Built for Linux desktops (GNOME, Cinnamon, MATE, XFCE) running X11.

**Press `Ctrl+Shift+T` anywhere to toggle time tracking on or off.** That's it.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Linux%20X11-orange)

## Why This Exists

Toggl's official desktop app doesn't work on Linux anymore. This replaces it with a single Python file that does the one thing you actually need: **toggle tracking with a hotkey**.

The tray icon shows the Toggl logo in **pink when tracking** and **grey when stopped**, so you always know at a glance.

## Features

- **Global hotkey** (`Ctrl+Shift+T`) — works everywhere, overrides all apps (X11 key grab)
- **Tray icon** — pink = tracking, grey = stopped
- **Tooltip** — hover to see elapsed time and description
- **Offline mode** — works without internet, syncs to Toggl when back online
- **Single instance** — launching twice won't create duplicate icons
- **Sound feedback** — subtle system sounds on toggle (no popup notifications)
- **View today's entries** — right-click menu shows your timesheet
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
sudo apt install libgirepository-2.0-dev gir1.2-ayatanaappindicator3-0.1 libsecret-tools
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
| **Edit/delete entries** | From the "Today's entries" window |
| **Check elapsed time** | Hover over the tray icon |

### Terminal recovery commands

These do not need the tray window to be visible:

```bash
python3 toggl_tray.py status          # show current state and local pending entries
python3 toggl_tray.py local           # list today's local/offline entries
python3 toggl_tray.py entries         # list today's Toggl + local entries
python3 toggl_tray.py doctor          # inspect local state, token, pending queue, and ledger
python3 toggl_tray.py doctor --cloud  # also check Toggl's current timer (uses one API request)
python3 toggl_tray.py audit 2026-05-01 2026-05-31
                                     # summarize totals, blanks, local pending entries, and large gaps
python3 toggl_tray.py start "Task"    # start tracking from a terminal
python3 toggl_tray.py stop            # stop tracking from a terminal
python3 toggl_tray.py set-start 09:00 # correct the current timer start time
python3 toggl_tray.py sync            # push pending local entries to Toggl now
python3 toggl_tray.py install-app     # add Toggl Tray to your app launcher
```

## Autostart on Login

To make the app searchable from your desktop app launcher:

```bash
python3 toggl_tray.py install-app
```

To also start it automatically on login:

```bash
python3 toggl_tray.py install-app --autostart
```

## How It Works

- **One file** (`toggl_tray.py`) — no complex project structure
- Talks to [Toggl Track API v9](https://engineering.toggl.com/docs/track/) using basic auth
- Global hotkey uses X11 `XGrabKey` — the key is grabbed at the X server level, so no other app sees `Ctrl+Shift+T`
- If the API is unreachable, auth is missing, or Toggl rate-limits requests, start/stop actions are saved locally and retried
- Pending local actions sync every 5 minutes; idle cloud polling is limited to once per hour to preserve the free-plan request budget
- State is saved to `~/.local/share/toggl-tray/state.json`
- Pending writes are saved to `~/.local/share/toggl-tray/pending.json`
- Every toggle/start/stop/pending action is appended to `~/.local/share/toggl-tray/events.jsonl`
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
- Retrying pending local entries every 5 minutes when there is something to sync
- Polling Toggl's current timer at most once per hour while idle
- Keeping billable pending start/stop data locally after auth errors, 4xx errors, 5xx errors, network failures, and rate limits

Pending billable entries are not expired or garbage-collected automatically.

## Troubleshooting

**No tray icon appears**
- Make sure your desktop has a system tray / notification area. On GNOME, you may need the [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/).
- Install the appindicator library: `sudo apt install gir1.2-ayatanaappindicator3-0.1`

**Ctrl+Shift+T doesn't work**
- This app only works on X11, not Wayland. Check with `echo $XDG_SESSION_TYPE`.
- Another app may have already grabbed that key combo.
- If the tray app cannot grab the hotkey, it shows a desktop notification. You can still use the tray menu or terminal commands.

**"Another instance is already running"**
- The message includes the PID from the lock file. If that process is gone, remove the lock file: `rm ~/.local/share/toggl-tray/toggl-tray.lock`

**Tray entries are empty but tracking looks active**
- Run `python3 toggl_tray.py status`. Local/offline entries are shown from `~/.local/share/toggl-tray/pending.json`.
- Run `python3 toggl_tray.py local` to list only local pending entries for today.

**Nothing is being tracked**
- Run `python3 toggl_tray.py status` to check whether a local timer or pending queue exists.
- Run `python3 toggl_tray.py doctor` for state file, pending file, token, ledger, and rate-limit health.
- Run `python3 toggl_tray.py entries` to check today's Toggl entries.
- If both are empty, the hotkey likely did not reach the app. Use the tray menu or `python3 toggl_tray.py start "Task"` until the hotkey notification/conflict is resolved.

**Need to review missing time**
- Run `python3 toggl_tray.py audit 2026-05-01 2026-05-31`.
- The audit shows per-day totals, blank descriptions, local pending entries, and large gaps.
- Use `--local-only` to inspect only unsynced local entries without spending API requests.

**No sound on toggle**
- Sounds use `paplay` (PulseAudio). Make sure it's installed: `sudo apt install pulseaudio-utils`

## License

MIT
