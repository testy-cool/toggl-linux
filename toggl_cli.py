"""Command-line tools: status, doctor, entries, audit, start/stop, sync and launcher install."""
import argparse
import fcntl
import json
import shlex
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import toggl_tray as app


_NO_LIVE_TRAY = object()


def _tray_lock_held():
    """Return whether another process owns the tray's single-instance lock."""
    if not app.LOCK_FILE.exists():
        return False
    lock_fp = open(app.LOCK_FILE, "a+")
    try:
        try:
            fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(lock_fp, fcntl.LOCK_UN)
        return False
    finally:
        lock_fp.close()


def _live_snapshot():
    """Reach the authoritative tray process, or prove there is no live instance."""
    import toggl_control
    socket_path = app.STATE_DIR / "control.sock"
    if socket_path.exists():
        try:
            return toggl_control.request(app.STATE_DIR, "snapshot", {})
        except toggl_control.ControlError:
            if _tray_lock_held():
                raise
    if _tray_lock_held():
        raise toggl_control.ControlError("Toggl Tray is starting or its control socket is unavailable; try again")
    return _NO_LIVE_TRAY


def _run_live_cli(args):
    """Run state-changing commands in the tray process when it is active."""
    import toggl_control
    snapshot = _live_snapshot()
    if snapshot is _NO_LIVE_TRAY:
        return None
    current = snapshot["state"]

    if args.command == "start":
        if current.get("tracking"):
            print("Already tracking.")
            if current.get("start_time"):
                print(f"Started: {_format_local_dt(current['start_time'])}")
            return 0
        description = " ".join(args.description).strip()
        changes = {"description": description} if args.description else {}
        result = toggl_control.request(app.STATE_DIR, "start", {"changes": changes, "expected": current})
        state = result["state"]
        target = "on Toggl" if state.get("entry_id") else "locally"
        print(f"Started {target} at {_format_local_dt(state['start_time'])}.")
        return 0

    if args.command == "stop":
        if not current.get("tracking"):
            print("Not tracking.")
            return 0
        stopped_on_toggl = bool(current.get("entry_id"))
        toggl_control.request(app.STATE_DIR, "stop", {"expected": current})
        target = "on Toggl" if stopped_on_toggl else "locally"
        print(f"Stopped {target} at {_format_local_dt(datetime.now(timezone.utc))}.")
        return 0

    if args.command == "set-start":
        if not current.get("tracking") or not current.get("start_time"):
            print("No running local timer found.")
            return 1
        reference = app._coerce_datetime(current["start_time"]).astimezone()
        new_start = app._parse_hhmm(args.time, reference)
        if not new_start:
            print(f"Invalid time '{args.time}'. Use HH:MM.", file=sys.stderr)
            return 2
        result = toggl_control.request(
            app.STATE_DIR, "edit-current",
            {"changes": {"start": new_start.isoformat()}, "expected": current},
        )
        print(f"Start time set to {_format_local_dt(result['state']['start_time'])}.")
        return 0

    if args.command == "sync":
        result = toggl_control.request(app.STATE_DIR, "sync", {})
        print(f"Sync complete. Pending actions remaining: {result['pending_count']}")
        return 0

    if args.command == "set-api-base":
        result = toggl_control.request(app.STATE_DIR, "set-api-base", {"url": args.url})
        print(f"API base: {result['api_base']}")
        print(f"Config file: {app.CONFIG_FILE}")
        print("Cleared stored workspace/project/entry ids for the configured API base.")
        if result["cleared_ids"]:
            print(f"Cleared {result['cleared_ids']} id field(s) from pending local actions.")
        return 0

    return None


def _format_local_dt(value):
    dt = app._coerce_datetime(value).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_entry_line(entry):
    start_dt = app._coerce_datetime(entry.get("start")).astimezone()
    stop_text = "running"
    if entry.get("stop"):
        stop_text = app._coerce_datetime(entry["stop"]).astimezone().strftime("%H:%M")
    source = "local pending" if entry.get("_offline") else "toggl"
    desc = entry.get("description") or "(no description)"
    return (
        f"{start_dt.strftime('%Y-%m-%d %H:%M')} - {stop_text:<7} "
        f"{app._format_duration(app._entry_duration_seconds(entry)):>9}  "
        f"[{source}] {desc}"
    )


