# Troubleshooting

Back to the [README](../README.md). Logs and recovery details are in [how-it-works.md](how-it-works.md).

## No tray icon appears
- Make sure your desktop has a system tray / notification area. On GNOME, you may need the [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/).
- Install the appindicator library: `sudo apt install gir1.2-ayatanaappindicator3-0.1`

## Ctrl+Shift+T doesn't work
- This app only works on X11, not Wayland. Check with `echo $XDG_SESSION_TYPE`.
- Another app may have already grabbed that key combo.
- If the tray app cannot grab the hotkey, it prints an error and plays the error sound. You can still use the tray menu or terminal commands.

## "Another instance is already running"
- The message includes the PID of the running copy. The lock is released automatically when that process exits, so quit or stop that process instead of deleting the lock file; deleting it while the app runs allows a second copy to start.

## Tray entries are empty but tracking looks active
- Run `toggl status`. Local/offline entries are shown from `~/.local/share/toggl-tray/pending.json`.
- Run `toggl local` to list only local pending entries for today.

## Nothing is being tracked
- Run `toggl status` to check whether a local timer or pending queue exists.
- Run `toggl doctor` for state file, pending file, token, ledger, and rate-limit health.
- Run `toggl entries` to check today's Toggl entries.
- If both are empty, the hotkey likely did not reach the app. Use the tray menu or `toggl start "Task"` until the hotkey conflict is resolved.

## Need to review missing time
- Run `toggl audit 2026-05-01 2026-05-31`.
- The audit shows per-day totals, blank descriptions, local pending entries, and large gaps.
- Use `--local-only` to inspect only unsynced local entries without spending API requests.

## No sound on toggle
- Sounds use `paplay` (PulseAudio). Make sure it's installed: `sudo apt install pulseaudio-utils`

