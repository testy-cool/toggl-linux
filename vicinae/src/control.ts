import { execFile } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";

export interface Project { id: number | string; workspace_id: number | string; name: string; color: string | null; active?: boolean }
export interface Task { id: number | string; workspace_id: number | string; project_id: number | string; name: string; active?: boolean }
export interface Entry { workspace_id?: number | string; id: number | string; description: string; project_id?: number | string | null; task_id?: number | string | null; start: string; stop?: string | null; duration: number; _offline?: boolean; [key: string]: unknown }
export interface Snapshot {
  state: { tracking: boolean; description: string; project_id: number | string | null; task_id: number | string | null; workspace_id: number | string | null; start_time: string | null; entry_id: number | string | null };
  projects: Project[]; tasks: Task[]; task_capability: "unknown" | "supported" | "unavailable"; entries: Entry[]; pending_count: number; warning: string;
  project_context: { workspace_id: number | string | null; account: string | null };
  projects_url: string | null;
  saved_project?: Project;
  saved_task?: Task;
}

export function control(cliPath: string | undefined, action: string, payload: object = {}): Promise<Snapshot> {
  const configured = cliPath?.trim() || join(homedir(), ".local", "bin", "toggl");
  const executable = configured.startsWith("~/") ? join(homedir(), configured.slice(2)) : configured;
  return new Promise((resolve, reject) => {
    execFile(executable, ["control", action, JSON.stringify(payload)], { timeout: 45000, maxBuffer: 2 * 1024 * 1024 }, (error, stdout) => {
      let response;
      try { response = JSON.parse(stdout); } catch {
        reject(new Error(error ? "Could not reach Toggl Tray. Check the CLI path and launch the tray app." : "Invalid response from Toggl Tray.")); return;
      }
      if (!response.ok) { reject(new Error(response.error || "Timer command failed")); return; }
      if (error) { reject(new Error("Timer command failed. Refresh before trying again.")); return; }
      resolve(response.result as Snapshot);
    });
  });
}

export function elapsed(start: string | null, stop?: string | null): string {
  if (!start) return "0:00:00";
  const seconds = Math.max(0, Math.floor(((stop ? Date.parse(stop) : Date.now()) - Date.parse(start)) / 1000));
  if (!Number.isFinite(seconds)) return "0:00:00";
  return `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

export function queueCommands(send: (action: string, payload: object) => Promise<Snapshot>) {
  let tail: Promise<unknown> = Promise.resolve();
  let pending = 0;
  return (action: string, payload: object = {}): Promise<Snapshot | undefined> => {
    if (pending && ["snapshot", "refresh"].includes(action)) return Promise.resolve(undefined);
    pending += 1;
    const result = tail.then(() => send(action, payload)).finally(() => { pending -= 1; });
    tail = result.catch(() => undefined);
    return result;
  };
}

export function projectChoices(snapshot: Snapshot, current: { workspace_id?: number | string | null; project_id?: number | string | null }): Project[] {
  const workspace = current.workspace_id ?? snapshot.state.workspace_id;
  return snapshot.projects.filter(p => String(p.workspace_id) === String(workspace)
    && (p.active !== false || String(p.id) === String(current.project_id)));
}

export function taskChoices(snapshot: Snapshot, current: { workspace_id?: number | string | null; project_id?: number | string | null; task_id?: number | string | null }): Task[] {
  const workspace = current.workspace_id ?? snapshot.state.workspace_id;
  return snapshot.tasks.filter(task => String(task.workspace_id) === String(workspace)
    && String(task.project_id) === String(current.project_id)
    && (task.active !== false || String(task.id) === String(current.task_id)));
}

export function timerChanges(current: Pick<Entry, "description" | "project_id" | "task_id">, values: { description?: unknown; project?: unknown; task?: unknown }) {
  const changes: { description?: string; project_id?: string | null; task_id?: string | null } = {};
  const description = String(values.description ?? "");
  if (description !== (current.description || "")) changes.description = description;
  if (values.project !== undefined) {
    const project = values.project === "none" ? null : String(values.project);
    const previous = current.project_id == null ? null : String(current.project_id);
    if (project !== previous) changes.project_id = project;
  }
  if (values.task !== undefined) {
    const task = values.task === "none" ? null : String(values.task);
    const previous = current.task_id == null ? null : String(current.task_id);
    if (task !== previous) changes.task_id = task;
  }
  return changes;
}

export function projectCommand(context: Snapshot["project_context"], name: string, color: string, project?: Project) {
  name = name.trim();
  color = color.trim().toLowerCase();
  if (!name || name.length > 200) throw new Error("Enter a project name of 1 to 200 characters.");
  if (!/^#[0-9a-f]{6}$/.test(color)) throw new Error("Enter the color as #RRGGBB, for example #3b82f6.");
  return {
    action: project ? "project-update" : "project-create",
    payload: {
      context: { ...context },
      ...(project ? { project: { id: project.id, workspace_id: project.workspace_id, name: project.name, color: project.color } } : {}),
      name, color,
    },
  };
}

export function taskCommand(context: Snapshot["project_context"], name: string, projectId: string, task?: Task, active = true) {
  name = name.trim();
  if (!name || name.length > 200) throw new Error("Enter a task name of 1 to 200 characters.");
  if (!projectId || projectId === "none") throw new Error("Choose a project for this task.");
  return {
    action: task ? "task-update" : "task-create",
    payload: {
      context: { ...context },
      ...(task ? { task: { ...task } } : { project_id: projectId }),
      name, active,
    },
  };
}