def _print_entries(entries):
    sorted_entries = sorted(entries, key=lambda item: item.get("start", ""))
    total = sum(app._entry_duration_seconds(entry) for entry in sorted_entries)
    for entry in sorted_entries:
        print(_format_entry_line(entry))
    print(f"Total: {app._format_duration(total)}")


def _parse_cli_date(value):
    if not value:
        return datetime.now().date()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        print(f"Invalid date '{value}'. Use YYYY-MM-DD.", file=sys.stderr)
        return None


def _file_json_status(path):
    if not path.exists():
        return "missing"
    try:
        json.loads(path.read_text())
        return "ok"
    except (json.JSONDecodeError, OSError):
        if app._backup_path(path).exists():
            try:
                json.loads(app._backup_path(path).read_text())
                return "corrupt (backup ok)"
            except (json.JSONDecodeError, OSError):
                pass
        return "corrupt"


def _load_cli_runtime(fetch_workspace=False):
    pass
    app.load_state()
    app.api_token = app.get_api_token()
    if fetch_workspace and app.api_token and not app.state.get("workspace_id"):
        try:
            me = app.fetch_me()
            app.state["workspace_id"] = me["default_workspace_id"]
            app.save_state()
        except Exception as e:
            print(f"Could not fetch workspace: {e}", file=sys.stderr)


def _cli_status(_args):
    _load_cli_runtime()
    print(f"State file: {app.STATE_FILE}")
    print(f"Pending file: {app.PENDING_FILE}")
    print(f"API base: {app.get_api_base()}")
    print(f"API token: {'yes' if app.api_token else 'no'}")
    print(f"Workspace: {app.state.get('workspace_id') or '(unknown)'}")
    print(f"Tracking: {'yes' if app.state.get('tracking') else 'no'}")
    if app.state.get("tracking") and app.state.get("start_time"):
        print(f"Started: {_format_local_dt(app.state['start_time'])}")
        start = app._coerce_datetime(app.state["start_time"]).astimezone(timezone.utc)
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        print(f"Elapsed: {app._format_duration(elapsed)}")
    if app.state.get("entry_id"):
        print(f"Toggl entry id: {app.state['entry_id']}")
    elif app.state.get("tracking"):
        print("Toggl entry id: none (local/offline timer)")

    raw_pending = app._load_pending()
    pending_entries = app._pending_as_entries()
    print(f"Pending queue: {len(raw_pending)} action(s), {len(pending_entries)} visible entries")
    if pending_entries:
        print("")
        _print_entries(pending_entries)
    return 0


def _cli_doctor(args):
    _load_cli_runtime()
    pending = app._load_pending()
    pending_entries = app._pending_as_entries()
    ledger_events = app._read_ledger_events()
    rate_limit_left = max(int(app.rate_limited_until - app.time.monotonic()), 0)
    request_budget = app._request_budget_status()

    print("Toggl Tray Doctor")
    print(f"State file: {app.STATE_FILE} ({_file_json_status(app.STATE_FILE)})")
    print(f"Pending file: {app.PENDING_FILE} ({_file_json_status(app.PENDING_FILE)})")
    print(f"Ledger file: {app.LEDGER_FILE} ({'ok' if app.LEDGER_FILE.exists() else 'missing'})")
    debug_path = app.STATE_DIR / 'debug.log'
    print(f"Debug log: {debug_path} ({'ok' if debug_path.exists() else 'missing; created when tray starts'})")
    print(f"Config file: {app.CONFIG_FILE} ({_file_json_status(app.CONFIG_FILE)})")
    print(f"API base: {app.get_api_base()}")
    print(f"API token: {'yes' if app.api_token else 'no'}")
    print(f"Workspace: {app.state.get('workspace_id') or '(unknown)'}")
    print(f"Tracking: {'yes' if app.state.get('tracking') else 'no'}")
    if app.state.get("start_time"):
        print(f"Started: {_format_local_dt(app.state['start_time'])}")
    if app.state.get("entry_id"):
        print(f"Toggl entry id: {app.state['entry_id']}")
    elif app.state.get("tracking"):
        print("Toggl entry id: none (local/offline timer)")
    print(f"Pending actions: {len(pending)}")
    print(f"Pending visible entries: {len(pending_entries)}")
    print(f"Ledger events: {len(ledger_events)}")
    print(f"Rate limit: {'active for ' + str(rate_limit_left) + 's' if rate_limit_left else 'inactive'}")
    print(f"Last status: {app.status_message or 'none'}")
    if app._request_budget_applies():
        print(
            f"Request budget: {request_budget['used']}/{request_budget['limit']} used, "
            f"{request_budget['remaining']} remaining"
        )
    else:
        print("Request budget: not applied for configured API base")

    if args.cloud:
        if not app.api_token:
            print("Cloud current: skipped (no API token)")
        else:
            try:
                current = app.fetch_current_entry()
                if current:
                    print(f"Cloud current: yes ({current.get('id')})")
                else:
                    print("Cloud current: no")
            except Exception as e:
                print(f"Cloud current: error ({e})")
    else:
        print("Cloud current: skipped (use --cloud)")
    return 0


