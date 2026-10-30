import { useEffect, useMemo, useState } from "react";
import { hotkeysCoreFeature, syncDataLoaderFeature } from "@headless-tree/core";
import { useTree } from "@headless-tree/react";
import { Circle, MoreHorizontal, Pencil, Play, Plus } from "lucide-react";
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
import { workGroups, running } from "./model";
import type { Command, Snapshot, Work, WorkGroup } from "./model";

type Node = {
  name: string;
  children?: string[];
  group?: WorkGroup;
  work?: Work;
};

export function WorkTree({
  data,
  query,
  project,
  tasksOnly,
  busy,
  edit,
  rename,
  createTask,
  act,
}: {
  data: Snapshot;
  query: string;
  project: string;
  tasksOnly: boolean;
  busy: boolean;
  edit: (work: Work) => void;
  rename: (work: Work) => void;
  createTask: (project: WorkGroup) => void;
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
      for (const work of group.works)
        result[work.key] = { name: work.label, work };
    }
    return result;
  }, [groups]);
  // Keep browsing choices when searching, polling, or returning from the editor.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const searchScope = query.trim().toLocaleLowerCase();
  const roots = nodes.root.children!;
  const expanded = useMemo(
    () => roots.filter((id) => !collapsed[`${searchScope}:${id}`]),
    [roots, collapsed, searchScope],
  );
  const tree = useTree<Node>({
    rootItemId: "root",
    state: { expandedItems: expanded },
    setExpandedItems: (update) => {
      const next = typeof update === "function" ? update(expanded) : update;
      setCollapsed((previous) => ({
        ...previous,
        ...Object.fromEntries(
          roots.map((id) => [`${searchScope}:${id}`, !next.includes(id)]),
        ),
      }));
    },
    getItemName: (item) => item.getItemData()?.name ?? "",
    isItemFolder: (item) => !!item.getItemData()?.group,
    onPrimaryAction: (item) => {
      const work = item.getItemData()?.work;
      if (work && !busy) edit(work);
    },
    dataLoader: {
      getItem: (id) => nodes[id],
      getChildren: (id) => nodes[id]?.children ?? [],
    },
    features: [syncDataLoaderFeature, hotkeysCoreFeature],
  });
  const structure = JSON.stringify([
    roots,
    groups.map((g) => g.works.map((w) => w.key)),
    expanded,
  ]);
  useEffect(() => {
    tree.rebuildTree();
    if (!tree.getFocusedItem()?.getItemMeta()) tree.getItems()[0]?.setFocused();
  }, [tree, structure]);

  return (
    <Tree
      tree={tree}
      indent={20}
      aria-label="Projects and work"
      className="project-tree"
    >
      {tree.getItems().map((item) => {
        const node = nodes[item.getId()];
        if (!node) return null;
        const { group, work } = node;
        const active = !!work && running(data, work);
        return (
          <TreeItem key={item.getId()} item={item} asChild>
            <div
              className="tree-row"
              data-running={active || undefined}
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
              {group ? (
                <>
                  <TreeItemLabel asChild>
                    <Button
                      variant="ghost"
                      className="project-name"
                      aria-label={group.name}
                    >
                      <Circle
                        aria-hidden="true"
                        className="project-color"
                        fill="currentColor"
                        style={{ color: group.color }}
                      />
                      <span className="truncate">{group.name}</span>
                    </Button>
                  </TreeItemLabel>
                  {group.id != null &&
                    data.task_capability !== "unavailable" && (
                      <Button
                        variant="ghost"
                        size="icon"
                        disabled={busy}
                        aria-label={`New task in ${group.name}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          createTask(group);
                        }}
                      >
                        <Plus />
                      </Button>
                    )}
                </>
              ) : (
                work && (
                  <>
                    <TreeItemLabel asChild>
                      <Button
                        variant="ghost"
                        className="work-name"
                        disabled={busy}
                        aria-label={work.label}
                        onClick={(event) => {
                          event.stopPropagation();
                          edit(work);
                        }}
                      >
                        <span className="work-label">{work.label}</span>
                      </Button>
                    </TreeItemLabel>
                    {active ? (
                      <Badge variant="secondary">Running</Badge>
                    ) : work.task?.active === false ? (
                      <Badge variant="secondary">Archived</Badge>
                    ) : (
                      !work.task && <span className="work-kind">Recent</span>
                    )}
                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={busy}
                      aria-label={`Edit ${work.label}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        edit(work);
                      }}
                    >
                      <Pencil />
                    </Button>
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
                      <Button
                        variant="ghost"
                        size="icon"
                        disabled={busy || active}
                        aria-label={
                          active
                            ? `${work.label} is running`
                            : `Start ${work.label}`
                        }
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
                    )}
                    {work.task && (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            disabled={busy}
                            aria-label={`More actions for ${work.label}`}
                            onClick={(event) => event.stopPropagation()}
                          >
                            <MoreHorizontal />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuGroup>
                            <DropdownMenuItem onSelect={() => rename(work)}>
                              Rename task
                            </DropdownMenuItem>
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
                          </DropdownMenuGroup>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
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
