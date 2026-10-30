import { sameId } from "./model.ts";
import type { Entry, Id, Snapshot, Work } from "./model";

export type TodayRow = {
  key: string;
  entry: Entry;
  from: number;
  to: number;
  seconds: number;
};
export type TodayGroup = {
  key: string;
  project_id: Id | null;
  task_id: Id | null;
  description: string;
  label: string;
  seconds: number;
  rows: TodayRow[];
};
export type Today = {
  date: string;
  loaded: boolean;
  incomplete: boolean;
  pending: number;
  seconds: number;
  groups: Map<string, TodayGroup>;
};

export function localDay(now: number) {
  const date = new Date(now);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

const descriptionKey = (description: string | null) =>
  (description ?? "").trim().replace(/\s+/g, " ").toLocaleLowerCase();
export const workTimeKey = (
  workspace: Id | null,
  project: Id | null,
  task: Id | null,
  description: string | null,
  activity?: string,
) =>
  JSON.stringify([
    String(workspace),
    String(project ?? "none"),
    task == null ? "activity" : "task",
    task == null ? (activity ?? descriptionKey(description)) : String(task),
  ]);
export const timeKeyForWork = (s: Snapshot, work: Work) =>
  workTimeKey(
    s.state.workspace_id,
    work.project_id,
    work.task_id,
    work.description,
    work.activity_key,
  );

export function shortTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0m";
  const minutes = Math.floor(seconds / 60);
  if (minutes === 0) return "<1m";
  return minutes < 60
    ? `${minutes}m`
    : `${Math.floor(minutes / 60)}h${minutes % 60 ? ` ${minutes % 60}m` : ""}`;
}

export const clockRange = (row: TodayRow) => {
  const options: Intl.DateTimeFormatOptions = {
    hour: "2-digit",
    minute: "2-digit",
  };
  return `${new Date(row.from).toLocaleTimeString([], options)}–${row.entry.stop ? new Date(row.to).toLocaleTimeString([], options) : "now"}`;
};

export function todayTotals(s: Snapshot, now: number): Today {
  const date = new Date(now);
  const midnight = new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate(),
  ).getTime();
  const end = new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate() + 1,
  ).getTime();
  const result: Today = {
    date: localDay(now),
    loaded: s.today_date === localDay(now) && !!s.today_fetched_at,
    incomplete: !!s.today_incomplete || !!s.today_error,
    pending: 0,
    seconds: 0,
    groups: new Map(),
  };
  const sameWorkspace = (entry: Entry) =>
    sameId(entry.workspace_id, s.state.workspace_id);
  const all = s.entries.filter(sameWorkspace);
  const pending = all.filter((e) => e._offline);
  const identity = (entry: Entry) => String(entry._remote_entry_id ?? entry.id);
  const unique = new Map<string, Entry>();
  for (const entry of all.filter((e) => !e._offline))
    unique.set(identity(entry), entry);
  for (const entry of pending) {
    // A pending create can already have appeared in a cloud reply before its
    // local confirmation. Only collapse a matching interval, never two cloud IDs.
    if (entry._remote_entry_id == null) {
      const matches = [...unique].filter(
        ([, cloud]) =>
          !cloud._offline &&
          Date.parse(cloud.start) === Date.parse(entry.start) &&
          sameId(cloud.project_id, entry.project_id) &&
          sameId(cloud.task_id, entry.task_id) &&
          (cloud.description ?? "") === (entry.description ?? ""),
      );
      if (matches.length === 1) unique.delete(matches[0][0]);
      else if (matches.length > 1) result.incomplete = true;
    }
    unique.set(identity(entry), entry);
  }
  const current = s.state;
  const active =
    current.tracking &&
    current.start_time &&
    sameId(current.workspace_id, s.project_context.workspace_id);
  for (const [id, entry] of unique) {
    if (
      active &&
      ((current.entry_id != null &&
        sameId(identity(entry), current.entry_id)) ||
        (current.entry_id == null &&
          !entry.stop &&
          Date.parse(entry.start) === Date.parse(current.start_time!) &&
          sameId(entry.project_id, current.project_id) &&
          sameId(entry.task_id, current.task_id) &&
          (entry.description ?? "") === (current.description ?? "")))
    )
      unique.delete(id);
  }
  if (active) {
    const entry: Entry = {
      ...s.current_pending,
      id: current.entry_id ?? "current",
      workspace_id: current.workspace_id!,
      project_id: current.project_id,
      task_id: current.task_id,
      description: current.description,
      start: current.start_time!,
      stop: null,
      duration: -1,
      _offline: current.entry_id == null,
      _activity_key: s.current_activity_key,
    };
    unique.set("current", entry);
  }
  for (const [key, entry] of unique) {
    if (entry.description != null && typeof entry.description !== "string") {
      result.incomplete = true;
      continue;
    }
    const from = Math.max(midnight, Date.parse(entry.start));
    const to = Math.min(end, now, entry.stop ? Date.parse(entry.stop) : now);
    if (!Number.isFinite(from) || !Number.isFinite(to)) {
      result.incomplete = true;
      continue;
    }
    if (!entry.stop && key !== "current") {
      result.incomplete = true;
      continue;
    }
    if (to < from || to <= midnight || from >= end) continue;
    const seconds = Math.floor((to - from) / 1000);
    const groupKey = workTimeKey(
      entry.workspace_id,
      entry.project_id,
      entry.task_id,
      entry.description,
      entry._activity_key,
    );
    let group = result.groups.get(groupKey);
    if (!group) {
      const label =
        entry.task_id != null
          ? (s.tasks.find(
              (t) =>
                sameId(t.workspace_id, entry.workspace_id) &&
                sameId(t.id, entry.task_id),
            )?.name ?? `Task ${entry.task_id}`)
          : entry.description || "No description";
      group = {
        key: groupKey,
        project_id: entry.project_id,
        task_id: entry.task_id,
        description: entry.description ?? "",
        label,
        seconds: 0,
        rows: [],
      };
      result.groups.set(groupKey, group);
    }
    group.rows.push({ key, entry, from, to, seconds });
    group.seconds += seconds;
    result.seconds += seconds;
    if (entry._offline) result.pending += 1;
    if (entry._needs_full_review || entry._pending_missing?.length)
      result.incomplete = true;
  }
  for (const group of result.groups.values())
    group.rows.sort((a, b) => b.from - a.from);
  return result;
}

export const todayLabel = (today: Today, seconds: number) =>
  `${(!today.loaded || today.incomplete) && seconds > 0 ? "≈" : ""}${(today.loaded && !today.incomplete) || seconds > 0 ? shortTime(seconds) : "—"}`;
export const todayGroupLabel = (s: Snapshot, group: TodayGroup) => {
  const project = s.projects.find(
    (p) =>
      sameId(p.workspace_id, s.state.workspace_id) &&
      sameId(p.id, group.project_id),
  );
  return `${project?.name ?? (group.project_id == null ? "No project" : `Project ${group.project_id}`)} · ${group.label}`;
};
