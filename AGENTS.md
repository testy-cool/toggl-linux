# Repository Guidelines

## Project Structure & Module Organization

This is a compact Python tray application. The main implementation lives in `toggl_tray.py`, including API access, X11 hotkey handling, GTK dialogs, tray icon behavior, state persistence, and offline sync. Tests are in `test_toggl_tray.py` and mock GTK, Xlib, and pystray before importing the app. Runtime dependencies are listed in `requirements.txt`. User-facing documentation is in `README.md`, and `toggl_icon.webp` is the source tray icon asset.

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
python -m pytest test_toggl_tray.py -v
```

Install required Linux system packages before running the tray app; see `README.md` for Debian/Ubuntu/Linux Mint and Fedora commands.

## Coding Style & Naming Conventions

Use standard Python style with 4-space indentation, `snake_case` for functions and variables, and `UPPER_CASE` for constants such as `STATE_FILE` and `API_BASE`. Keep changes localized: this project intentionally keeps most behavior in `toggl_tray.py`. Prefer small helper functions when a block handles a distinct concern, but avoid broad restructuring unless it directly supports the change. Keep comments brief and useful for non-obvious behavior, especially around X11 grabs, keyring access, and offline sync.

## Testing Guidelines

Tests use `pytest` with `unittest.mock`. Add tests to `test_toggl_tray.py` near the behavior being covered, following the existing class-based grouping such as `TestElapsedStr` and `TestSyncPending`. Use `tmp_path` and patched module constants for filesystem state instead of writing to `~/.local/share/toggl-tray`. Mock network, GTK, Xlib, and tray interactions; do not require a live Toggl account or desktop session for tests.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries such as `Add offline queue, single instance lock, and sound-only feedback` and `Fix tray icon color switching and use system sounds`. Follow that style: capitalize the first word, describe the behavior changed, and keep the subject concise.

Pull requests should include a clear description, test results, linked issues when applicable, and screenshots or short notes for visible tray/dialog changes. Call out changes that affect state files, keyring lookup, Toggl API behavior, or desktop compatibility.

## Security & Configuration Tips

Never commit API tokens, local state, or pending queues. Prefer GNOME Keyring via `secret-tool`; `TOGGL_API_TOKEN` is acceptable for local sessions only. Treat `~/.local/share/toggl-tray/state.json` and `pending.json` as user data.
