# Timer scenarios

Provisional priorities based on the user's request; frequency has not been measured.

```text
Manage my time
|
+-- 1. Switch projects
|   +-- Choose the project and task description, or resume a previous task
|   +-- Stop the previous interval and start a new one
|   +-- Show the new project's saved icon color
|
+-- 2. Resume earlier work
|   +-- Choose Resume Previous Task in the timer window
|   +-- Or choose Start Again on a completed Today entry in Vicinae
|   +-- Create a new interval with the saved task, project and workspace
|
+-- 3. Start working
|   +-- Keep the selected project or choose another
|   +-- Enter a task description and start
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

The native timer window groups project, description, start time and Start/Stop together. **Save Changes** corrects the current interval; **Start New Task** ends it and starts another. Enter or Ctrl+Enter saves corrections; Ctrl+Shift+Enter starts a new task while tracking. Start time shows its date: enter a full date and time to correct an older interval, or HH:MM for today. Stop, Sync and Refresh preserve unsaved drafts.

Today's entries stay visible. **More** holds panel visibility, Sync Now, Refresh Projects and Connection inside the timer window. Panel selections reload from the saved configuration whenever More opens. Vicinae exposes the same common timer actions through searchable native lists/forms. Stale forms must not edit a replacement timer, and offline actions remain recoverable.

**Resume Previous Task** lists up to 20 distinct completed tasks from today's cached or pending entries, most recent first. It loads today's history if no completed tasks are cached. **Start Again** stops any current interval and creates a new one, preserving the selected description, project and workspace. It never extends or relabels the earlier interval. Selection and current-timer changes are checked again before starting; stale selections require a refresh.
