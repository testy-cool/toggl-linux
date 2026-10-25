import { useEffect, useRef, useState } from "react";
import { Action, ActionPanel, Form, Icon, List, Toast, getPreferenceValues, showToast, useNavigation } from "@vicinae/api";
import { control, elapsed, Entry, Project, Snapshot, queueCommands, projectChoices, timerChanges, projectCommand } from "./control";

const PROJECT_COLORS = [
  { name: "Blue", color: "#3b82f6" },
  { name: "Green", color: "#22c55e" },
  { name: "Purple", color: "#8b5cf6" },
  { name: "Orange", color: "#f97316" },
  { name: "Red", color: "#ef4444" },
  { name: "Teal", color: "#14b8a6" },
  { name: "Gray", color: "#6b7280" },
];

function ProjectForm({ snapshot, project, submit }: { snapshot: Snapshot; project?: Project; submit: (action: string, payload: object) => Promise<boolean> }) {
  const { pop } = useNavigation();
  const [context] = useState(() => ({ ...snapshot.project_context }));
  const [original] = useState(() => project ? { ...project } : undefined);
  const [name, setName] = useState(original?.name || "");
  const [customColor, setCustomColor] = useState((original?.color || "#3b82f6").toLowerCase());
  const [colorChoice, setColorChoice] = useState(() => PROJECT_COLORS.some(p => p.color === customColor) ? customColor : "custom");
  const color = colorChoice === "custom" ? customColor : colorChoice;
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  async function save() {
    if (inFlight.current) return;
    let command;
    try { command = projectCommand(context, name, color, original); }
    catch (e) { setError(e instanceof Error ? e.message : "Check the project fields."); return; }
    inFlight.current = true;
    setSaving(true); setError("");
    try {
      if (await submit(command.action, command.payload)) pop();
      else setError("Project could not be saved. Your changes are kept.");
    } finally { inFlight.current = false; setSaving(false); }
  }
  return <Form isLoading={saving} navigationTitle={original ? "Edit Selected Project" : "New Project"}
    actions={!saving && <ActionPanel><Action.SubmitForm title={original ? "Save Project" : "Create Project"} onSubmit={save} /></ActionPanel>}>
    <Form.Description text={original ? "Update this project's name and saved color." : "Create a project, then select it in the timer's Project field. Your current timer keeps running as it is."} />
    {saving ? <>
      <Form.Description title="Name" text={name} />
      <Form.Description title="Color" text={PROJECT_COLORS.find(p => p.color === colorChoice)?.name || customColor} />
      <Form.Description text="Saving project…" />
    </> : <>
      <Form.TextField id="name" title="Name" value={name} onChange={setName} autoFocus />
      <Form.Dropdown id="color" title="Color" value={colorChoice} onChange={setColorChoice} filtering={false}>
        {PROJECT_COLORS.map(p => <Form.Dropdown.Item key={p.color} value={p.color} title={p.name} icon={{source: Icon.CircleFilled, tintColor: p.color}} />)}
        <Form.Dropdown.Item value="custom" title="Custom" />
      </Form.Dropdown>
      {colorChoice === "custom" && <Form.TextField id="customColor" title="Custom color" value={customColor} onChange={setCustomColor} info="Use #RRGGBB, for example #3b82f6." />}
    </>}
    {error && <Form.Description title="Could not save project" text={error} />}
  </Form>;
}

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
    const projectAction = action === "project-create" || action === "project-update";
    try {
      const result = await send(action, payload);
      if (!result) return false;
      setSnapshot(result); setError("");
      if (!["snapshot", "refresh"].includes(action)) await showToast({ style: Toast.Style.Success, title: projectAction ? action === "project-create" ? "Project created" : "Project saved" : "Timer updated" });
      return true;
    } catch (e) {
      const message = e instanceof Error ? e.message : "Timer command failed";
      setError(message);
      if (action !== "snapshot") await showToast({ style: Toast.Style.Failure, title: projectAction ? "Could not save project" : "Could not update timer", message });
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
      <List.Section title="Projects">
        <List.Item title="New Project" subtitle="Create a project without changing the timer" icon={Icon.Plus}
          actions={<ActionPanel><Action.Push title="New Project" icon={Icon.Plus} target={<ProjectForm snapshot={snapshot} submit={run} />} /></ActionPanel>} />
        {project && <List.Item title="Edit Selected Project" subtitle={project.name} icon={{source: Icon.CircleFilled, tintColor: project.color || undefined}}
          actions={<ActionPanel><Action.Push title="Edit Selected Project" icon={Icon.Pencil} target={<ProjectForm snapshot={snapshot} project={project} submit={run} />} /></ActionPanel>} />}
        <List.Item title="Manage Projects" subtitle={snapshot.projects_url ? "Open all projects in your account" : "Connect your account in Toggl Tray, then refresh"} icon={Icon.Link}
          actions={<ActionPanel>{snapshot.projects_url ? <Action.OpenInBrowser title="Manage Projects" url={snapshot.projects_url} /> : refresh}</ActionPanel>} />
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
