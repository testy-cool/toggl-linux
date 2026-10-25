import { useEffect, useState } from "react";
import { Action, ActionPanel, Form, Icon, List, Toast, getPreferenceValues, showToast, useNavigation } from "@vicinae/api";
import { control, elapsed, Entry, Snapshot, queueCommands } from "./control";

function TimerForm({ snapshot, entry, start, submit }: { snapshot: Snapshot; entry?: Entry; start?: boolean; submit: (action: string, payload: object) => Promise<boolean> }) {
  const { pop } = useNavigation();
  const current = entry || snapshot.state;
  const [saving, setSaving] = useState(false);
  async function save(values: Form.Values) {
    if (saving) return;
    setSaving(true);
    const changes = { description: String(values.description || ""), project_id: values.project === "none" ? null : String(values.project) };
    const action = start ? "start" : entry ? "edit-entry" : "edit-current";
    if (await submit(action, entry ? { entry, changes } : start ? { changes } : { changes, expected: snapshot.state })) pop();
    setSaving(false);
  }
  return <Form isLoading={saving} navigationTitle={start ? "Start Timer" : "Edit Timer"} actions={<ActionPanel><Action.SubmitForm title={start ? "Start Timer" : "Save Changes"} onSubmit={save} /></ActionPanel>}>
    <Form.TextField id="description" title="Description" defaultValue={current.description || ""} />
    <Form.Dropdown id="project" title="Project" defaultValue={current.project_id == null ? "none" : String(current.project_id)}>
      <Form.Dropdown.Item value="none" title="No project" />
      {current.project_id != null && !snapshot.projects.some(p => String(p.id) === String(current.project_id)) && <Form.Dropdown.Item value={String(current.project_id)} title="Current project (not cached)" />}
      {snapshot.projects.filter(p => (p.active !== false || String(p.id) === String(current.project_id)) && String(p.workspace_id) === String(snapshot.state.workspace_id)).map(p => <Form.Dropdown.Item key={String(p.id)} value={String(p.id)} title={p.name} icon={{source: Icon.CircleFilled, tintColor: p.color || undefined}} />)}
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
            {state.tracking ? <Action title="Stop Timer" icon={Icon.Stop} onAction={() => run("stop")} /> : <Action.Push title="Start Timer" icon={Icon.Play} target={<TimerForm snapshot={snapshot} start submit={run} />} />}
            <Action.Push title="Edit Description and Project" icon={Icon.Pencil} target={<TimerForm snapshot={snapshot} submit={run} />} />
            {refresh}
          </ActionPanel>} />
      </List.Section>
      {snapshot.warning && <List.Item title="Showing saved entries" subtitle={snapshot.warning} icon={Icon.Info01} actions={<ActionPanel>{refresh}</ActionPanel>} />}
      <List.Section title="Today" subtitle={snapshot.pending_count ? `${snapshot.pending_count} pending actions` : "Synced"}>
        {snapshot.entries.filter(e => !state.tracking || String(e.id) !== String(state.entry_id)).map(e => <List.Item key={String(e.id)} title={e.description || "No description"}
          subtitle={e._offline ? "Pending sync" : new Date(e.start).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"})}
          icon={e._offline ? Icon.Clock : Icon.CheckCircle}
          accessories={[{text: elapsed(e.start, e.stop)}]}
          actions={<ActionPanel><Action.Push title="Edit Description and Project" icon={Icon.Pencil} target={<TimerForm snapshot={snapshot} entry={e} submit={run} />} />{refresh}</ActionPanel>} />)}
      </List.Section>
    </>}
  </List>;
}
