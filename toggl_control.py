"""Private local control socket shared by the tray and launcher integrations."""
import json
import os
import socket
import socketserver
import threading
import time
from pathlib import Path

MAX_MESSAGE = 2 * 1024 * 1024


class ControlError(Exception):
    pass


def snapshot(app):
    with app.state_lock:
        current = dict(app.state)
    today = app.datetime.now().date()
    pending = app._pending_as_entries(today)
    cached = getattr(app, "_control_entries", []) if getattr(app, "_control_entries_date", "") == today.isoformat() else []
    entries = app._merge_entries_with_pending(cached, pending)
    return {"state": current, "projects": app._cached_projects(), "entries": entries,
            "pending_count": len(app._load_pending()), "warning": getattr(app, "_control_warning", "")}


def _changes(app, values):
    if not isinstance(values, dict) or set(values) - {"description", "project_id"}:
        raise ControlError("Only description and project may be edited")
    if "description" in values and (not isinstance(values["description"], str) or len(values["description"]) > 10000):
        raise ControlError("Description must be text of at most 10000 characters")
    result = dict(values)
    if result.get("project_id") is not None:
        if str(result["project_id"]) == str(app.state.get("project_id")):
            result["project_id"] = app.state["project_id"]
            return result
        project = next((p for p in app._cached_projects() if str(p.get("id")) == str(result["project_id"])
                        and str(p.get("workspace_id")) == str(app.state.get("workspace_id"))
                        ), None)
        if not project:
            raise ControlError("Project is unavailable; refresh the project list")
        result["project_id"] = project["id"]
    return result


def _edit_current(app, changes):
    with app.state_lock:
        current = dict(app.state)
    if current.get("tracking") and current.get("entry_id"):
        if not app.api_token or app._rate_limit_active() or app._request_budget_exhausted():
            raise ControlError("Cloud entry cannot be edited right now; retry when connected")
        app.update_entry(current["workspace_id"], current["entry_id"], changes)
    if current.get("tracking") and not current.get("entry_id"):
        with app.pending_lock:
            queue = app._load_pending()
            candidates = [op for op in app._pending_operations(queue) if op["kind"] == "open"
                          and op.get("start_time") == current.get("start_time")]
            if not candidates:
                raise ControlError("Pending timer changed; refresh before editing")
            for idx in candidates[-1]["indexes"]:
                queue[idx].update(changes)
            app._save_pending(queue)
    with app.state_lock:
        app.state.update(changes)
        app.save_state()


def _edit_entry(app, selected, changes):
    if not isinstance(selected, dict):
        raise ControlError("Entry is required")
    if selected.get("_offline"):
        with app.pending_lock:
            queue = app._load_pending()
            candidates = app._pending_as_entries()
            entry = next((e for e in candidates if all(e.get(k) == selected.get(k)
                         for k in ("id", "start", "stop", "description"))), None)
            if not entry:
                raise ControlError("Pending entry changed; refresh before editing")
            for idx in entry["_pending_indexes"]:
                queue[idx].update(changes)
            app._save_pending(queue)
        if entry.get("_pending_kind") == "open" and app.state.get("start_time") == entry.get("start"):
            with app.state_lock:
                app.state.update(changes)
                app.save_state()
    else:
        entries = getattr(app, "_control_entries", [])
        entry = next((e for e in entries if str(e.get("id")) == str(selected.get("id"))), None)
        if not entry:
            raise ControlError("Entry changed; refresh before editing")
        if not app.api_token or app._rate_limit_active() or app._request_budget_exhausted():
            raise ControlError("Cloud entry cannot be edited right now; retry when connected")
        app.update_entry(entry.get("workspace_id") or app.state["workspace_id"], entry["id"], changes)
        entry.update(changes)
        if app.state.get("entry_id") == entry["id"]:
            with app.state_lock:
                app.state.update(changes)
                app.save_state()


