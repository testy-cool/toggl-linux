import {
  Fragment,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { hotkeysCoreFeature, syncDataLoaderFeature } from "@headless-tree/core";
import { useTree } from "@headless-tree/react";
import { Circle, MoreHorizontal, Pencil, Play, Plus } from "lucide-react";
import { cn } from "cn";
import { Tree, TreeItem, TreeItemLabel } from "@/components/reui/tree";
import { Badge } from "@/components/reui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TimeEntryRow } from "./TimeEntryRow";
import { ActionHint } from "./ActionHint";
import { clockRange, timeKeyForWork, todayLabel } from "./today";
import type { Today, TodayRow } from "./today";
import { workGroups, running, sameId } from "./model";
import type { Command, Snapshot, Work, WorkGroup, Entry, Id } from "./model";

function isSavedTaskWork(data: Snapshot, work: Work) {
  return (
    !!work.task ||
    (work.task_id != null &&
      data.tasks.some((task) => sameId(task.id, work.task_id)))
  );
}

type Node = {
  name: string;
  projectKey?: string;
  children?: string[];
  group?: WorkGroup;
  work?: Work;
  row?: TodayRow;
  parentLabel?: string;
  section?: "Saved tasks" | "Recent activity";
};

function RowLabel({
  className,
  children,
}: {
  className: string;
  children: ReactNode;
}) {
  return (
    <TreeItemLabel
      className={className}
      onClick={(event) =>
        event.currentTarget.closest<HTMLElement>('[role="treeitem"]')?.focus()
      }
    >
      {children}
    </TreeItemLabel>
  );
}

export function WorkTree({
  data,
  today,
  openEntry,
  retime,
  prepare,
  saveTask,
  query,
  project,
  tasksOnly,
  busy,
  savingTime,
  edit,
  rename,
  createTask,
  saveExpansion,
  act,
}: {
  data: Snapshot;
  today: Today;
  openEntry: (entry: Entry) => void;
  retime: (entry: Entry, field: "start" | "stop", time: string) => void;
  prepare: (work: Work) => void;
  saveTask: (work: Work) => void;
  query: string;
  project: string;
  tasksOnly: boolean;
  busy: boolean;
  savingTime: boolean;
  edit: (work: Work) => void;
  rename: (work: Work) => void;
  createTask: (project: WorkGroup) => void;
  saveExpansion: (
    knownProjects: (string | number | null)[],
    expandedProjects: (string | number | null)[],
    expandedTasks: (string | number)[],
  ) => void;
  act: (command: Command) => void;
}) {
  const groups = useMemo(
    () => workGroups(data, tasksOnly, query, project),
    [data, tasksOnly, query, project],
  );
  const nodes = useMemo(() => {
    const result: Record<string, Node> = {
      root: { name: "Projects", children: [] },
    };
    for (const group of groups) {
      const id = `project:${group.id ?? "none"}`;
      result.root.children!.push(id);
      const saved = group.works.filter((work) =>
        isSavedTaskWork(data, work),
      );
      const recent = group.works.filter((work) => !saved.includes(work));
      const works = [...saved, ...recent];
      const mixed = saved.length > 0 && recent.length > 0;
      result[id] = {
        name: group.name,
        projectKey: id,
        group,
        children: works.map((work) => work.key),
      };
      for (const work of works) {
        const section =
          mixed && work === saved[0]
            ? "Saved tasks"
            : mixed && work === recent[0]
              ? "Recent activity"
              : undefined;
        const rows = today.groups.get(timeKeyForWork(data, work))?.rows ?? [];
        const children = rows.map((row) => `interval:${work.key}:${row.key}`);
        result[work.key] = { name: work.label, projectKey: id, work, children, section };
        for (const row of rows)
          result[`interval:${work.key}:${row.key}`] = {
            name: `${clockRange(row)} · ${row.entry.description || work.label}`,
            projectKey: id,
            row,
            parentLabel: work.label,
          };
      }
    }
    return result;
  }, [groups, today, data]);
  // Search gets temporary browsing choices. The unfiltered tree starts from the
  // account-scoped file and keeps local choices through polling and editors.
  const [expansion, setExpansion] = useState<Record<string, boolean>>({});
  const [focusInterval, setFocusInterval] = useState<string | null>(null);
  const searchScope = query.trim().toLocaleLowerCase();
  const preferenceScope = JSON.stringify(data.project_context);
  const roots = nodes.root.children!;
  const folders = useMemo(
    () =>
      Object.keys(nodes).filter(
        (id) => id !== "root" && !!nodes[id].children?.length,
      ),
    [nodes],
  );
  const preferences = useMemo(
    () =>
      data.work_preferences ?? {
        configured: false,
        known_projects: [],
        expanded_projects: [],
        expanded_tasks: [],
      },
    [data.work_preferences],
  );
  const persistentGroups = useMemo(
    () => workGroups(data, false, "", "all"),
    [data],
  );
  const expansionKey = (id: string) =>
    `${preferenceScope}:${searchScope}:${id}`;
  const expanded = useMemo(
    () =>
      folders.filter((id) => {
        const saved = expansion[`${preferenceScope}:${searchScope}:${id}`];
        if (saved !== undefined) return saved;
        const node = nodes[id];
        if (searchScope) return !!node.group;
        if (node.group) {
          const known = preferences.known_projects.some((projectId) =>
            sameId(projectId, node.group!.id),
          );
          return (
            !preferences.configured ||
            !known ||
            preferences.expanded_projects.some((projectId) =>
              sameId(projectId, node.group!.id),
            )
          );
        }
        if (node.work?.task)
          return (
            preferences.configured &&
            preferences.expanded_tasks.some((taskId) =>
              sameId(taskId, node.work!.task!.id),
            )
          );
        return false;
      }),
    [
      folders,
      nodes,
      expansion,
      preferenceScope,
      preferences,
      searchScope,
    ],
  );
  const tree = useTree<Node>({
    rootItemId: "root",
    state: { expandedItems: expanded },
    setExpandedItems: (update) => {
      const next = typeof update === "function" ? update(expanded) : update;
      setExpansion((previous) => ({
        ...previous,
        ...Object.fromEntries(
          folders.map((id) => [expansionKey(id), next.includes(id)]),
        ),
      }));
      if (!searchScope) {
        const visible = new Set(folders);
        const projectCandidates = new Map<string, Id | null>();
        for (const group of persistentGroups)
          if (group.works.length)
            projectCandidates.set(String(group.id ?? "none"), group.id);
        for (const id of [
          ...preferences.known_projects,
          ...preferences.expanded_projects,
        ])
          projectCandidates.set(String(id ?? "none"), id);
        const taskCandidates = new Map<string, Id>();
        for (const task of data.tasks)
          if (sameId(task.workspace_id, data.state.workspace_id))
            taskCandidates.set(String(task.id), task.id);
        for (const id of preferences.expanded_tasks)
          taskCandidates.set(String(id), id);
        const chosen = (nodeId: string, fromFile: boolean) =>
          visible.has(nodeId)
            ? next.includes(nodeId)
            : (expansion[`${preferenceScope}::${nodeId}`] ?? fromFile);
        const knownProjects = [...projectCandidates.values()].filter((id) => {
          const nodeId = `project:${id ?? "none"}`;
          return (
            visible.has(nodeId) ||
            expansion[`${preferenceScope}::${nodeId}`] !== undefined ||
            preferences.known_projects.some((saved) => sameId(saved, id))
          );
        });
        saveExpansion(
          knownProjects,
          knownProjects.filter((id) =>
            chosen(
              `project:${id ?? "none"}`,
              preferences.expanded_projects.some((saved) => sameId(saved, id)),
            ),
          ),
          [...taskCandidates.values()].filter((id) =>
            chosen(
              `task:${id}`,
              preferences.expanded_tasks.some((saved) => sameId(saved, id)),
            ),
          ),
        );
      }
    },
    getItemName: (item) => item.getItemData()?.name ?? "",
    isItemFolder: (item) => !!item.getItemData()?.children?.length,
    dataLoader: {
      getItem: (id) => nodes[id],
      getChildren: (id) => nodes[id]?.children ?? [],
    },
    features: [syncDataLoaderFeature, hotkeysCoreFeature],
  });
  const structure = JSON.stringify([
    roots,
    Object.entries(nodes).map(([id, node]) => [id, node.children]),
    expanded,
  ]);
  useEffect(() => {
    tree.rebuildTree();
    if (!tree.getItems().some((item) => item.isFocused()))
      tree.getItems()[0]?.setFocused();
  }, [tree, structure]);

  const items = tree.getItems();
  const projectTones = new Map<string, number | "neutral">(
    persistentGroups.map((group, index) => [
      `project:${group.id ?? "none"}`,
      group.id == null ? "neutral" : index % 5,
    ] as const),
  );

  return (
    <Tree tree={tree} aria-label="Projects and work" className="project-tree">
      {items.map((item, index) => {
        const node = nodes[item.getId()];
        if (!node) return null;
        const { group, work, row } = node;
        const active = !!work && running(data, work);
        const seconds = work
          ? (today.groups.get(timeKeyForWork(data, work))?.seconds ?? 0)
          : 0;
        const savedTask = !!work && isSavedTaskWork(data, work);
        const hasEntries = !!work && !!node.children?.length;
        const projectTone = projectTones.get(node.projectKey ?? "") ?? "neutral";
        const nextItem = items[index + 1];
        const projectEnd = !nextItem || nodes[nextItem.getId()]?.projectKey !== node.projectKey;
        return (
          <Fragment key={item.getId()}>
            {node.section && (
              <div
                aria-hidden="true"
                role="presentation"
                className="work-section-heading project-band"
                data-section={node.section}
                data-project-tone={projectTone}
                data-project-key={node.projectKey}
              >
                {node.section}
              </div>
            )}
            <TreeItem
              item={item}
              asChild
              className={group ? "mt-2 first:mt-0" : undefined}
            >
              <div
                className="tree-row project-band"
                data-project-tone={projectTone}
                data-project-key={node.projectKey}
                data-project-band-end={projectEnd || undefined}
                data-project={!!group || undefined}
                data-work={!!work || undefined}
                data-work-kind={
                  work ? (savedTask ? "task" : "activity") : undefined
                }
                data-running={active || undefined}
                data-archived={work?.task?.active === false || undefined}
                data-interval={!!row || undefined}
                aria-description={
                  work
                    ? savedTask
                      ? "Saved task"
                      : "Recent activity"
                    : undefined
                }
                onFocus={(event) => {
                  if (event.target === event.currentTarget) item.setFocused();
                }}
                onKeyDown={(event) => {
                  if (
                    event.target === event.currentTarget &&
                    (event.key === "Enter" || event.key === " ")
                  ) {
                    event.preventDefault();
                    event.currentTarget.click();
                  }
                }}
              >
                {row ? (
                  <TimeEntryRow
                    row={row}
                    parentLabel={node.parentLabel}
                    busy={busy}
                    savingTime={savingTime}
                    focusRange={focusInterval === item.getId()}
                    onRangeFocused={() => setFocusInterval(null)}
                    edit={openEntry}
                    retime={retime}
                  />
                ) : group ? (
                  <div className="project-heading">
                    <RowLabel className="project-name font-semibold">
                      <Circle
                        aria-hidden="true"
                        className="project-color"
                        fill="currentColor"
                        style={{ color: group.color }}
                      />
                      <span className="work-label">{group.name}</span>
                    </RowLabel>
                    {group.id != null &&
                      data.task_capability !== "unavailable" && (
                        <ActionHint text={`New task in ${group.name}`}>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            disabled={busy}
                            aria-label={`New task in ${group.name}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              createTask(group);
                            }}
                          >
                            <Plus />
                          </Button>
                        </ActionHint>
                      )}
                  </div>
                ) : (
                  work && (
                    <>
                      <RowLabel className="work-name">
                        <span className="work-label">{work.label}</span>
                      </RowLabel>
                      <div className="work-controls">
                        {active ? (
                          <Badge variant="secondary">Running</Badge>
                        ) : work.task?.active === false ? (
                          <Badge variant="secondary">Archived</Badge>
                        ) : null}
                        {hasEntries && !item.isExpanded() && (
                          <Button
                            variant="ghost"
                            size="xs"
                            className="edit-entries"
                            disabled={busy}
                            onClick={(event) => {
                              event.stopPropagation();
                              setFocusInterval(node.children![0]);
                              item.expand();
                              item.setFocused();
                            }}
                          >
                            Edit entries
                          </Button>
                        )}
                        {(work.task || active) && (
                          <ActionHint
                            text={
                              work.task ? "Edit saved task" : "Edit running timer"
                            }
                          >
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              disabled={busy}
                              aria-label={
                                work.task
                                  ? `Edit task ${work.label}`
                                  : `Edit timer ${work.label}`
                              }
                              onClick={(event) => {
                                event.stopPropagation();
                                if (work.task) rename(work);
                                else edit(work);
                              }}
                            >
                              <Pencil />
                            </Button>
                          </ActionHint>
                        )}
                        {work.task?.active === false ? (
                          <Button
                            variant="ghost"
                            size="xs"
                            disabled={busy}
                            onClick={(event) => {
                              event.stopPropagation();
                              act({
                                action: "task-update",
                                payload: {
                                  context: data.project_context,
                                  task: work.task,
                                  name: work.task!.name,
                                  active: true,
                                },
                              });
                            }}
                          >
                            Restore
                          </Button>
                        ) : (
                          !active && (
                            <ActionHint
                              text={
                                data.state.tracking
                                  ? "Start this work and stop the current timer"
                                  : "Start this work"
                              }
                            >
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                disabled={busy}
                                aria-label={`Start ${work.label}`}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  act({
                                    action: "switch",
                                    payload: {
                                      changes: {
                                        project_id: work.project_id,
                                        task_id: work.task_id,
                                        description: work.description,
                                      },
                                      expected: data.state,
                                      context: data.project_context,
                                    },
                                  });
                                }}
                              >
                                <Play />
                              </Button>
                            </ActionHint>
                          )
                        )}
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              disabled={busy}
                              aria-label={`More actions for ${work.label}`}
                              onClick={(event) => event.stopPropagation()}
                            >
                              <MoreHorizontal />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent
                            align="end"
                            onClick={(event) => event.stopPropagation()}
                          >
                            <DropdownMenuGroup>
                              {!work.task && (
                                <DropdownMenuItem onSelect={() => prepare(work)}>
                                  New timer with these details
                                </DropdownMenuItem>
                              )}
                              {!work.task && (
                                <DropdownMenuItem
                                  disabled={
                                    data.task_capability === "unavailable" ||
                                    work.project_id == null
                                  }
                                  onSelect={() => saveTask(work)}
                                >
                                  Save as task
                                </DropdownMenuItem>
                              )}
                              {!work.task && (
                                <DropdownMenuItem
                                  onSelect={() =>
                                    act({
                                      action: "hide-activity",
                                      payload: {
                                        project_id: work.project_id,
                                        description: work.description,
                                        context: data.project_context,
                                      },
                                    })
                                  }
                                >
                                  Hide suggestion
                                </DropdownMenuItem>
                              )}
                              {work.task && (
                                <DropdownMenuItem onSelect={() => rename(work)}>
                                  Edit task
                                </DropdownMenuItem>
                              )}
                              {work.task && (
                                <DropdownMenuItem
                                  onSelect={() =>
                                    act({
                                      action: "task-update",
                                      payload: {
                                        context: data.project_context,
                                        task: work.task,
                                        name: work.task!.name,
                                        active: work.task!.active === false,
                                      },
                                    })
                                  }
                                >
                                  {work.task.active === false
                                    ? "Restore task"
                                    : "Archive task"}
                                </DropdownMenuItem>
                              )}
                            </DropdownMenuGroup>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                      <span
                        className={cn(
                          "work-time",
                          seconds === 0 && "text-muted-foreground",
                        )}
                        aria-label={`Today for ${work.label}`}
                      >
                        {todayLabel(today, seconds)}
                      </span>
                    </>
                  )
                )}
              </div>
            </TreeItem>
          </Fragment>
        );
      })}
    </Tree>
  );
}
