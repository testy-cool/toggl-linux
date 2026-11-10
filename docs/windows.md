# Windows, projects and tasks

What each window in the tray app does. Back to the [README](../README.md).

## Project colors

Right-click the tray icon and open **Project** to select a saved project or **No project** directly. The current choice is checked. Selection updates the current interval, or the remembered project while stopped, without starting a new interval. **Refresh projects** reloads the choices. Pending offline timers remain editable; if a cloud edit cannot be saved, the checked choice stays as it was.

The same submenu includes **New Project…**, **Edit Selected Project…**, and **Manage Projects…**. New/Edit opens a name and color form; Manage opens your account's project page for archives, deletion, and advanced settings. The timer window's **Projects** button and Vicinae's **Projects** section offer the same actions. Creating a project adds it to the choices without assigning it to the timer. Saving a name or color immediately refreshes shared project lists while keeping existing time entries attached. Creation/editing requires a connection; failed or uncertain writes keep the form text and ask you to refresh before retrying.

Project dots and the running tray icon share a local display palette from the selected **GrokNight** or **Gruvbox** theme. Switching themes updates both together. The display colors are assigned from workspace project IDs, independently of list order, search, or project names; the account's saved project colors remain available in the project editor. Timers without a project use a stable theme color derived from their description; stopped timers stay grey. Metadata cached in `~/.local/share/toggl-tray/projects.json` keeps the display colors available offline. Rendering never makes account requests.

## Edit the current timer quickly

Choose **Open Timer** in the tray to edit the project, real task, description, and start time together. **Save Changes** (Enter or Ctrl+Enter) keeps the same interval; **Start New Entry** (Ctrl+Shift+Enter) closes it and starts another. A task is reusable OpenTickly metadata, while the description is optional detail for one time entry. Start time includes the date; HH:MM means today, while a full date and time can correct an older interval. Stop and sync preserve unsaved drafts. Start/Stop and today's entries stay visible; **More** holds sync, project refresh, account connection, and panel visibility in the same window. Vicinae offers the same Start New Entry action. See [`timer-scenarios.md`](timer-scenarios.md) for the scenario tree.

Use **Resume Recent Activity** in the timer window, or **Start Again** on a completed Today entry in Vicinae, to return to earlier work. The previous description, project, task, and workspace are copied into a new interval when the saved task is still active; a running timer is stopped first. Today's cached and pending entries are supported, including offline. Resume never changes the earlier interval.

## Work window

Left-click opens an offline HTML/CSS work window built with React, ReUI and shadcn.
The timer, queue, API credentials and sync remain in the Python tray process. A
separate pywebview process talks only to its private control socket. Closing the
window hides it, so reopening preserves search, scroll and any unfinished draft.
The current timer stays visible while browsing; editing happens in the same window.

Use the palette button in the header to switch between **Gruvbox** and
**GrokNight**. GrokNight uses the [Grok themes palette](https://github.com/promptegrity/grok-themes):
charcoal surfaces, pale text, violet accents, and amber focus and running cues.
The choice is saved locally in `appearance.json`, independently of timers,
account settings, and tree expansion.

Projects and Tasks show a ReUI tree in locally bundled Inter. Names use 14px
text, and supporting text stays at 12px or larger. Projects with both kinds of
work group their rows under **Saved tasks** and **Recent activity** instead of
repeating a type label on every row. Narrow work rows put the name and Today
total first, then place status and actions on a second line. Focus highlights
the complete row.

Click an expandable name or press Enter to browse today's sessions without
opening an editor or starting a timer. Sessions show a 24-hour local time range
until you activate it, then use ReUI's typed time controls for direct corrections.
**Edit entries** expands a collapsed row and moves focus to its first range.
Keyboard activation moves focus to the start field; **Done** returns focus to the
compact range action. Both time fields and **Done** remain usable while one
correction saves, and a second correction waits for the first response. The
session pencil opens the full entry editor for its
description, project, task, and times. Empty or repeated session descriptions
stay hidden when the parent already names the work, while pending-sync status
remains visible.

Play is the only direct start or switch action. A saved task's pencil opens its
metadata editor. The task API cannot move an existing task, so **Create in
another project** makes a separate task and leaves the original task and its
time entries intact. The copy starts with an editable, unique name and rejects
case-insensitive duplicates before submission. If no other active project is
available, the disabled copy action explains why. Collapsed saved tasks and
recent activity with loaded sessions show **Edit entries**; expanded rows show
their correction controls instead. A recent activity's menu still offers **New timer with these
details**, **Save as task**, and **Hide suggestion**. Empty rows keep their start
and task-editing actions, and empty projects keep **New task**.

The running timer uses a compact status strip. Its running title comes first,
with project and task context on a quieter second line. Focusing or hovering the
text shows the complete title and context when the visible lines truncate. A
visible pencil opens the editor. Started, elapsed time, and Stop remain available
at narrow widths. **New** beside the tabs offers **New task** and **New timer**.
The editors use the available height, scroll fields that do not fit, and keep
Save visible. **Open timer** and **Edit description** open the same editor;
**Today's entries** opens the Today tab. Stop keeps an unfinished description
attached to the completed interval, and offline sync cannot replace a draft when
it assigns a cloud ID.

Closing the managed window hides the existing process, so its draft, search,
and scroll position remain in memory. Project and saved-task expansion also
survives a complete UI process restart in private
`~/.local/share/toggl-tray/ui-preferences.json`. The file stores only allowlisted
IDs for the current account and workspace; credentials and timer writes stay out
of it. A partial project or task cache cannot erase an ID already validated for
that scope, while an unseen ID still needs current metadata. A filtered tree
preserves choices for projects outside the filter. A project that first gains
work opens by default so the new task remains visible,
while an explicit collapse survives reloads. Search can reveal matches
temporarily, then clearing search restores the saved tree.

The Today tab groups sessions by task or activity with durations and subtotals.
Totals count only the selected workspace and the part of each interval within
today's local midnight boundaries, including daylight-saving days and overnight
pending entries. The current timer is added once. Unknown or incomplete totals
show a dash or an approximation rather than a false zero. If a cloud refresh fails,
the footer retains known time as an explicit estimate and the warning appears once
above the work list.

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

## GTK compatibility panel

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