def _cli_entries(args):
    date_obj = _parse_cli_date(args.date)
    if date_obj is None:
        return 2
    _load_cli_runtime()
    pending_entries = app._pending_as_entries(date_obj)
    entries = []
    if not args.local_only:
        if app.api_token:
            try:
                entries = app.fetch_entries_for_date(date_obj)
            except Exception as e:
                print(f"Could not fetch Toggl entries; showing local pending only: {e}", file=sys.stderr)
        else:
            print("No API token; showing local pending only.", file=sys.stderr)
    entries = app._merge_entries_with_pending(entries, pending_entries)
    if not entries:
        print(f"No entries for {date_obj.isoformat()}.")
        if app._load_pending():
            print("There are pending actions, but none start on that local date.")
        return 0
    _print_entries(entries)
    return 0


def _date_range(start, end):
    date_obj = start
    while date_obj <= end:
        yield date_obj
        date_obj = date_obj + timedelta(days=1)


def _entry_gap_count(entries, threshold_minutes):
    complete = []
    for entry in entries:
        if not entry.get("start") or not entry.get("stop"):
            continue
        complete.append(entry)
    complete.sort(key=lambda item: item.get("start", ""))
    gaps = 0
    threshold_seconds = threshold_minutes * 60
    for prev, current in zip(complete, complete[1:]):
        prev_stop = app._coerce_datetime(prev["stop"])
        current_start = app._coerce_datetime(current["start"])
        if (current_start - prev_stop).total_seconds() > threshold_seconds:
            gaps += 1
    return gaps


def _cli_audit(args):
    start = _parse_cli_date(args.date_from)
    end = _parse_cli_date(args.date_to)
    if start is None or end is None:
        return 2
    if start > end:
        print("Start date must be before or equal to end date.", file=sys.stderr)
        return 2

    _load_cli_runtime()
    grand_total = 0
    print(f"Audit {start.isoformat()}..{end.isoformat()}")
    for date_obj in _date_range(start, end):
        pending_entries = app._pending_as_entries(date_obj)
        entries = []
        if not args.local_only and app.api_token:
            try:
                entries = app.fetch_entries_for_date(date_obj)
            except Exception as e:
                print(f"Could not fetch Toggl entries for {date_obj.isoformat()}: {e}", file=sys.stderr)
        elif not args.local_only and not app.api_token:
            print("No API token; auditing local pending entries only.", file=sys.stderr)

        merged = app._merge_entries_with_pending(entries, pending_entries)
        total = sum(app._entry_duration_seconds(entry) for entry in merged)
        blank = sum(1 for entry in merged if not entry.get("description"))
        local_count = sum(1 for entry in merged if entry.get("_offline"))
        gap_count = _entry_gap_count(merged, args.gap_minutes)
        grand_total += total
        print(
            f"{date_obj.isoformat()}: {app._format_duration(total)} total, "
            f"{len(merged)} entries, {blank} blank, {local_count} local pending, "
            f"{gap_count} large gaps"
        )
    print(f"Total: {app._format_duration(grand_total)}")
    return 0


