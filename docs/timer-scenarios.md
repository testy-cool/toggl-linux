# Timer scenarios

Provisional priorities based on the user's request; frequency has not been measured.

```text
Manage my time
|
+-- 1. Switch projects or tasks
|   +-- Choose the project, reusable task, and optional entry description
|   +-- Stop the previous interval and start a new one
|   +-- Show the new project's saved icon color
|
+-- 2. Resume earlier work
|   +-- Choose Resume Recent Activity in the timer window
|   +-- Or choose Start Again on a completed Today entry in Vicinae
|   +-- Create a new interval with the saved description, project, task, and workspace
|
+-- 3. Start working
|   +-- Keep the selected project or choose another
|   +-- Choose a task, add optional entry details, and start
|
+-- 4. Correct the current timer
|   +-- Edit project, description, or start time together
|   +-- Save without creating another interval
|
+-- 5. Pause or finish
|   +-- Stop with one action
|
+-- 6. Review today
|   +-- Open entries and durations
|   +-- Correct cloud or pending entries
|
+-- Occasional settings
    +-- Panel timer and description visibility
    +-- Sync status and retry
    +-- Account connection
```

```text
Project lifecycle (tray, timer window, Vicinae)
|
+-- Choose an existing project or No project
+-- New Project
|   +-- Enter name and choose color
|   +-- Create; project becomes available in every picker
|   +-- Select it explicitly when ready to assign the current interval
+-- Edit Selected Project
|   +-- Change name or color; keep the same project ID and history
|   +-- Refresh names and icon colors immediately
+-- Manage Projects
    +-- Open the configured web Projects page
    +-- Archive, restore, delete, or change advanced settings there
```

Project name/color forms retain drafts on failure and detect changed accounts, workspaces, and project metadata. Project writes are not queued for replay. The native timer's unsaved description, start time, and project selection survive project-list refreshes.

```text
Task lifecycle (tray, timer window, Vicinae)
|
+-- New Task or Create & Start under an active project
+-- Rename a task without changing its ID or past entries
+-- Archive a task so it disappears from new-entry choices
+-- Restore an archived task
+-- Permanent deletion is not exposed
```

Task names are server metadata and remain separate from descriptions on individual time entries. Historical descriptions appear as **Recent activities**, collapsed by default; each can be started again, saved as a real task, or hidden locally. Servers without task support retain this activity fallback.

The native timer window groups project, task, description, start time and Start/Stop together. **Save Changes** corrects the current interval; **Start New Entry** ends it and starts another. Enter or Ctrl+Enter saves corrections; Ctrl+Shift+Enter starts a new entry while tracking. Start time shows its date: enter a full date and time to correct an older interval, or HH:MM for today. Stop, Sync and Refresh preserve unsaved drafts.

Today's entries stay visible. **More** holds panel visibility, Sync Now, Refresh Projects and Connection inside the timer window. Panel selections reload from the saved configuration whenever More opens. Vicinae exposes the same common timer actions through searchable native lists/forms. Stale forms must not edit a replacement timer, and offline actions remain recoverable.

**Resume Recent Activity** lists up to 20 distinct completed entries from today's cache or pending queue, most recent first. It loads today's history if nothing completed is cached. **Start Again** stops any current interval and creates a new one, preserving the selected description, project, active task, and workspace. It never extends or relabels the earlier interval. Selection and current-timer changes are checked again before starting; stale selections require a refresh.