def dispatch(app, action, payload):
    if not isinstance(payload, dict):
        raise ControlError("Command payload must be an object")
    if action == "snapshot":
        return snapshot(app)
    if action == "refresh":
        last = getattr(app, "_control_refreshed_at", None)
        if last is None or time.monotonic() - last >= 60:
            app._control_refreshed_at = time.monotonic()
            app._refresh_projects()
            try:
                if not app.api_token or app._rate_limit_active() or app._request_budget_remaining() <= app.REQUEST_BUDGET_BACKGROUND_RESERVE:
                    raise ControlError("Cloud refresh unavailable; showing cached and pending entries")
                app._control_entries = app.fetch_today_entries()
                app._control_entries_date = app.datetime.now().date().isoformat()
                app._control_warning = ""
            except Exception:
                app._control_warning = "Cloud refresh unavailable; showing cached and pending entries"
        return snapshot(app)
    if action not in {"start", "stop", "edit-current", "edit-entry"}:
        raise ControlError("Unknown action")
    # Share the tray's mutation lock, including its existing toggle implementation.
    with app.toggle_lock:
        if action == "start":
            if not app.state.get("tracking"):
                _edit_current(app, _changes(app, payload.get("changes", {})))
                app.toggle_tracking(prompt_for_description=False)
        elif action == "stop":
            if app.state.get("tracking"):
                app.toggle_tracking(prompt_for_description=False)
        elif action == "edit-current":
            _edit_current(app, _changes(app, payload.get("changes", {})))
        elif action == "edit-entry":
            _edit_entry(app, payload.get("entry"), _changes(app, payload.get("changes", {})))
    if app.state.get("tracking") and app.state.get("entry_id"):
        current = app.state
        entry = {"id": current["entry_id"], "workspace_id": current["workspace_id"],
                 "description": current.get("description", ""), "project_id": current.get("project_id"),
                 "start": current["start_time"], "stop": None,
                 "duration": -int(app._coerce_datetime(current["start_time"]).timestamp())}
        cache = getattr(app, "_control_entries", [])
        app._control_entries = [e for e in cache if e.get("id") != entry["id"]] + [entry]
        app._control_entries_date = app.datetime.now().date().isoformat()
    if app.icon_ref:
        app.GLib.idle_add(app._refresh_tray_menu)
    return snapshot(app)


def start_server(app):
    path = app.STATE_DIR / "control.sock"
    path.unlink(missing_ok=True)  # Caller already owns the single-instance lock.

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(10)
            try:
                line = self.rfile.readline(16385)
                if len(line) > 16384 or not line.endswith(b"\n"):
                    raise ControlError("Command is too large or incomplete")
                command = json.loads(line)
                result = dispatch(app, command["action"], command.get("payload", {}))
                response = {"ok": True, "result": result}
            except Exception as error:
                response = {"ok": False, "error": str(error)[:500]}
            self.wfile.write(json.dumps(response).encode() + b"\n")

    previous_mask = os.umask(0o177)
    try:
        server = socketserver.UnixStreamServer(str(path), Handler)
    finally:
        os.umask(previous_mask)
    os.chmod(path, 0o600)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def request(state_dir, action, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(35)
        try:
            client.connect(str(Path(state_dir) / "control.sock"))
        except OSError as error:
            raise ControlError("Toggl Tray is not running. Launch the tray app and try again.") from error
        command = json.dumps({"action": action, "payload": payload}).encode() + b"\n"
        if len(command) > 16384:
            raise ControlError("Command is too large")
        client.sendall(command)
        with client.makefile("rb") as reader:
            line = reader.readline(MAX_MESSAGE + 1)
        if not line or len(line) > MAX_MESSAGE:
            raise ControlError("Invalid response from Toggl Tray")
        response = json.loads(line)
        if not response.get("ok"):
            raise ControlError(response.get("error", "Command failed"))
        return response["result"]