def _cli_start(args):
    _load_cli_runtime(fetch_workspace=True)
    app._append_event("cli_start_requested", description=" ".join(args.description).strip())
    open_pending = [op for op in app._pending_operations(app._load_pending()) if op.get("kind") == "open"]
    if app.state.get("tracking") or open_pending:
        if not app.state.get("tracking") and open_pending:
            op = open_pending[-1]
            app.state["tracking"] = True
            app.state["entry_id"] = None
            app.state["start_time"] = op.get("start_time")
            app.state["description"] = op.get("description", app.state.get("description", ""))
            app.save_state()
        print("Already tracking.")
        if app.state.get("start_time"):
            print(f"Started: {_format_local_dt(app.state['start_time'])}")
        return 0

    if args.description:
        app.state["description"] = " ".join(args.description).strip()
    description = app.state.get("description", "")
    project_id = app.state.get("project_id")
    workspace_id = app.state.get("workspace_id")
    now = datetime.now(timezone.utc).isoformat()

    entry = None
    if app.api_token and workspace_id:
        try:
            entry = app.start_entry(workspace_id, description=description, project_id=project_id, start_time=now)
        except Exception as e:
            print(f"Could not start on Toggl; queued locally: {e}", file=sys.stderr)
    elif not app.api_token:
        print("No API token; queued locally.", file=sys.stderr)
    else:
        print("No workspace id; queued locally.", file=sys.stderr)

    if entry:
        app.state["tracking"] = True
        app.state["entry_id"] = entry["id"]
        app.state["start_time"] = entry.get("start", now)
        app.save_state()
        app._append_event("cli_started", entry_id=entry["id"], start_time=app.state["start_time"], mode="cloud")
        print(f"Started on Toggl at {_format_local_dt(app.state['start_time'])}.")
    else:
        app.state["tracking"] = True
        app.state["entry_id"] = None
        app.state["start_time"] = now
        app.save_state()
        app.queue_action("start", start_time=now, description=description, project_id=project_id)
        app._append_event("cli_started", entry_id=None, start_time=now, mode="local")
        print(f"Started locally at {_format_local_dt(now)}.")
    return 0


def _cli_stop(_args):
    _load_cli_runtime()
    app._append_event("cli_stop_requested", was_tracking=app.state.get("tracking"))
    if not app.state.get("tracking"):
        open_pending = [op for op in app._pending_operations(app._load_pending()) if op.get("kind") == "open"]
        if not open_pending:
            print("Not tracking.")
            return 0
        op = open_pending[-1]
        now = datetime.now(timezone.utc).isoformat()
        app.queue_action(
            "stop",
            start_time=op.get("start_time"),
            stop_time=now,
            description=op.get("description", ""),
            project_id=op.get("project_id"),
            workspace_id=op.get("workspace_id"),
        )
        print(f"Stopped open local pending entry at {_format_local_dt(now)}.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    entry_id = app.state.get("entry_id")
    workspace_id = app.state.get("workspace_id")
    start_time = app.state.get("start_time")
    description = app.state.get("description", "")

    stopped_on_toggl = False
    if app.api_token and entry_id and workspace_id:
        try:
            app.stop_entry(workspace_id, entry_id)
            stopped_on_toggl = True
        except Exception as e:
            print(f"Could not stop on Toggl; queued locally: {e}", file=sys.stderr)

    if not stopped_on_toggl:
        app.queue_action(
            "stop",
            entry_id=entry_id,
            start_time=start_time,
            stop_time=now,
            description=description,
            project_id=app.state.get("project_id"),
            workspace_id=workspace_id,
        )

    app.state["tracking"] = False
    app.state["entry_id"] = None
    app.state["start_time"] = None
    app.save_state()
    app._append_event(
        "cli_stopped",
        entry_id=entry_id,
        stop_time=now,
        mode="cloud" if stopped_on_toggl else "local",
    )
    target = "on Toggl" if stopped_on_toggl else "locally"
    print(f"Stopped {target} at {_format_local_dt(now)}.")
    return 0


def _cli_set_start(args):
    _load_cli_runtime()
    reference = app.state.get("start_time")
    pending_entries = [entry for entry in app._pending_as_entries() if entry.get("_pending_kind") == "open"]
    if not reference and pending_entries:
        reference = pending_entries[-1].get("start")
    reference_dt = app._coerce_datetime(reference).astimezone()
    new_start = app._parse_hhmm(args.time, reference_dt)
    if not new_start:
        print(f"Invalid time '{args.time}'. Use HH:MM.", file=sys.stderr)
        return 2
    new_start_iso = new_start.isoformat()

    updated = False
    if pending_entries:
        updated = app._update_pending_entry(pending_entries[-1], {"start": new_start_iso}) or updated

    if app.state.get("tracking"):
        app.state["start_time"] = new_start_iso
        app.save_state()
        updated = True

    if app.state.get("entry_id") and app.state.get("workspace_id") and app.api_token:
        try:
            app.update_entry(app.state["workspace_id"], app.state["entry_id"], {
                "start": new_start_iso,
                "duration": -int(new_start.timestamp()),
            })
        except Exception as e:
            print(f"Could not update Toggl start time: {e}", file=sys.stderr)

    if not updated:
        print("No running local timer found.")
        return 1
    print(f"Start time set to {_format_local_dt(new_start_iso)}.")
    return 0


def _cli_sync(_args):
    _load_cli_runtime(fetch_workspace=True)
    if not app.api_token:
        print("No API token; cannot sync pending entries.", file=sys.stderr)
        return 2
    remaining = app.sync_pending(retry_review=True)
    app._sync_cloud_state()
    print(f"Sync complete. Pending actions remaining: {remaining}")
    return 0


def _cli_set_api_base(args):
    app.load_state()
    try:
        api_base, pending_ids = app.switch_api_base(args.url)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"API base: {api_base}")
    print(f"Config file: {app.CONFIG_FILE}")
    print("Cleared stored workspace/project/entry ids for the configured API base.")
    if pending_ids:
        print(f"Cleared {pending_ids} id field(s) from pending local actions.")
    return 0


