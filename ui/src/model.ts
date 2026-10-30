export type Id = string | number;
export type Project = {
  id: Id;
  workspace_id: Id;
  name: string;
  color?: string;
  active?: boolean;
};
export type Task = {
  id: Id;
  workspace_id: Id;
  project_id: Id;
  name: string;
  active?: boolean;
};
export type Timer = {
  tracking: boolean;
  entry_id: Id | null;
  start_time: string | null;
  workspace_id: Id | null;
  project_id: Id | null;
  task_id: Id | null;
  description: string | null;
};
export type Entry = {
  id: Id;
  workspace_id: Id;
  project_id: Id | null;
  task_id: Id | null;
  description: string | null;
  start: string;
  stop: string | null;
  duration: number;
  _offline?: boolean;
  _needs_full_review?: boolean;
  _pending_ids?: string[];
  _pending_kind?: string;
  _remote_entry_id?: Id | null;
};
export type Activity = {
  description: string;
  last_used: number;
  count: number;
};
export type Snapshot = {
  state: Timer;
  projects: Project[];
  tasks: Task[];
  entries: Entry[];
  stopped_entry?: Entry;
  current_pending?: Entry | null;
  draft_resolution?: {
    source: Entry;
    entry: Entry;
    context: Snapshot["project_context"];
  };
  activities?: {
    id: Id | null;
    name: string;
    last_used: number;
    activities: Activity[];
  }[];
  project_context: { account: string | null; workspace_id: Id | null };
  task_capability: string;
  pending_count: number;
  warning: string;
};
export type Work = {
  key: string;
  project_id: Id | null;
  description: string;
  task_id: Id | null;
  label: string;
  task?: Task;
  recent: number;
};
export type Mode = "current" | "new" | "task" | "new-task" | "entry";
export type Draft = {
  mode: Mode;
  snapshot: Snapshot;
  work?: Work;
  entry?: Entry;
  description: string;
  project: string;
  task: string;
  start: string;
  stop: string;
  reviewed: boolean;
  pending?: Entry;
};
export type Command = { action: string; payload: Record<string, unknown> };
export const sameId = (a: unknown, b: unknown) =>
  String(a ?? "none") === String(b ?? "none");
export const projectName = (s: Snapshot, id: Id | null) =>
  s.projects.find((p) => sameId(p.id, id))?.name ??
  (id == null ? "No project" : "Current project");
export const taskName = (s: Snapshot, id: Id | null) =>
  s.tasks.find((t) => sameId(t.id, id))?.name ??
  (id == null ? "" : "Current task");
export const timerTitle = (s: Snapshot) =>
  s.state.description || taskName(s, s.state.task_id) || "No description";
export const scope = (d: Draft) =>
  d.entry?.workspace_id ?? d.snapshot.state.workspace_id;
