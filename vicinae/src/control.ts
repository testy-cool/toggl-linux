import { execFile } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";

export interface Project { id: number | string; workspace_id: number | string; name: string; color: string | null; active?: boolean }
export interface Entry { id: number | string; description: string; project_id?: number | string | null; start: string; stop?: string | null; duration: number; _offline?: boolean; [key: string]: unknown }
export interface Snapshot {
  state: { tracking: boolean; description: string; project_id: number | string | null; workspace_id: number | string | null; start_time: string | null; entry_id: number | string | null };
  projects: Project[]; entries: Entry[]; pending_count: number; warning: string;
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
