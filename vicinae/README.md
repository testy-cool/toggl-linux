# Toggl Tray for Vicinae

Manage the running Toggl Tray timer from Vicinae: start/stop, edit the description and project, and edit descriptions/projects on today's cloud and pending entries. Project dots use the project's saved color. Changes are sent to the running tray over a private Unix socket; the extension never reads an API token or writes timer files itself.

## Install

First run the tray from this repository and install its CLI launcher:

```sh
python toggl_tray.py install-app
python toggl_tray.py run
```

In this directory, install dependencies and build the extension:

```sh
npm ci
npm run check
npm test
npm run build
```

The Vicinae SDK installs the built command under your Vicinae extension data directory. Open Vicinae and run **Toggl Tray → Manage Timer**. If the CLI is elsewhere, set **Toggl CLI path** in the extension preferences. The default is `~/.local/bin/toggl`; paths are executed directly without a shell.

**Enter** starts or stops the current timer. **Edit Description and Project** opens the form for the current timer or a selected entry. **Ctrl+R** refreshes today's cloud entries. Status updates every two seconds using local reads; cloud entry refresh is limited to once per minute and respects the tray's request budget. Project metadata follows the tray's hourly cache. Offline entries remain editable. Cloud edits report a failure without changing local state if the backend is unavailable.

To update, run `npm ci`, `npm run check`, `npm test`, and `npm run build` again. To remove the extension, uninstall **Toggl Tray** in Vicinae's extension settings; this leaves your tray app, credentials, timers, and pending queue intact.

## Verification

`python toggl_tray.py control snapshot` returns the same state used by the extension. `npm run build -- --out dist` validates and builds without installing. Native Vicinae `List`, `Form`, and `Action` components render the interface; no browser or custom HTML/CSS controls are used.
