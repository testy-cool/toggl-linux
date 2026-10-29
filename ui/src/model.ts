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
  description: string;
};
export type Entry = {
  id: Id;
  workspace_id: Id;
  project_id: Id | null;
  task_id: Id | null;
  description: string;
  start: string;
  stop: string | null;
  duration: number;
  _offline?: boolean;
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
    .slice(0, 16);
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
        sameId(t.workspace_id, s.state.workspace_id),
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
  (w.task != null || s.state.description.trim() === w.description.trim());
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
    entry,
    description:
      mode === "task"
        ? work!.task!.name
        : (original?.description ?? work?.description ?? ""),
    project: String(original?.project_id ?? work?.project_id ?? "none"),
    task: String(original?.task_id ?? work?.task_id ?? "none"),
    start: localTime(s.state.start_time),
  };
}
export function commandFor(d: Draft, asTask = false): Command {
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
      ...(d.mode === "entry" ? { entry: d.entry } : { expected: s.state }),
    },
  };
}
