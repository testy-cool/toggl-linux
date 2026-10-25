import { useEffect, useState } from "react";
import { Action, ActionPanel, Form, Icon, List, Toast, getPreferenceValues, showToast, useNavigation } from "@vicinae/api";
import { control, elapsed, Entry, Snapshot, queueCommands, projectChoices, timerChanges } from "./control";

function TimerForm({ snapshot, entry, start, newTask, submit }: { snapshot: Snapshot; entry?: Entry; start?: boolean; newTask?: boolean; submit: (action: string, payload: object) => Promise<boolean> }) {
  const { pop } = useNavigation();
  const current = entry || snapshot.state;
  const [saving, setSaving] = useState(false);
  const projects = projectChoices(snapshot, current);
  async function save(values: Form.Values) {
    if (saving) return;
    setSaving(true);
    const changes = timerChanges(current, values);
    const action = newTask ? "switch" : start ? "start" : entry ? "edit-entry" : "edit-current";
    if (await submit(action, entry ? { entry, changes } : { changes, expected: snapshot.state })) pop();
    setSaving(false);
  }
  return <Form isLoading={saving} navigationTitle={newTask ? "Start New Task" : start ? "Start Timer" : "Edit Timer"} actions={<ActionPanel><Action.SubmitForm title={newTask ? "Start New Task" : start ? "Start Timer" : "Save Changes"} onSubmit={save} /></ActionPanel>}>
    <Form.TextField id="description" title="Description" defaultValue={current.description || ""} />
    <Form.Dropdown id="project" title="Project" defaultValue={current.project_id == null ? "none" : String(current.project_id)}>
      <Form.Dropdown.Item value="none" title="No project" />
      {current.project_id != null && !projects.some(p => String(p.id) === String(current.project_id)) && <Form.Dropdown.Item value={String(current.project_id)} title="Current project (not cached)" />}
      {projects.map(p => <Form.Dropdown.Item key={String(p.id)} value={String(p.id)} title={p.name} icon={{source: Icon.CircleFilled, tintColor: p.color || undefined}} />)}
    </Form.Dropdown>
  </Form>;
}

export default function Timer() {
  const prefs = getPreferenceValues<{ cliPath?: string }>();
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [send] = useState(() => queueCommands((action, payload) => control(prefs.cliPath, action, payload)));
  async function run(action: string, payload: object = {}): Promise<boolean> {
    try {
      const result = await send(action, payload);
      if (!result) return false;
      setSnapshot(result); setError("");
      if (!["snapshot", "refresh"].includes(action)) await showToast({ style: Toast.Style.Success, title: "Timer updated" });
      return true;
    } catch (e) {
      const message = e instanceof Error ? e.message : "Timer command failed";
      setError(message);
      if (action !== "snapshot") await showToast({ style: Toast.Style.Failure, title: "Could not update timer", message });
      return false;
    } finally { setLoading(false); }
  }
  useEffect(() => {
    void (async () => { if (await run("snapshot")) await run("refresh"); })();
    const interval = setInterval(() => { void run("snapshot"); }, 2000);
    return () => clearInterval(interval);
  }, []);
  const state = snapshot?.state;
  const project = snapshot?.projects.find(p => String(p.id) === String(state?.project_id) && String(p.workspace_id) === String(state?.workspace_id));
  const refresh = <Action title="Refresh" icon={Icon.ArrowClockwise} onAction={() => run("refresh")} shortcut={{modifiers:["ctrl"], key:"r"}} />;
  return <List isLoading={loading} navigationTitle="Toggl Tray" searchBarPlaceholder="Search today's entries…">
    {error && <List.Item title="Connection problem" subtitle={error} icon={Icon.Exclamationmark} actions={<ActionPanel>{refresh}</ActionPanel>} />}
    {!snapshot && !loading && <List.EmptyView title="Launch Toggl Tray to connect" description="Install the CLI launcher, launch the tray app, and check the extension's CLI path preference." icon={Icon.Stopwatch} />}
    {snapshot && state && <>
      <List.Section title="Current timer">
        <List.Item title={state.description || "No description"} subtitle={state.tracking ? project?.name || "No project" : "Stopped"}
          icon={{source: Icon.Stopwatch, tintColor: state.tracking ? project?.color || undefined : undefined}}
          accessories={[{text: state.tracking ? elapsed(state.start_time) : "Stopped"}]}
          actions={<ActionPanel>
            {state.tracking ? <Action title="Stop Timer" icon={Icon.Stop} onAction={() => run("stop", { expected: snapshot.state })} /> : <Action.Push title="Start Timer" icon={Icon.Play} target={<TimerForm snapshot={snapshot} start submit={run} />} />}
            {state.tracking && <Action.Push title="Start New Task" icon={Icon.Play} target={<TimerForm snapshot={snapshot} newTask submit={run} />} />}
            <Action.Push title="Edit Description and Project" icon={Icon.Pencil} target={<TimerForm snapshot={snapshot} submit={run} />} />
            {snapshot.pending_count > 0 && <Action title="Sync Pending Entries" icon={Icon.ArrowClockwise} onAction={() => run("sync")} />}
            {refresh}
          </ActionPanel>} />
      </List.Section>
      {snapshot.warning && <List.Item title="Showing saved entries" subtitle={snapshot.warning} icon={Icon.Info01} actions={<ActionPanel>{refresh}</ActionPanel>} />}
      <List.Section title="Today" subtitle={snapshot.pending_count ? `${snapshot.pending_count} pending actions` : "Synced"}>
        {snapshot.entries.filter(e => !state.tracking || String(e.id) !== String(state.entry_id)).map(e => <List.Item key={String(e.id)} title={e.description || "No description"}
          subtitle={e._offline ? "Pending sync" : new Date(e.start).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"})}
          icon={e._offline ? Icon.Clock : Icon.CheckCircle}
          accessories={[{text: elapsed(e.start, e.stop)}]}
          actions={<ActionPanel>
            {e.stop && <Action title="Start Again" icon={Icon.Play} onAction={() => run("resume", { entry: e, expected: snapshot.state })} />}
            <Action.Push title="Edit Description and Project" icon={Icon.Pencil} target={<TimerForm snapshot={snapshot} entry={e} submit={run} />} />
            {refresh}
          </ActionPanel>} />)}
      </List.Section>
    </>}
  </List>;
}
