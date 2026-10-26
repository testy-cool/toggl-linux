# Repository Guidelines

## Project Structure & Module Organization

This is a Python tray application split into focused modules:

- `toggl_tray.py`: the core. API access, token and API base, request budget, state files, offline queue and sync, tray icon and panel label, X11 hotkey, tray menu and startup.
- `toggl_cli.py`: terminal commands (status, doctor, entries, audit, start/stop, sync, install-app).
- `toggl_control.py`: the private control socket and the timer/entry commands used by Vicinae and the windows.
- `toggl_projects.py`: account/workspace checks and project create/edit.
- `toggl_task_tree.py`: cached 30-day task history, grouping, corrections and task switching.
- `toggl_tray_popup.py`: the left-click project/task popup.
- `toggl_timer_window.py`, `toggl_project_window.py`, `toggl_entries_window.py`: the timer, project form and Today's entries windows.
- `vicinae/`: the Vicinae launcher extension.

Tests are the `test_*.py` files. `conftest.py` fakes GTK, Xlib and pystray, gives every test a private state folder and blocks network access. Runtime dependencies are listed in `requirements.txt`. User-facing documentation is in `README.md`, and `toggl_icon.webp` is the source tray icon asset.

## Build, Test, and Development Commands

Create and activate a virtual environment before development:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the app locally with:

```bash
python3 toggl_tray.py
```

Run tests with:

```bash
python -m pytest -q
```

Install required Linux system packages before running the tray app; see `README.md` for Debian/Ubuntu/Linux Mint and Fedora commands.

## Coding Style & Naming Conventions

Use standard Python style with 4-space indentation, `snake_case` for functions and variables, and `UPPER_CASE` for constants such as `STATE_FILE` and `API_BASE`. Keep changes localized to the module that owns the behavior. Split-out modules reach shared state as `app.<name>` (`import toggl_tray as app`) so tests that patch `toggl_tray` still reach them. Avoid broad restructuring unless it directly supports the change. Keep comments brief and useful for non-obvious behavior, especially around X11 grabs, keyring access, and offline sync.

## Testing Guidelines

Tests use `pytest` with `unittest.mock`. Add tests to the `test_*.py` file for the module being covered. `conftest.py` already points every app path into the test's own folder; never write to `~/.local/share/toggl-tray`. Mock network, GTK, Xlib, and tray interactions; do not require a live Toggl account or desktop session for tests.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries such as `Add offline queue, single instance lock, and sound-only feedback` and `Fix tray icon color switching and use system sounds`. Follow that style: capitalize the first word, describe the behavior changed, and keep the subject concise.

Pull requests should include a clear description, test results, linked issues when applicable, and screenshots or short notes for visible tray/dialog changes. Call out changes that affect state files, keyring lookup, Toggl API behavior, or desktop compatibility.

## Security & Configuration Tips

Never commit API tokens, local state, or pending queues. Prefer GNOME Keyring via `secret-tool`; `TOGGL_API_TOKEN` is acceptable for local sessions only. Treat `~/.local/share/toggl-tray/state.json` and `pending.json` as user data.
- Keep pending Toggl entries editable, just like synced entries. <!-- convo-miner:1c2c265d8f6b -->
