import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Frame,
  FramePanel,
  FrameHeader,
  FrameTitle,
  FrameDescription,
  FrameFooter,
} from "@/components/reui/frame";
import {
  contextMatches,
  entryEnd,
  projectName,
  sameId,
  scope,
  timerMatches,
  timerTitle,
} from "./model";
import type { Draft, Snapshot } from "./model";

export function Choice({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  options: { id: string; name: string }[];
  disabled?: boolean;
  onChange(value: string): void;
}) {
  return (
    <Field data-disabled={disabled}>
      <FieldLabel>{label}</FieldLabel>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger aria-label={label} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {options.map((o) => (
              <SelectItem key={o.id} value={o.id}>
                {o.name}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </Field>
  );
}

export function Editor({
  draft: d,
  current,
  busy,
  change,
  cancel,
  save,
  hide,
}: {
  draft: Draft;
  current: Snapshot;
  busy: boolean;
  change(d: Draft): void;
  cancel(): void;
  save(asTask?: boolean): void;
  hide(): void;
}) {
  const metadata = d.mode === "task" || d.mode === "new-task";
  const fullReview = d.mode === "entry" && d.entry?._needs_full_review;
  const staleContext = !contextMatches(d.snapshot, current);
  const staleTimer =
    (d.mode === "current" || d.mode === "new") &&
    !timerMatches(d.snapshot.state, current.state);
  const projects = d.snapshot.projects
    .filter(
      (p) =>
        sameId(p.workspace_id, scope(d)) &&
        (p.active !== false || sameId(p.id, d.project)),
    )
    .map((p) => ({ id: String(p.id), name: p.name }));
  if (d.project !== "none" && !projects.some((p) => p.id === d.project))
    projects.push({ id: d.project, name: "Current project" });
  const tasks = d.snapshot.tasks
    .filter(
      (t) =>
        sameId(t.workspace_id, scope(d)) &&
        sameId(t.project_id, d.project) &&
        (t.active !== false || sameId(t.id, d.task)),
    )
    .map((t) => ({ id: String(t.id), name: t.name }));
  if (d.task !== "none" && !tasks.some((t) => t.id === d.task))
    tasks.push({ id: d.task, name: "Current task" });
  const nameValid =
    !!d.description.trim() && d.description.trim().length <= 200;
  const title = {
    current: "Edit running timer",
    new: "New timer",
    task: "Edit saved task",
    "new-task": "New saved task",
    entry: "Edit time entry",
  }[d.mode];
  const note = staleContext
    ? "Your account or workspace changed. Your draft is kept. Copy it or cancel to reload."
    : staleTimer
      ? "The timer changed elsewhere. Your draft is kept. Cancel to load the current timer."
      : d.mode === "current"
        ? "Save keeps the same time interval."
        : d.mode === "new"
          ? d.snapshot.state.tracking
            ? `Stops “${timerTitle(d.snapshot)}” and starts a new interval with these details.`
            : "Start a new interval with these details."
          : d.mode === "entry"
            ? "Corrects this entry without starting another timer."
            : d.mode === "task"
              ? `This saved task stays in ${projectName(d.snapshot, d.work!.project_id)}.`
              : "Save a reusable task in this project.";
  return (
    <Frame
      variant="ghost"
      dense
      data-testid="editor"
      className="editor-frame min-h-0"
    >
      <FramePanel className="flex min-h-0 flex-col">
        <FrameHeader>
          <FrameTitle>{title}</FrameTitle>
          <FrameDescription>{note}</FrameDescription>
        </FrameHeader>
        <form
          className="flex min-h-0 flex-col"
          onSubmit={(event) => {
            event.preventDefault();
            save();
          }}
        >
          <ScrollArea className="min-h-0 flex-1">
            <FieldGroup className="gap-4 py-3">
              <Field>
                <FieldLabel htmlFor="work-description">
                  {metadata ? "Task name" : "Description"}
                </FieldLabel>
                <Input
                  id="work-description"
                  autoFocus
                  value={d.description}
                  placeholder={
                    metadata ? "Name this task" : "What are you working on?"
                  }
                  maxLength={metadata ? 200 : 10000}
                  disabled={busy}
                  onChange={(e) =>
                    change({ ...d, description: e.target.value })
                  }
                />
              </Field>
              <FieldGroup className="grid gap-4 min-[480px]:grid-cols-2">
                <Choice
                  label="Project"
                  value={d.project}
                  options={[{ id: "none", name: "No project" }, ...projects]}
                  disabled={busy || d.mode === "task"}
                  onChange={(project) =>
                    change({ ...d, project, task: "none" })
                  }
                />
                {!metadata && (
                  <Choice
                    label="Task"
                    value={d.task}
                    options={[{ id: "none", name: "No task" }, ...tasks]}
                    disabled={busy || d.project === "none"}
                    onChange={(task) => change({ ...d, task })}
                  />
                )}
              </FieldGroup>
              {(d.mode === "current" || d.mode === "entry") && (
                <Field>
                  <FieldLabel htmlFor="work-start">Started at</FieldLabel>
                  <Input
                    id="work-start"
                    type="datetime-local"
                    step="1"
                    value={d.start}
                    disabled={busy}
                    required
                    onChange={(e) =>
                      change({ ...d, start: e.target.value, reviewed: false })
                    }
                  />
                </Field>
              )}
              {d.mode === "entry" && entryEnd(d.entry) && (
                <Field>
                  <FieldLabel htmlFor="work-stop">Stopped at</FieldLabel>
                  <Input
                    id="work-stop"
                    type="datetime-local"
                    step="1"
                    value={d.stop}
                    disabled={busy}
                    required
                    onChange={(e) =>
                      change({ ...d, stop: e.target.value, reviewed: false })
                    }
                  />
                </Field>
              )}
              {fullReview && (
                <Field>
                  <FieldDescription>
                    This older pending entry has no original cloud baseline.
                    Saving approves all fields above, including its full start
                    and stop dates and times.
                  </FieldDescription>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={busy || d.reviewed}
                    onClick={() => change({ ...d, reviewed: true })}
                  >
                    {d.reviewed
                      ? "Interval reviewed"
                      : "Approve displayed interval"}
                  </Button>
                </Field>
              )}
              {metadata && !nameValid && (
                <FieldDescription>
                  Enter a task name of 1–200 characters.
                </FieldDescription>
              )}
            </FieldGroup>
          </ScrollArea>
          <FrameFooter className="flex shrink-0 flex-wrap justify-between gap-2">
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={cancel}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={
                busy ||
                staleContext ||
                staleTimer ||
                (fullReview && !d.reviewed) ||
                (metadata && (!nameValid || d.project === "none"))
              }
            >
              {busy
                ? "Saving…"
                : d.mode === "new"
                  ? "Start timer"
                  : d.mode === "new-task"
                    ? "Create task"
                    : "Save changes"}
            </Button>
          </FrameFooter>
        </form>
        {d.mode === "new" && (
          <div className="flex flex-wrap gap-2 pt-3">
            <Button
              variant="ghost"
              size="default"
              disabled={
                busy ||
                staleContext ||
                !nameValid ||
                d.project === "none" ||
                current.task_capability === "unavailable"
              }
              onClick={() => save(true)}
            >
              Save as task
            </Button>
            {d.work && (
              <Button
                variant="ghost"
                size="default"
                disabled={busy || staleContext}
                onClick={hide}
              >
                Hide suggestion
              </Button>
            )}
          </div>
        )}
      </FramePanel>
    </Frame>
  );
}
