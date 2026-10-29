import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "cn";
import {
  Check,
  Clock3,
  MoreHorizontal,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Square,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription, AlertTitle } from "@/components/reui/alert";
import {
  Frame,
  FrameHeader,
  FrameTitle,
  FramePanel,
} from "@/components/reui/frame";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Editor, Choice } from "./Editor";
import { host, request } from "./api";
import {
  commandFor,
  afterOwnStop,
  draftFor,
  elapsed,
  projectName,
  running,
  sameId,
  taskName,
  timerTitle,
  workItems,
} from "./model";
import type { Command, Draft, Entry, Snapshot, Work } from "./model";

export default function WorkApp() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [query, setQuery] = useState("");
  const [project, setProject] = useState("all");
  const [tab, setTab] = useState("work");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now);
  const epoch = useRef(0);
  const writing = useRef(false);
  const reading = useRef(false);
  const mounted = useRef(true);
  const todayLoaded = useRef(false);

  const poll = useCallback(async () => {
    if (writing.current || reading.current) return;
    reading.current = true;
    const version = epoch.current;
    try {
      const snapshot = await request("work-snapshot");
      if (mounted.current && version === epoch.current && !writing.current)
        setData(snapshot);
    } catch (failure) {
      if (mounted.current && version === epoch.current)
        setError(String(failure instanceof Error ? failure.message : failure));
    } finally {
      reading.current = false;
    }
  }, []);
  const act = useCallback(
    async (command: Command, closeDraft = false) => {
      if (writing.current) return;
      if (
        command.action === "edit-current" &&
        Object.keys(command.payload.changes as object).length === 0
      ) {
        setDraft(null);
        return;
      }
      writing.current = true;
      epoch.current += 1;
      setBusy(true);
      setError("");
      try {
        const result = await request(command.action, command.payload);
        if (mounted.current) {
          setData((previous) =>
            previous ? { ...previous, ...result } : result,
          );
          if (command.action === "stop")
            setDraft((previous) =>
              previous ? afterOwnStop(previous, result) : null,
            );
          if (closeDraft) setDraft(null);
        }
      } catch (failure) {
        setError(String(failure instanceof Error ? failure.message : failure));
      } finally {
        writing.current = false;
        epoch.current += 1;
        setBusy(false);
        void poll();
      }
    },
    [poll],
  );
  const changeTab = useCallback(
    (value: string) => {
      setTab(value);
      if (value === "today" && !todayLoaded.current && !writing.current) {
        todayLoaded.current = true;
        void act({ action: "refresh", payload: {} });
      }
    },
    [act],
  );
  useEffect(() => {
    mounted.current = true;
    let stopped = false;
    async function start() {
      try {
        const bridge = await host();
        if (stopped) return;
        const mode = await bridge.ready();
        const snapshot = await request("work-snapshot");
        if (stopped) return;
        setData(snapshot);
        if (mode.mode === "timer")
          setDraft(
            draftFor(snapshot, snapshot.state.tracking ? "current" : "new"),
          );
        else if (mode.mode === "today") changeTab("today");
      } catch (failure) {
        if (!stopped)
          setError(
            String(failure instanceof Error ? failure.message : failure),
          );
      }
    }
    void start();
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    const refresh = window.setInterval(() => void poll(), 2500);
    return () => {
      stopped = true;
      mounted.current = false;
      clearInterval(timer);
      clearInterval(refresh);
    };
  }, [poll, changeTab]);
  useEffect(() => {
    const open = () => {
      if (data)
        setDraft(
          (previous) =>
            previous ?? draftFor(data, data.state.tracking ? "current" : "new"),
        );
    };
    window.addEventListener("toggl-edit-current", open);
    const today = () => changeTab("today");
    window.addEventListener("toggl-open-today", today);
    const key = (event: KeyboardEvent) => {
      if (
        event.key === "Escape" &&
        !writing.current &&
        !document.querySelector('[role="listbox"], [role="menu"]')
      ) {
        if (draft) setDraft(null);
        else void host().then((bridge) => bridge.close());
      }
    };
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("toggl-edit-current", open);
      window.removeEventListener("toggl-open-today", today);
      window.removeEventListener("keydown", key);
    };
  }, [data, draft, changeTab]);
  useEffect(() => {
    if (tab === "today" && !busy && !todayLoaded.current) changeTab("today");
  }, [tab, busy, changeTab]);

  function openWork(work: Work) {
    if (!data || busy) return;
    setError("");
    setDraft(
      draftFor(
        data,
        running(data, work) ? "current" : work.task ? "task" : "new",
        work,
      ),
    );
  }
  function openEntry(entry: Entry) {
    if (!data || busy) return;
    setDraft(
      draftFor(
        data,
        data.state.tracking && sameId(entry.id, data.state.entry_id)
          ? "current"
          : "entry",
        undefined,
        entry,
      ),
    );
  }
  function saveDraft(asTask = false) {
    if (!draft) return;
    try {
      void act(commandFor(draft, asTask), true);
    } catch (failure) {
      setError(String(failure));
    }
  }
  const projects =
    data?.projects.filter(
      (p) =>
        sameId(p.workspace_id, data.state.workspace_id) && p.active !== false,
    ) ?? [];
  const needle = query.trim().toLocaleLowerCase();
  const works = data
    ? workItems(data, tab === "tasks").filter(
        (w) =>
          (tab !== "tasks" || !!w.task) &&
          (project === "all" || sameId(w.project_id, project)) &&
          `${w.label} ${projectName(data, w.project_id)}`
            .toLocaleLowerCase()
            .includes(needle),
      )
    : [];
  const entries =
    data?.entries.filter(
      (e) =>
        (project === "all" || sameId(e.project_id, project)) &&
        `${e.description} ${projectName(data, e.project_id)}`
          .toLocaleLowerCase()
          .includes(needle),
    ) ?? [];

  return (
    <TooltipProvider>
      <main className="app-shell" data-testid="work-app">
        <header className="app-header">
          <div className="pywebview-drag-region flex min-w-0 flex-1 items-center gap-3">
            <Clock3 aria-hidden="true" className="app-mark" />
            <span className="font-semibold">Toggl</span>
            <span className="min-w-0 truncate text-sm text-muted-foreground">
              {draft && data?.state.tracking
                ? `${projectName(data, data.state.project_id)} · ${elapsed(data.state, now)}`
                : "Work timer"}
            </span>
          </div>
          {draft && data?.state.tracking && (
            <Button
              variant="destructive"
              disabled={busy}
              aria-label="Stop timer and keep draft"
              onClick={() =>
                void act({
                  action: "stop",
                  payload: {
                    expected: data.state,
                    context: data.project_context,
                  },
                })
              }
            >
              Stop
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close window"
            onClick={() => void host().then((bridge) => bridge.close())}
          >
            <X />
          </Button>
        </header>
        <div className="app-content">
          {!data && !error && (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-60 w-full" />
            </div>
          )}
          {error && (
            <Alert variant="destructive">
              <AlertTitle>Couldn’t complete that action</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
              <Button
                variant="ghost"
                size="default"
                onClick={() => {
                  setError("");
                  void poll();
                }}
              >
                Retry connection
              </Button>
            </Alert>
          )}
          {data && (
            <>
              {data.warning && (
                <Alert variant="warning">
                  <AlertTitle>Some work needs attention</AlertTitle>
                  <AlertDescription>{data.warning}</AlertDescription>
                </Alert>
              )}
              {!draft && (
                <Frame variant="ghost" dense>
                  <FrameHeader>
                    <FrameTitle>
                      <span className="text-muted-foreground">
                        {data.state.tracking
                          ? "Running now"
                          : "Ready when you are"}
                      </span>
                    </FrameTitle>
                  </FrameHeader>
                  <FramePanel>
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <div className="elapsed" aria-label="Elapsed time">
                          {elapsed(data.state, now)}
                        </div>
                        <h1 className="work-title">
                          {data.state.tracking
                            ? timerTitle(data)
                            : "What’s next?"}
                        </h1>
                        <p className="text-sm text-muted-foreground">
                          {data.state.tracking
                            ? `${projectName(data, data.state.project_id)}${data.state.task_id != null && data.state.description ? ` · ${taskName(data, data.state.task_id)}` : ""}`
                            : "Choose recent work or start a new timer."}
                        </p>
                      </div>
                      <div className="flex shrink-0 gap-2">
                        {data.state.tracking ? (
                          <>
                            <Button
                              variant="secondary"
                              size="icon"
                              disabled={busy || !!draft}
                              aria-label="Edit running timer"
                              onClick={() =>
                                setDraft(draftFor(data, "current"))
                              }
                            >
                              <Pencil />
                            </Button>
                            <Button
                              variant="destructive"
                              disabled={busy || !!draft}
                              onClick={() =>
                                void act({
                                  action: "stop",
                                  payload: {
                                    expected: data.state,
                                    context: data.project_context,
                                  },
                                })
                              }
                            >
                              <Square data-icon="inline-start" />
                              Stop
                            </Button>
                          </>
                        ) : (
                          <Button
                            disabled={busy || !!draft}
                            onClick={() => setDraft(draftFor(data, "new"))}
                          >
                            <Plus data-icon="inline-start" />
                            New timer
                          </Button>
                        )}
                      </div>
                    </div>
                  </FramePanel>
                </Frame>
              )}
              {draft && (
                <Editor
                  draft={draft}
                  current={data}
                  busy={busy}
                  change={setDraft}
                  cancel={() => {
                    setDraft(null);
                    setError("");
                  }}
                  save={saveDraft}
                  hide={() =>
                    void act(
                      {
                        action: "hide-activity",
                        payload: {
                          project_id: draft.work!.project_id,
                          description: draft.work!.description,
                          context: draft.snapshot.project_context,
                        },
                      },
                      true,
                    )
                  }
                />
              )}
              {!draft && (
                <>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <Tabs value={tab} onValueChange={changeTab}>
                      <TabsList>
                        <TabsTrigger value="work">Work</TabsTrigger>
                        <TabsTrigger value="tasks">Tasks</TabsTrigger>
                        <TabsTrigger value="today">Today</TabsTrigger>
                      </TabsList>
                    </Tabs>
                    <div className="flex gap-1">
                      <Button
                        variant="ghost"
                        size="default"
                        disabled={
                          busy ||
                          !!draft ||
                          data.task_capability === "unavailable"
                        }
                        onClick={() => setDraft(draftFor(data, "new-task"))}
                      >
                        New task
                      </Button>
                      <Button
                        variant="ghost"
                        size="default"
                        disabled={busy || !!draft}
                        onClick={() => setDraft(draftFor(data, "new"))}
                      >
                        <Plus data-icon="inline-start" />
                        New timer
                      </Button>
                    </div>
                  </div>
                  <div className="search-row">
                    <Input
                      aria-label="Search work"
                      placeholder="Search your work…"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                    <div className="project-filter">
                      <Choice
                        label="Filter by project"
                        value={project}
                        options={[
                          { id: "all", name: "All projects" },
                          { id: "none", name: "No project" },
                          ...projects.map((p) => ({
                            id: String(p.id),
                            name: p.name,
                          })),
                        ]}
                        onChange={setProject}
                      />
                    </div>
                  </div>
                </>
              )}
              <ScrollArea
                className={cn(
                  "work-list flex-1",
                  draft ? "min-h-0" : "min-h-24",
                )}
                data-testid="work-list"
              >
                <ItemGroup
                  className="gap-1"
                  aria-label={
                    tab !== "today" ? "Available work" : "Today’s entries"
                  }
                >
                  {tab !== "today"
                    ? works.map((work) => (
                        <Item
                          key={work.key}
                          size="sm"
                          variant={running(data, work) ? "muted" : "default"}
                        >
                          <ItemContent className="min-w-0">
                            <ItemTitle className="max-w-full">
                              {work.label}
                            </ItemTitle>
                            <ItemDescription>
                              {projectName(data, work.project_id)}
                              {work.task
                                ? work.task.active === false
                                  ? " · Archived task"
                                  : " · Saved task"
                                : ""}
                            </ItemDescription>
                          </ItemContent>
                          <ItemActions>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Edit ${work.label}`}
                              disabled={busy || !!draft}
                              onClick={() => openWork(work)}
                            >
                              <Pencil />
                            </Button>
                            {work.task?.active === false ? (
                              <Button
                                variant="ghost"
                                size="default"
                                disabled={busy || !!draft}
                                onClick={() =>
                                  void act({
                                    action: "task-update",
                                    payload: {
                                      context: data.project_context,
                                      task: work.task,
                                      name: work.task!.name,
                                      active: true,
                                    },
                                  })
                                }
                              >
                                Restore
                              </Button>
                            ) : (
                              <Button
                                variant="ghost"
                                size="icon"
                                aria-label={
                                  running(data, work)
                                    ? `${work.label} is running`
                                    : `Start ${work.label}`
                                }
                                disabled={
                                  busy || !!draft || running(data, work)
                                }
                                onClick={() =>
                                  void act({
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
                                  })
                                }
                              >
                                {running(data, work) ? <Check /> : <Play />}
                              </Button>
                            )}
                            {work.task && (
                              <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    aria-label={`More actions for ${work.label}`}
                                    disabled={busy || !!draft}
                                  >
                                    <MoreHorizontal />
                                  </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end">
                                  <DropdownMenuGroup>
                                    <DropdownMenuItem
                                      onSelect={() =>
                                        setDraft(draftFor(data, "task", work))
                                      }
                                    >
                                      Rename task
                                    </DropdownMenuItem>
                                    <DropdownMenuItem
                                      onSelect={() =>
                                        void act({
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
                                      {work.task!.active === false
                                        ? "Restore task"
                                        : "Archive task"}
                                    </DropdownMenuItem>
                                  </DropdownMenuGroup>
                                </DropdownMenuContent>
                              </DropdownMenu>
                            )}
                          </ItemActions>
                        </Item>
                      ))
                    : entries.map((entry) => (
                        <Item key={String(entry.id)} size="sm">
                          <ItemContent className="min-w-0">
                            <ItemTitle>
                              {entry.description ||
                                taskName(data, entry.task_id) ||
                                "No description"}
                            </ItemTitle>
                            <ItemDescription>
                              {projectName(data, entry.project_id)} ·{" "}
                              {new Date(entry.start).toLocaleTimeString([], {
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                              {entry._offline ? " · Pending sync" : ""}
                            </ItemDescription>
                          </ItemContent>
                          <ItemActions>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Edit entry ${entry.description || "No description"}`}
                              disabled={busy || !!draft}
                              onClick={() => openEntry(entry)}
                            >
                              <Pencil />
                            </Button>
                            {entry.stop && (
                              <Button
                                variant="ghost"
                                size="icon"
                                aria-label={`Resume ${entry.description || "No description"}`}
                                disabled={busy || !!draft}
                                onClick={() =>
                                  void act({
                                    action: "resume",
                                    payload: {
                                      entry,
                                      expected: data.state,
                                      context: data.project_context,
                                    },
                                  })
                                }
                              >
                                <Play />
                              </Button>
                            )}
                          </ItemActions>
                        </Item>
                      ))}
                </ItemGroup>
                {(tab !== "today" ? works : entries).length === 0 && (
                  <Empty>
                    <EmptyHeader>
                      <EmptyTitle>
                        {query
                          ? "No matching work"
                          : tab === "work"
                            ? "A fresh start"
                            : "No entries to show"}
                      </EmptyTitle>
                      <EmptyDescription>
                        {query
                          ? "Try another name or project."
                          : tab === "work"
                            ? "Start a timer to make it easy to return to your work."
                            : "Refresh to load today’s entries."}
                      </EmptyDescription>
                    </EmptyHeader>
                  </Empty>
                )}
              </ScrollArea>
              <footer className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">
                  {data.pending_count
                    ? `${data.pending_count} pending sync`
                    : "No pending sync"}
                </span>
                <Button
                  variant="ghost"
                  size="default"
                  disabled={busy}
                  onClick={() =>
                    void act({ action: "refresh", payload: { force: true } })
                  }
                >
                  <RefreshCw data-icon="inline-start" />
                  Refresh
                </Button>
              </footer>
            </>
          )}
        </div>
      </main>
    </TooltipProvider>
  );
}
