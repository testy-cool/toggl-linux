import { useEffect, useMemo, useState, type ReactNode } from "react";
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

type Node = {
  name: string;
  children?: string[];
  group?: WorkGroup;
  work?: Work;
  row?: TodayRow;
  parentLabel?: string;
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
  edit: (work: Work) => void;
  rename: (work: Work) => void;
  createTask: (project: WorkGroup) => void;
  saveExpansion: (expandedProjects: (string | number | null)[], expandedTasks: (string | number)[]) => void;
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
      result[id] = {
        name: group.name,
        group,
        children: group.works.map((w) => w.key),
      };
      for (const work of group.works) {
        const rows = today.groups.get(timeKeyForWork(data, work))?.rows ?? [];
        const children = rows.map((row) => `interval:${work.key}:${row.key}`);
        result[work.key] = { name: work.label, work, children };
        for (const row of rows)
          result[`interval:${work.key}:${row.key}`] = {
            name: `${clockRange(row)} · ${row.entry.description || work.label}`,
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
        if (node.group)
          return (
            !preferences.configured ||
            preferences.expanded_projects.some((projectId) =>
              sameId(projectId, node.group!.id),
            )
          );
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
        for (const id of preferences.expanded_projects)
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
        saveExpansion(
          [...projectCandidates.values()].filter((id) =>
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

  return (
    <Tree tree={tree} aria-label="Projects and work" className="project-tree">
      {tree.getItems().map((item) => {
        const node = nodes[item.getId()];
        if (!node) return null;
        const { group, work, row } = node;
        const active = !!work && running(data, work);
        const seconds = work
          ? (today.groups.get(timeKeyForWork(data, work))?.seconds ?? 0)
          : 0;
        return (
          <TreeItem
            key={item.getId()}
            item={item}
            asChild
            className={group ? "mt-2 first:mt-0" : undefined}
          >
            <div
              className="tree-row"
              data-project={!!group || undefined}
              data-running={active || undefined}
              data-archived={work?.task?.active === false || undefined}
              data-interval={!!row || undefined}
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
                      <span className="work-title">
                        <span className="work-label">{work.label}</span>
                        {!tasksOnly &&
                          work.key !== "current" &&
                          work.task?.active !== false && (
                            <span className="work-kind">
                              {work.task ? "Task" : "Recent"}
                            </span>
                          )}
                      </span>
                    </RowLabel>
                    {active ? (
                      <Badge variant="secondary">Running</Badge>
                    ) : work.task?.active === false ? (
                      <Badge variant="secondary">Archived</Badge>
                    ) : null}
                    {(work.task || active) && (
                      <ActionHint
                        text={
                          work.task ? "Rename saved task" : "Edit running timer"
                        }
                      >
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          disabled={busy}
                          aria-label={
                            work.task
                              ? `Rename task ${work.label}`
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
                    {
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
                                Rename task
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
                    }
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
        );
      })}
    </Tree>
  );
}