def _desktop_quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _launcher_python():
    repo_python = Path(app.__file__).resolve().parent / ".venv" / "bin" / "python"
    if repo_python.exists():
        return repo_python
    return Path(sys.executable).resolve()


def _launcher_icon():
    try:
        app._init_icons()
        return Path(app._icon_path_inactive)
    except Exception:
        return Path(app.__file__).resolve().parent / "toggl_icon.webp"


def _desktop_entry_text(autostart=False):
    script = Path(app.__file__).resolve()
    exec_cmd = f"{_desktop_quote(_launcher_python())} {_desktop_quote(script)} run"
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=Toggl Tray",
        "Comment=Toggl Track tray timer",
        f"Exec={exec_cmd}",
        f"Icon={_launcher_icon()}",
        "Terminal=false",
        "StartupNotify=false",
        "Categories=Utility;",
        "Keywords=Toggl;Track;Timer;Time;",
    ]
    if autostart:
        lines.append("X-GNOME-Autostart-enabled=true")
    return "\n".join(lines) + "\n"


def _write_desktop_file(path, autostart=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_desktop_entry_text(autostart=autostart))
    path.chmod(0o755)
    return path


def _write_cli_launcher(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    python = shlex.quote(str(_launcher_python()))
    script = shlex.quote(str(Path(app.__file__).resolve()))
    path.write_text(f'#!/bin/sh\nexec {python} {script} "$@"\n')
    path.chmod(0o755)
    return path


def _refresh_desktop_database():
    updater = shutil.which("update-desktop-database")
    if updater:
        app.subprocess.run(
            [updater, str(app.APPLICATIONS_DIR)],
            stdout=app.subprocess.DEVNULL,
            stderr=app.subprocess.DEVNULL,
            timeout=10,
            check=False,
        )


def _cli_install_app(args):
    app_file = _write_desktop_file(app.DESKTOP_FILE)
    print(f"Installed app launcher: {app_file}")
    cli_file = _write_cli_launcher(app.CLI_FILE)
    print(f"Installed CLI launcher: {cli_file}")
    if args.autostart:
        autostart_file = _write_desktop_file(app.AUTOSTART_FILE, autostart=True)
        print(f"Installed autostart launcher: {autostart_file}")
    _refresh_desktop_database()
    print("Look for 'Toggl Tray' in the app launcher.")
    print("Run 'toggl --help' for terminal commands.")
    return 0


def _cli_uninstall_app(_args):
    removed = []
    for path in (app.DESKTOP_FILE, app.AUTOSTART_FILE, app.CLI_FILE):
        if path.exists():
            path.unlink()
            removed.append(path)
    _refresh_desktop_database()
    if removed:
        for path in removed:
            print(f"Removed: {path}")
    else:
        print("No Toggl Tray desktop launchers were installed.")
    return 0


def _build_cli_parser():
    parser = argparse.ArgumentParser(
        prog="toggl",
        description="Run the Toggl tray app or inspect/recover local tracking state.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="run the tray app")
    control = sub.add_parser("control", help="control the running tray using its private socket")
    control.add_argument("action", choices=["snapshot", "refresh", "sync", "start", "switch", "resume", "stop", "edit-current", "edit-entry", "project-create", "project-update", "task-create", "task-update", "set-api-base"])
    control.add_argument("payload", nargs="?", default="{}", help="JSON command payload")
    sub.add_parser("status", help="show local state and pending queue")
    doctor = sub.add_parser("doctor", help="diagnose local state, pending queue, token, and optional cloud state")
    doctor.add_argument("--cloud", action="store_true", help="spend one API request to check Toggl's current timer")

    entries = sub.add_parser("entries", aliases=["today"], help="show entries for a local date")
    entries.add_argument("date", nargs="?", help="local date, YYYY-MM-DD (default: today)")
    entries.add_argument("--local-only", action="store_true", help="only show pending local entries")

    audit = sub.add_parser("audit", help="summarize entries, blanks, local pending work, and large gaps")
    audit.add_argument("date_from", help="start local date, YYYY-MM-DD")
    audit.add_argument("date_to", nargs="?", help="end local date, YYYY-MM-DD (default: start date)")
    audit.add_argument("--local-only", action="store_true", help="only audit pending local entries")
    audit.add_argument("--gap-minutes", type=int, default=120, help="count gaps larger than this many minutes")

    local = sub.add_parser("local", help="show pending local entries for a date")
    local.add_argument("date", nargs="?", help="local date, YYYY-MM-DD (default: today)")
    local.set_defaults(local_only=True)

    start = sub.add_parser("start", help="start tracking from the terminal")
    start.add_argument("description", nargs="*", help="optional description")

    sub.add_parser("stop", help="stop tracking from the terminal")
    set_start = sub.add_parser("set-start", help="set current timer start time, HH:MM local")
    set_start.add_argument("time", help="new local start time, HH:MM")
    set_api_base = sub.add_parser("set-api-base", help="set the Toggl-compatible API base URL")
    set_api_base.add_argument("url", help="API base URL, for example https://opentickly.example.com/api/v9")
    sub.add_parser("sync", help="try to sync pending local entries now")
    install_app = sub.add_parser("install-app", help="install desktop app and CLI launchers")
    install_app.add_argument("--autostart", action="store_true", help="also start Toggl Tray on login")
    sub.add_parser("uninstall-app", help="remove desktop app launcher")
    return parser


def _run_cli(argv):
    if not argv:
        return None
    parser = _build_cli_parser()
    args = parser.parse_args(argv)
    if args.command == "control":
        import toggl_control
        try:
            result = toggl_control.request(app.STATE_DIR, args.action, json.loads(args.payload))
            print(json.dumps({"ok": True, "result": result}))
            return 0
        except Exception as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            return 2
    if args.command == "run":
        return None
    if args.command in {"start", "stop", "set-start", "sync", "set-api-base"}:
        import toggl_control
        try:
            live_result = _run_live_cli(args)
        except toggl_control.ControlError as error:
            print(str(error), file=sys.stderr)
            return 2
        if live_result is not None:
            return live_result
        lock_fp = app._acquire_instance_lock()
        try:
            if args.command == "start":
                return _cli_start(args)
            if args.command == "stop":
                return _cli_stop(args)
            if args.command == "set-start":
                return _cli_set_start(args)
            if args.command == "set-api-base":
                return _cli_set_api_base(args)
            return _cli_sync(args)
        finally:
            lock_fp.close()
    if args.command == "status":
        return _cli_status(args)
    if args.command == "doctor":
        return _cli_doctor(args)
    if args.command == "audit":
        if args.date_to is None:
            args.date_to = args.date_from
        return _cli_audit(args)
    if args.command in ("entries", "today", "local"):
        if args.command == "local":
            args.local_only = True
        return _cli_entries(args)
    if args.command == "install-app":
        return _cli_install_app(args)
    if args.command == "uninstall-app":
        return _cli_uninstall_app(args)
    parser.print_help()
    return 2
