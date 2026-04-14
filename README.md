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
secret-tool store --label='Toggl API Token' service toggl username api_token
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

## Autostart on Login

Create `~/.config/autostart/toggl-tray.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Toggl Tray
Exec=/path/to/toggl-linux/.venv/bin/python /path/to/toggl-linux/toggl_tray.py
Hidden=false
X-GNOME-Autostart-enabled=true
```

Replace `/path/to/toggl-linux` with the actual path where you cloned the repo.

## How It Works

- **One file** (`toggl_tray.py`, ~700 lines) — no complex project structure
- Talks to [Toggl Track API v9](https://engineering.toggl.com/docs/track/) using basic auth
- Global hotkey uses X11 `XGrabKey` — the key is grabbed at the X server level, so no other app sees `Ctrl+Shift+T`
- If the API is unreachable (no internet, rate limited), actions are queued locally and synced automatically every 30 seconds
- State is saved to `~/.local/share/toggl-tray/state.json`

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

The free plan allows **30 API requests per hour**. This app only calls the API when you explicitly do something (toggle, view entries, edit). Normal usage is ~5 requests per hour. You won't hit the limit unless you toggle hundreds of times.

## Troubleshooting

**No tray icon appears**
- Make sure your desktop has a system tray / notification area. On GNOME, you may need the [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/).
- Install the appindicator library: `sudo apt install gir1.2-ayatanaappindicator3-0.1`

**Ctrl+Shift+T doesn't work**
- This app only works on X11, not Wayland. Check with `echo $XDG_SESSION_TYPE`.
- Another app may have already grabbed that key combo.

**"Another instance is already running"**
- Delete the stale lock file: `rm ~/.local/share/toggl-tray/toggl-tray.lock`

**No sound on toggle**
- Sounds use `paplay` (PulseAudio). Make sure it's installed: `sudo apt install pulseaudio-utils`

## License

MIT
