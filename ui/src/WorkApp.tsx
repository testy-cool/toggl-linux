import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "cn";
import {
  ChevronDown,
  Clock3,
  Pencil,
  RefreshCw,
  Square,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Item,
  ItemContent,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import { TimeEntryRow } from "./TimeEntryRow";
import { TimeInput } from "./TimeInput";
import {
  localDay,
  todayTotals,
  todayLabel,
  todayGroupLabel,
  todaySummary,
} from "./today";
import { ActionHint } from "./ActionHint";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription, AlertTitle } from "@/components/reui/alert";
import { TooltipProvider } from "@/components/ui/tooltip";
import { WorkTree } from "./WorkTree";
import { Editor, Choice } from "./Editor";
import { host, request } from "./api";
import {
  commandFor,
  afterOwnStop,
  draftFor,
  elapsed,
  entryFromSnapshot,
  projectName,
  running,
  sameId,
  timerTitle,
  taskName,
  workGroups,
  pendingSource,
  rebaseSyncedDraft,
  isCurrentEntry,
  localTime,
  retimeCommand,
  stopCommand,
  timerKey,
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
  const dailyAttempt = useRef("");
  const todayReading = useRef(false);
  const [loadingToday, setLoadingToday] = useState(false);
  const latest = useRef<Snapshot | null>(null);
  useEffect(() => {
    latest.current = data;
  }, [data]);
  // A typed time saves when the field loses focus. Stop and the next typed time must survive that
  // save; every other control stays disabled during it, as for any write.
  const correcting = useRef(false);
  const [savingTime, setSavingTime] = useState(false);
  const heldBack = useRef<(() => Command | null)[]>([]);
  const preferenceWrites = useRef<Promise<unknown>>(Promise.resolve());
  const draftRef = useRef<Draft | null>(null);
  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  const poll = useCallback(async () => {
    if (writing.current || reading.current) return;
    reading.current = true;
    const version = epoch.current;
    try {
      const frozen = draftRef.current;
      const source = frozen && pendingSource(frozen);
      const snapshot = await request(
        "work-snapshot",
        source
          ? {
              draft_entry: source,
              draft_context: frozen!.snapshot.project_context,
            }
          : {},
      );
      if (mounted.current && version === epoch.current && !writing.current) {
        setData(snapshot);
        setDraft((previous) =>
          previous ? rebaseSyncedDraft(previous, snapshot) : null,
        );
      }
    } catch (failure) {
      if (mounted.current && version === epoch.current)
        setError(String(failure instanceof Error ? failure.message : failure));
    } finally {
      reading.current = false;
    }
  }, []);
  const act = useCallback(
    async function run(
      command: Command,
      closeDraft = false,
      correction = false,
      keepError = false,
    ): Promise<void> {
      if (writing.current) return;
      const next = () => {
        while (heldBack.current.length) {
          const held = heldBack.current.shift()!();
          if (held) return void run(held, false, held.action !== "stop", true);
        }
        void poll();
      };
      if (
        command.action === "edit-current" &&
        Object.keys(command.payload.changes as object).length === 0
      ) {
        setDraft(null);
        next();
        return;
      }
      writing.current = true;
      correcting.current = correction;
      epoch.current += 1;
      setBusy(true);
      setSavingTime(correction);
      if (!keepError) setError("");
      try {
        const frozen = draftRef.current,
          source = frozen && pendingSource(frozen);
        const result = await request(
          command.action,
          command.action === "stop" && source
            ? {
                ...command.payload,
                draft_entry: source,
                draft_context: frozen!.snapshot.project_context,
              }
            : command.payload,
        );
        if (mounted.current) {
          latest.current = latest.current
            ? { ...latest.current, ...result }
            : result;
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
        correcting.current = false;
        epoch.current += 1;
        setBusy(false);
        setSavingTime(false);
        next();
      }
    },
    [poll],
  );
  const loadToday = useCallback(
    async (manual = false) => {
      if (todayReading.current) return;
      todayReading.current = true;
      setLoadingToday(true);
      try {
        await request(
          manual ? "refresh" : "refresh-today",
          manual ? { force: true } : {},
        );
      } catch (failure) {
        if (mounted.current)
          setError(
            String(failure instanceof Error ? failure.message : failure),
          );
      } finally {
        todayReading.current = false;
        if (mounted.current) {
          setLoadingToday(false);
          void poll();
        }
      }
    },
    [poll],
  );
  const changeTab = useCallback((value: string) => setTab(value), []);
  const day = localDay(now);
  useEffect(() => {
    if (!data || draft || todayReading.current) return;
    const key = JSON.stringify([data.project_context, day]);
    if (dailyAttempt.current === key) return;
    dailyAttempt.current = key;
    if (data.today_date !== day || !data.today_fetched_at) void loadToday();
  }, [data, draft, day, loadingToday, loadToday]);
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
        !event.isComposing &&
        !event.defaultPrevented &&
        !writing.current &&
        !document.querySelector('[role="listbox"], [role="menu"]')
      ) {
        event.preventDefault();
        void host().then((bridge) => bridge.close());
      }
    };
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("toggl-edit-current", open);
      window.removeEventListener("toggl-open-today", today);
      window.removeEventListener("keydown", key);
    };
  }, [data, draft, changeTab]);
  function openWork(work: Work) {
    if (!data || busy || !running(data, work)) return;
    setError("");
    setDraft(draftFor(data, "current", work));
  }
  function openEntry(entry: Entry) {
    if (!data || busy) return;
    setDraft(
      draftFor(
        data,
        isCurrentEntry(data, entry) ? "current" : "entry",
        undefined,
        entry,
      ),
    );
  }
  function retime(entry: Entry | null, field: "start" | "stop", time: string) {
    if (!data || (writing.current && !correcting.current)) return;
    if (correcting.current) {
      heldBack.current.push(() => {
        const snapshot = latest.current!;
        const command = retimeCommand(
          snapshot,
          entry ? entryFromSnapshot(snapshot, entry) : null,
          field,
          time,
        );
        if (!command)
          setError("Review this entry with its pencil before changing its times.");
        return command;
      });
      return;
    }
    try {
      const command = retimeCommand(data, entry, field, time);
      if (command) void act(command, false, true);
      else if (entry) openEntry(entry);
    } catch (failure) {
      setError(String(failure));
    }
  }
  function stopTimer() {
    if (!data) return;
    const clicked = data.state;
    const build = () => stopCommand(latest.current!, clicked);
    if (correcting.current) heldBack.current.push(build);
    else {
      const command = build();
      if (command) void act(command);
    }
  }
  function saveDraft(asTask = false) {
    if (!draft) return;
    try {
      void act(commandFor(draft, asTask), true);
    } catch (failure) {
      setError(String(failure));
    }
  }
  function saveExpansion(
    knownProjects: (string | number | null)[],
    expandedProjects: (string | number | null)[],
    expandedTasks: (string | number)[],
  ) {
    if (!data) return;
    const context = data.project_context;
    preferenceWrites.current = preferenceWrites.current
      .catch(() => undefined)
      .then(() =>
        request("save-work-preferences", {
          context,
          known_projects: knownProjects,
          expanded_projects: expandedProjects,
          expanded_tasks: expandedTasks,
        }),
      )
      .catch((failure) => {
        if (mounted.current)
          setError(
            String(failure instanceof Error ? failure.message : failure),
          );
      });
  }
  const projects =
    data?.projects.filter(
      (p) =>
        sameId(p.workspace_id, data.state.workspace_id) && p.active !== false,
    ) ?? [];
  const needle = query.trim().toLocaleLowerCase();
  const groups = data ? workGroups(data, tab === "tasks", query, project) : [];
  const totals = data ? todayTotals(data, now) : null;
  const todayGroups = totals
    ? [...totals.groups.values()].filter(
        (group) =>
          (project === "all" || sameId(group.project_id, project)) &&
          `${todayGroupLabel(data!, group)} ${group.rows.map((row) => row.entry.description ?? "").join(" ")}`
            .toLocaleLowerCase()
            .includes(needle),
      )
    : [];
  const currentTitle = data ? timerTitle(data) : "";
  const currentContext = data
    ? [
        projectName(data, data.state.project_id),
        taskName(data, data.state.task_id),
      ]
        .filter(Boolean)
        .join(" · ")
    : "";

  return (
    <TooltipProvider delayDuration={500}>
      <main className="app-shell" data-testid="work-app">
        <header className="app-header">
          <div className="pywebview-drag-region flex min-w-0 flex-1 items-center gap-3">
            <Clock3 aria-hidden="true" className="app-mark" />
            <span className="font-semibold">Toggl</span>
            {draft && data?.state.tracking && (
              <span className="current-project truncate text-xs text-muted-foreground">
                {elapsed(data.state, now)} · {timerTitle(data)}
              </span>
            )}
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
              {(data.warning || data.today_error) && (
                <Alert variant="warning">
                  <AlertDescription>
                    {data.warning || data.today_error}
                  </AlertDescription>
                </Alert>
              )}
              {!draft && data.state.tracking && (
                <Item
                  size="xs"
                  variant="muted"
                  className="current-timer"
                  data-testid="current-timer"
                >
                  <ActionHint text={`${currentTitle} · ${currentContext}`}>
                    <ItemContent
                      className="current-work"
                      tabIndex={0}
                      title={`${currentTitle} · ${currentContext}`}
                      aria-label={`Running timer: ${currentTitle}. ${currentContext}`}
                    >
                      <ItemTitle className="current-description">
                        <span className="current-title truncate">
                          {currentTitle}
                        </span>
                        <span className="current-project truncate">
                          {currentContext}
                        </span>
                      </ItemTitle>
                    </ItemContent>
                  </ActionHint>
                  <ActionHint text="Edit running timer">
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="current-edit"
                      disabled={busy}
                      aria-label="Edit running timer"
                      onClick={() => setDraft(draftFor(data, "current"))}
                    >
                      <Pencil />
                    </Button>
                  </ActionHint>
                  <label className="current-start">
                    Started
                    <TimeInput
                      key={timerKey(data)}
                      label="Started at"
                      value={localTime(data.state.start_time).slice(11, 16)}
                      onChange={(time) => retime(null, "start", time)}
                    />
                  </label>
                  <span className="elapsed" aria-label="Elapsed time">
                    {elapsed(data.state, now)}
                  </span>
                  <Button
                    variant="destructive"
                    className="current-stop"
                    disabled={busy && !savingTime}
                    onClick={stopTimer}
                  >
                    <Square data-icon="inline-start" /> Stop
                  </Button>
                </Item>
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
                  <div className="work-toolbar flex items-center justify-between gap-2">
                    <Tabs value={tab} onValueChange={changeTab}>
                      <TabsList>
                        <TabsTrigger value="work">Projects</TabsTrigger>
                        <TabsTrigger value="tasks">Tasks</TabsTrigger>
                        <TabsTrigger value="today">Today</TabsTrigger>
                      </TabsList>
                    </Tabs>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          disabled={busy}
                          aria-label="New work"
                        >
                          New <ChevronDown data-icon="inline-end" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuGroup>
                          <DropdownMenuItem
                            disabled={data.task_capability === "unavailable"}
                            onSelect={() =>
                              setDraft(draftFor(data, "new-task"))
                            }
                          >
                            New task
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onSelect={() => setDraft(draftFor(data, "new"))}
                          >
                            New timer
                          </DropdownMenuItem>
                        </DropdownMenuGroup>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                  <div className="search-row">
                    <Input
                      aria-label="Search work"
                      placeholder="Search projects, tasks, and recent activity…"
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
                  draft ? "hidden" : "min-h-24",
                )}
                data-testid="work-list"
              >
                <div hidden={!!draft || tab === "today"}>
                  <div className="tree-column-heading">Today</div>
                  <WorkTree
                    data={data}
                    today={totals!}
                    openEntry={openEntry}
                    retime={retime}
                    prepare={(work) => setDraft(draftFor(data, "new", work))}
                    saveTask={(work) =>
                      setDraft(draftFor(data, "new-task", work))
                    }
                    query={query}
                    project={project}
                    tasksOnly={tab === "tasks"}
                    busy={busy || !!draft}
                    savingTime={savingTime}
                    edit={openWork}
                    rename={(work) => setDraft(draftFor(data, "task", work))}
                    createTask={(group) =>
                      setDraft({
                        ...draftFor(data, "new-task"),
                        project: String(group.id),
                      })
                    }
                    saveExpansion={saveExpansion}
                    act={(command) => void act(command)}
                  />
                </div>
                {tab === "today" && !draft && (
                  <ItemGroup className="gap-3" aria-label="Today’s entries">
                    {todayGroups.map((group) => (
                      <ItemGroup
                        key={group.key}
                        className="gap-1"
                        aria-label={group.label}
                      >
                        <ItemTitle className="today-group-title">
                          {todayGroupLabel(data, group)}
                          <span className="work-time">
                            {todayLabel(totals!, group.seconds)}
                          </span>
                        </ItemTitle>
                        {group.rows.map((row) => (
                          <TimeEntryRow
                            key={row.key}
                            row={row}
                            parentLabel={group.label}
                            busy={busy || !!draft}
                            savingTime={savingTime}
                            edit={openEntry}
                            retime={retime}
                            resume={(entry) =>
                              void act({
                                action: "resume",
                                payload: {
                                  entry,
                                  expected: data.state,
                                  context: data.project_context,
                                },
                              })
                            }
                          />
                        ))}
                      </ItemGroup>
                    ))}
                  </ItemGroup>
                )}
                {!draft &&
                  (tab !== "today" ? groups : todayGroups).length === 0 && (
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
                              : totals!.loaded && !totals!.incomplete
                                ? "No time tracked today."
                                : "Refresh to load today’s entries."}
                        </EmptyDescription>
                      </EmptyHeader>
                    </Empty>
                  )}
              </ScrollArea>
              {!draft && (
                <footer className="flex items-center justify-between gap-3">
                  <span className="text-xs text-muted-foreground">
                    {loadingToday
                      ? "Loading today’s time…"
                      : todaySummary(data, totals!)}
                    {data.pending_count
                      ? ` · ${data.pending_count} pending sync`
                      : ""}
                  </span>
                  <Button
                    variant="ghost"
                    size="default"
                    disabled={loadingToday}
                    onClick={() => void loadToday(true)}
                  >
                    <RefreshCw data-icon="inline-start" />
                    Refresh
                  </Button>
                </footer>
              )}
            </>
          )}
        </div>
      </main>
    </TooltipProvider>
  );
}
