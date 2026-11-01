import { useLayoutEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Item, ItemContent, ItemTitle } from "@/components/ui/item";
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
  nextTaskCopyName,
  taskNameConflict,
  timerMatches,
  timerTitle,
} from "./model";
import { DateTimeField } from "./TimeInput";
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
  const copyTask = d.mode === "new-task" && !!d.work?.task;
  const descriptionRef = useRef<HTMLInputElement>(null);
  const wasCopyTask = useRef(copyTask);
  useLayoutEffect(() => {
    const shouldFocus = copyTask && !wasCopyTask.current;
    wasCopyTask.current = copyTask;
    if (shouldFocus) descriptionRef.current?.focus();
  }, [copyTask]);
  const fullReview = d.mode === "entry" && d.entry?._needs_full_review;
  const staleContext = !contextMatches(d.snapshot, current);
  const staleTimer =
    (d.mode === "current" || d.mode === "new") &&
    !timerMatches(d.snapshot.state, current.state);
  const hasCopyDestination =
    d.mode === "task" &&
    d.snapshot.projects.some(
      (project) =>
        sameId(project.workspace_id, scope(d)) &&
        project.active !== false &&
        !sameId(project.id, d.work!.project_id),
    );
  const projects = d.snapshot.projects
    .filter(
      (p) =>
        sameId(p.workspace_id, scope(d)) &&
        (p.active !== false || sameId(p.id, d.project)) &&
        (!copyTask || !sameId(p.id, d.work!.project_id)),
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
  const duplicateName =
    metadata &&
    taskNameConflict(
      d.snapshot,
      d.description,
      scope(d),
      d.mode === "task" ? d.work!.task!.id : undefined,
    );
  const nameError = !metadata
    ? ""
    : !nameValid
      ? "Enter a task name of 1–200 characters."
      : duplicateName
        ? copyTask
          ? `A task with this name already exists in this workspace. Enter a different name, such as “${nextTaskCopyName(d.snapshot, d.work!.task!)}”.`
          : "A task with this name already exists in this workspace. Enter a different name."
        : "";
  const title = copyTask
    ? "Create task in another project"
    : {
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
              ? hasCopyDestination
                ? `Rename this task here, or create a separate task in another project. Its time entries stay in ${projectName(d.snapshot, d.work!.project_id)}.`
                : `Rename this task here. No other active project is available for a copy, and its time entries stay in ${projectName(d.snapshot, d.work!.project_id)}.`
              : copyTask
                ? `Creates a separate task. “${d.work!.task!.name}” and its time entries stay in ${projectName(d.snapshot, d.work!.project_id)}.`
                : "Save a reusable task in this project.";
  return (
    <Frame
      variant="ghost"
      dense
      data-testid="editor"
      className="editor-frame min-h-0"
    >
      <FramePanel className="flex min-h-0 flex-col">
        <FrameHeader className="shrink-0">
          <FrameTitle>{title}</FrameTitle>
          <FrameDescription>{note}</FrameDescription>
        </FrameHeader>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(event) => {
            event.preventDefault();
            save();
          }}
        >
          <ScrollArea type="auto" className="min-h-0 flex-1">
            <FieldGroup className="gap-4 px-4 py-3">
              <Field>
                <FieldLabel htmlFor="work-description">
                  {metadata ? "Task name" : "Description"}
                </FieldLabel>
                <Input
                  ref={descriptionRef}
                  id="work-description"
                  autoFocus
                  value={d.description}
                  aria-invalid={!!nameError || undefined}
                  aria-describedby={nameError ? "work-description-error" : undefined}
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
                {d.mode === "task" ? (
                  <Field className="min-[480px]:col-span-2">
                    <FieldLabel>Project</FieldLabel>
                    <Item size="xs" variant="muted" className="task-project-item">
                      <ItemContent>
                        <ItemTitle>
                          {projectName(d.snapshot, d.work!.project_id)}
                        </ItemTitle>
                      </ItemContent>
                      <Button
                        type="button"
                        variant="ghost"
                        disabled={busy || !hasCopyDestination}
                        onClick={() =>
                          change({
                            ...d,
                            mode: "new-task",
                            description: nextTaskCopyName(
                              d.snapshot,
                              d.work!.task!,
                            ),
                            project: "none",
                            task: "none",
                            reviewed: false,
                          })
                        }
                      >
                        Create in another project
                      </Button>
                    </Item>
                  </Field>
                ) : (
                  <Choice
                    label="Project"
                    value={d.project}
                    options={[
                      {
                        id: "none",
                        name: metadata ? "Choose a project" : "No project",
                      },
                      ...projects,
                    ]}
                    disabled={busy}
                    onChange={(project) =>
                      change({ ...d, project, task: "none" })
                    }
                  />
                )}
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
                <DateTimeField
                  id="work-start"
                  label="Started at"
                  value={d.start}
                  disabled={busy}
                  onChange={(start) => change({ ...d, start, reviewed: false })}
                />
              )}
              {d.mode === "entry" && entryEnd(d.entry) && (
                <DateTimeField
                  id="work-stop"
                  label="Stopped at"
                  value={d.stop}
                  disabled={busy}
                  onChange={(stop) => change({ ...d, stop, reviewed: false })}
                />
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
              {metadata && nameError && (
                <FieldDescription id="work-description-error">
                  {nameError}
                </FieldDescription>
              )}
            </FieldGroup>
          </ScrollArea>
          <FrameFooter className="flex shrink-0 flex-row flex-wrap justify-between gap-2">
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
                (metadata &&
                  (!nameValid || duplicateName || d.project === "none"))
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
          <div className="flex shrink-0 flex-wrap gap-2 px-4 pb-2 pt-3">
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