export const localTime = (stamp: string | null) => {
  if (!stamp) return "";
  const date = new Date(stamp);
  if (!Number.isFinite(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 19);
};
export function duration(seconds: number) {
  const value = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  return `${Math.floor(value / 3600)}:${String(Math.floor((value % 3600) / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}
export const elapsed = (timer: Timer, now: number) =>
  duration(
    timer.tracking && timer.start_time
      ? (now - Date.parse(timer.start_time)) / 1000
      : 0,
  );
export const contextMatches = (a: Snapshot, b: Snapshot) =>
  a.project_context.account === b.project_context.account &&
  sameId(a.project_context.workspace_id, b.project_context.workspace_id);
export const timerMatches = (a: Timer, b: Timer) =>
  [
    "tracking",
    "entry_id",
    "start_time",
    "workspace_id",
    "description",
    "project_id",
    "task_id",
  ].every((k) => a[k as keyof Timer] === b[k as keyof Timer]);

export function samePending(
  a: Entry | undefined | null,
  b: Entry | undefined | null,
) {
  const left = a?._pending_ids,
    right = b?._pending_ids;
  return (
    !!a &&
    !!b &&
    Array.isArray(left) &&
    Array.isArray(right) &&
    !!left.length &&
    left.length === right.length &&
    [...left].sort().every((id, index) => id === [...right].sort()[index]) &&
    [
      "start",
      "stop",
      "description",
      "project_id",
      "task_id",
      "workspace_id",
      "_pending_kind",
      "_remote_entry_id",
    ].every((key) => a[key as keyof Entry] === b[key as keyof Entry])
  );
}
export const pendingSource = (d: Draft) =>
  d.mode === "current" || d.mode === "new"
    ? d.pending
    : d.entry?._offline
      ? d.entry
      : undefined;
export const isCurrentEntry = (s: Snapshot, entry: Entry) =>
  s.state.tracking &&
  entry.duration < 0 &&
  (s.state.entry_id != null
    ? sameId(entry.id, s.state.entry_id)
    : samePending(entry, s.current_pending));

function rebaseFields(d: Draft, source: Entry, entry: Entry): Draft {
  const projectChanged = !sameId(d.project, source.project_id);
  return {
    ...d,
    description:
      d.description === (source.description ?? "")
        ? (entry.description ?? "")
        : d.description,
    project: projectChanged ? d.project : String(entry.project_id ?? "none"),
    task:
      projectChanged || !sameId(d.task, source.task_id)
        ? d.task
        : String(entry.task_id ?? "none"),
    start:
      d.start === localTime(source.start) ? localTime(entry.start) : d.start,
    stop: d.stop === localTime(source.stop) ? localTime(entry.stop) : d.stop,
  };
}

export function rebaseSyncedDraft(d: Draft, result: Snapshot): Draft {
  const resolution = result.draft_resolution,
    source = pendingSource(d);
  if (
    !resolution ||
    !source ||
    !contextMatches(d.snapshot, result) ||
    resolution.context.account !== result.project_context.account ||
    !sameId(
      resolution.context.workspace_id,
      result.project_context.workspace_id,
    ) ||
    !samePending(source, resolution.source)
  )
    return d;
  const entry = resolution.entry;
  if (!entry.stop) {
    const state = result.state;
    if (
      !state.tracking ||
      !sameId(state.entry_id, entry.id) ||
      !sameId(state.workspace_id, entry.workspace_id) ||
      state.start_time !== entry.start ||
      (state.description ?? "") !== (entry.description ?? "") ||
      !sameId(state.project_id, entry.project_id) ||
      !sameId(state.task_id, entry.task_id)
    )
      return d;
    return {
      ...(d.mode === "new" ? d : rebaseFields(d, source, entry)),
      mode: d.mode === "new" ? "new" : "current",
      entry: undefined,
      pending: undefined,
      snapshot: structuredClone(result),
    };
  }
  if (d.mode !== "entry") return d; // External stops never redirect a running-timer form.
  return {
    ...rebaseFields(d, source, entry),
    entry: structuredClone(entry),
    pending: undefined,
    snapshot: structuredClone(result),
  };
}

export function afterOwnStop(draft: Draft, result: Snapshot): Draft {
  if (!contextMatches(draft.snapshot, result)) return draft;
  if (draft.mode === "new" && !result.state.tracking)
    return { ...draft, pending: undefined, snapshot: structuredClone(result) };
  if (draft.mode !== "current") return draft;
  const original = draft.snapshot.state;
  const resolution = result.draft_resolution;
  const resolved =
    resolution &&
    resolution.context.account === result.project_context.account &&
    sameId(
      resolution.context.workspace_id,
      result.project_context.workspace_id,
    ) &&
    samePending(pendingSource(draft), resolution.source)
      ? resolution.entry
      : undefined;
  const candidates = resolved?.stop
    ? [resolved]
    : result.stopped_entry
      ? [result.stopped_entry]
      : result.entries;
  const matches = candidates.filter(
    (entry) =>
      entry.stop &&
      entry.duration >= 0 &&
      sameId(entry.workspace_id, original.workspace_id) &&
      ((resolved?.id ?? original.entry_id) != null
        ? sameId(entry.id, resolved?.id ?? original.entry_id)
        : Date.parse(entry.start) === Date.parse(original.start_time ?? "")),
  );
  // Only our explicit Stop action can retarget a draft, and only to its exact
  // completed interval. External status updates never rebase a form.
  return matches.length === 1
    ? {
        ...rebaseFields(
          draft,
          {
            id: original.entry_id ?? matches[0].id,
            workspace_id: original.workspace_id!,
            description: original.description,
            project_id: original.project_id,
            task_id: original.task_id,
            start: original.start_time!,
            stop: null,
            duration: -1,
          },
          matches[0],
        ),
        mode: "entry",
        entry: structuredClone(matches[0]),
        snapshot: structuredClone(result),
        pending: undefined,
        start:
          draft.start === localTime(original.start_time)
            ? localTime(matches[0].start)
            : draft.start,
        stop: localTime(matches[0].stop),
      }
    : draft;
}

export function workItems(s: Snapshot, includeArchived = false): Work[] {
  const recent: Work[] = (s.activities ?? []).flatMap((p) =>
    p.activities.map((a) => ({
      key: `activity:${p.id}:${a.description}`,
      project_id: p.id,
      task_id: null,
      description: a.description,
      label: a.description || "No description",
      recent: a.last_used,
    })),
  );
  const tasks: Work[] = s.tasks
    .filter(
      (t) =>
        (includeArchived || t.active !== false) &&
        sameId(t.workspace_id, s.state.workspace_id) &&
        s.projects.some(
          (p) =>
            sameId(p.id, t.project_id) &&
            sameId(p.workspace_id, t.workspace_id) &&
            p.active !== false,
        ),
    )
    .map((t) => ({
      key: `task:${t.id}`,
      project_id: t.project_id,
      task_id: t.id,
      description: "",
      label: t.name,
      task: t,
      recent:
        s.activities?.find((p) => sameId(p.id, t.project_id))?.last_used ?? 0,
    }));
  return [...tasks, ...recent].sort(
    (a, b) => b.recent - a.recent || Number(!!b.task) - Number(!!a.task),
  );
}
export const running = (s: Snapshot, w: Work) =>
  s.state.tracking &&
  sameId(s.state.project_id, w.project_id) &&
  sameId(s.state.task_id, w.task_id) &&
  (w.task != null ||
    (s.state.description ?? "").trim() === w.description.trim());
export function draftFor(
  s: Snapshot,
  mode: Mode,
  work?: Work,
  entry?: Entry,
): Draft {
  const original = mode === "current" ? s.state : entry;
  return {
    mode,
    snapshot: structuredClone(s),
    work,
    entry: mode === "entry" ? entry : undefined,
    description:
      mode === "task"
        ? work!.task!.name
        : (original?.description ?? work?.description ?? ""),
    project: String(original?.project_id ?? work?.project_id ?? "none"),
    task: String(original?.task_id ?? work?.task_id ?? "none"),
    start: localTime(
      mode === "entry" ? (entry?.start ?? null) : s.state.start_time,
    ),
    stop: localTime(mode === "entry" ? (entry?.stop ?? null) : null),
    reviewed: false,
    pending:
      (mode === "current" || mode === "new") &&
      s.state.entry_id == null &&
      s.current_pending
        ? structuredClone(s.current_pending)
        : undefined,
  };
}
export function commandFor(d: Draft, asTask = false): Command {
  if (d.mode === "entry" && d.entry?._needs_full_review && !d.reviewed)
    throw new Error("Review and approve all interval fields before saving");
  const s = d.snapshot;
  const selectedProject = s.projects.find(
    (p) => sameId(p.id, d.project) && sameId(p.workspace_id, scope(d)),
  );
  const original = d.mode === "entry" ? d.entry! : s.state;
  const project =
    d.project === "none" ? null : (selectedProject?.id ?? original.project_id);
  const task =
    d.task === "none"
      ? null
      : (s.tasks.find(
          (t) =>
            sameId(t.id, d.task) &&
            sameId(t.workspace_id, scope(d)) &&
            sameId(t.project_id, project),
        )?.id ?? original.task_id);
  if (asTask || d.mode === "task" || d.mode === "new-task")
    return {
      action: d.mode === "task" ? "task-update" : "task-create",
      payload: {
        name: d.description.trim(),
        project_id: project,
        context: s.project_context,
        ...(d.mode === "task" ? { task: d.work!.task } : {}),
      },
    };
  const changes: Record<string, unknown> = {
    description: d.description,
    project_id: project,
    task_id: task,
  };
  if (d.mode === "current" || d.mode === "entry") {
    for (const key of Object.keys(changes))
      if (
        key === "description"
          ? changes[key] === original.description
          : sameId(changes[key], original[key as keyof typeof original])
      )
        delete changes[key];
    if (d.mode === "current" && d.start !== localTime(s.state.start_time))
      changes.start = new Date(d.start).toISOString();
    if (d.mode === "entry") {
      if (d.start !== localTime(d.entry!.start))
        changes.start = new Date(d.start).toISOString();
      if (d.stop !== localTime(d.entry!.stop))
        changes.stop = new Date(d.stop).toISOString();
    }
  }
  return {
    action:
      d.mode === "current"
        ? "edit-current"
        : d.mode === "entry"
          ? "edit-entry"
          : "switch",
    payload: {
      changes,
      context: s.project_context,
      ...((d.mode === "current" || d.mode === "new") && d.pending
        ? { draft_entry: d.pending }
        : {}),
      ...(d.mode === "entry" && d.entry?._needs_full_review
        ? { reviewed_interval: d.reviewed }
        : {}),
      ...(d.mode === "entry" ? { entry: d.entry } : { expected: s.state }),
    },
  };
}
