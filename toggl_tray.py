#!/usr/bin/env python3
"""Toggl Track tray timer — Ctrl+Shift+T to toggle tracking."""

import os
import sys
import json
import time
import fcntl
import io
import contextlib
import hashlib
import re
import threading
import subprocess
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit

# Run as a script, this module is __main__; the split-out modules import it as toggl_tray.
if __name__ == "__main__":
    sys.modules.setdefault("toggl_tray", sys.modules[__name__])
import toggl_task_tree
import toggl_tasks
import toggl_debug
import requests
from requests.auth import HTTPBasicAuth
from PIL import Image
import pystray
from Xlib import X, XK
from Xlib.display import Display
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

try:
    gi.require_version("XApp", "1.0")
    from gi.repository import XApp
except (ImportError, ValueError):
    XApp = None

# ── Config ──────────────────────────────────────────────────────────────────

DEFAULT_API_BASE = "https://api.track.toggl.com/api/v9"
API_BASE_ENV = "TOGGL_API_BASE"
STATE_DIR = Path.home() / ".local" / "share" / "toggl-tray"
CONFIG_FILE = STATE_DIR / "config.json"
STATE_FILE = STATE_DIR / "state.json"
PENDING_FILE = STATE_DIR / "pending.json"
LEDGER_FILE = STATE_DIR / "events.jsonl"
REQUEST_BUDGET_FILE = STATE_DIR / "request_budget.json"
LOCK_FILE = STATE_DIR / "toggl-tray.lock"
APPLICATIONS_DIR = Path.home() / ".local" / "share" / "applications"
DESKTOP_FILE = APPLICATIONS_DIR / "toggl-tray.desktop"
AUTOSTART_DIR = Path.home() / ".config" / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "toggl-tray.desktop"
CLI_FILE = Path.home() / ".local" / "bin" / "toggl"
ICON_SIZE = 64
ICON_PADDING = 6  # transparent padding around the icon
SYNC_INTERVAL_SECONDS = 300
CLOUD_POLL_INTERVAL_SECONDS = 3600
NO_TOKEN_SYNC_MESSAGE = "No Toggl API token — pending entries cannot sync"
AUTH_FAILED_SYNC_MESSAGE = "Toggl auth failed — pending entries kept locally"
REQUEST_BUDGET_LIMIT = 30
REQUEST_BUDGET_WINDOW_SECONDS = 3600
REQUEST_BUDGET_BACKGROUND_RESERVE = 6
REQUEST_BUDGET_SYNC_MESSAGE = "Toggl request budget exhausted — pending sync paused"
SYNC_CONFLICT_MESSAGE = "Toggl conflict — local start kept pending"
OPEN_START_MATCH_TOLERANCE_SECONDS = 300
ENTRY_WINDOW_WIDTH = 560
ENTRY_WINDOW_HEIGHT = 560
ENTRY_ROW_DESC_CHARS = 42
TIMER_WINDOW_WIDTH = 430
TIMER_WINDOW_HEIGHT = 260
PANEL_TIMER_LABEL_GUIDE = "88:88:88 · MMMMMMMMMMMMMMMMMMMMMMMMMMMM"


# ── State ───────────────────────────────────────────────────────────────────

state = {
    "tracking": False,
    "entry_id": None,
    "start_time": None,  # ISO string
    "workspace_id": None,
    "project_id": None,
    "task_id": None,
    "description": "",
}

icon_ref = None
timer_window_ref = None
native_status_icon_ref = None
native_status_menu_ref = None
api_token = None
_restored_remote_id = None
_startup_scope = None
rate_limited_until = 0.0
status_message = ""
_request_budget_lock = threading.Lock()


class RateLimitedError(Exception):
    """Raised when Toggl asks us to wait before making more requests."""

    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__(f"Rate limited for {retry_after} seconds")


class MissingApiTokenError(Exception):
    """Raised when an API call is attempted without a configured token."""


class RestoredStopReviewError(RuntimeError):
    """A restored stop needs a user's correction or local discard before retry."""


# ── API Token ───────────────────────────────────────────────────────────────

def get_api_token():
    """Get API token from env or GNOME keyring."""
    token = os.environ.get("TOGGL_API_TOKEN")
    if token:
        return token
    initial_token = api_token
    service = _api_token_service()
    services = [service]
    config = _load_json_with_backup(CONFIG_FILE, {})
    origin = _api_origin()
    legacy_origin = config.get("legacy_opentickly_origin") if isinstance(config, dict) else None
    if service.startswith("opentickly:") and legacy_origin in (None, origin):
        services.append("opentickly")  # migrate the former shared custom-server key once
    lookups = [(candidate, command) for candidate in services for command in (
        ["secret-tool", "lookup", "service", candidate, "username", "api_token", "application", "toggl-tray"],
        ["secret-tool", "lookup", "service", candidate, "username", "api_token"],
    )]
    if service == "toggl":
        lookups.append((service, ["secret-tool", "lookup", "application", "toggl-tray"]))
    for candidate, cmd in lookups:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                token = result.stdout.strip()
                if candidate == "opentickly" and service != candidate:
                    with toggle_lock:
                        if _api_origin() != origin or api_token != initial_token:
                            return None
                        store_api_token(token)
                        config = _load_json_with_backup(CONFIG_FILE, {})
                        config = config if isinstance(config, dict) else {}
                        config["legacy_opentickly_origin"] = origin
                        _atomic_write_text(CONFIG_FILE, json.dumps(config, indent=2))
                return token
        except (FileNotFoundError, subprocess.TimeoutExpired):
            break
    return None


def store_api_token(token):
    """Store token in GNOME keyring."""
    service = _api_token_service()
    label = ("Toggl API Token" if service == "toggl" else
             f"OpenTickly API Token ({urlsplit(get_api_base()).hostname})")
    try:
        result = subprocess.run(
            ["secret-tool", "store", f"--label={label}",
             "service", service, "username", "api_token",
             "application", "toggl-tray"],
            input=token, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ── API Base ────────────────────────────────────────────────────────────────

def _normalize_api_base(value):
    base = str(value or DEFAULT_API_BASE).strip().rstrip("/")
    base = base or DEFAULT_API_BASE
    try:
        parsed = urlsplit(base)
        parsed.port  # Validate a malformed port eagerly.
    except ValueError as error:
        raise ValueError("API base must be a valid HTTP or HTTPS URL") from error
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("API base must be an HTTP or HTTPS URL without credentials, query, or fragment")
    if parsed.scheme == "http" and parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote API bases must use HTTPS")
    if parsed.hostname.lower() == "api.track.toggl.com" and parsed.scheme != "https":
        raise ValueError("The Toggl API base must use HTTPS")
    return base


def get_api_base():
    """Return the Toggl-compatible API base URL."""
    env_base = os.environ.get(API_BASE_ENV)
    if env_base:
        return _normalize_api_base(env_base)
    config = _load_json_with_backup(CONFIG_FILE, {})
    if isinstance(config, dict):
        return _normalize_api_base(config.get("api_base"))
    return DEFAULT_API_BASE


def set_api_base(value):
    """Persist a non-secret Toggl-compatible API base URL."""
    api_base = _normalize_api_base(value)
    config = _load_json_with_backup(CONFIG_FILE, {})
    if not isinstance(config, dict):
        config = {}
    config["api_base"] = api_base
    _atomic_write_text(CONFIG_FILE, json.dumps(config, indent=2))
    return api_base


def get_panel_timer_enabled():
    """Return whether elapsed time should be visible beside the tray icon."""
    config = _load_json_with_backup(CONFIG_FILE, {})
    return bool(config.get("show_timer_in_panel", False)) if isinstance(config, dict) else False


def set_panel_timer_enabled(enabled):
    """Persist whether elapsed time is visible beside the tray icon."""
    config = _load_json_with_backup(CONFIG_FILE, {})
    if not isinstance(config, dict):
        config = {}
    config["show_timer_in_panel"] = bool(enabled)
    _atomic_write_text(CONFIG_FILE, json.dumps(config, indent=2))
    return config["show_timer_in_panel"]


def get_panel_description_enabled():
    """Return whether the task description should be visible beside the tray icon."""
    config = _load_json_with_backup(CONFIG_FILE, {})
    return bool(config.get("show_description_in_panel", False)) if isinstance(config, dict) else False


def set_panel_description_enabled(enabled):
    """Persist whether the task description is visible beside the tray icon."""
    config = _load_json_with_backup(CONFIG_FILE, {})
    if not isinstance(config, dict):
        config = {}
    config["show_description_in_panel"] = bool(enabled)
    _atomic_write_text(CONFIG_FILE, json.dumps(config, indent=2))
    return config["show_description_in_panel"]


def _api_token_service():
    if _is_toggl_api_base():
        return "toggl"
    origin = _api_origin()
    return "opentickly:" + hashlib.sha256(origin.encode()).hexdigest()[:16]


def _api_origin(api_base=None):
    parsed = urlsplit(_normalize_api_base(api_base or get_api_base()))
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme.lower()}://{host}{port}"


def _is_toggl_api_base(api_base=None):
    parsed = urlsplit(_normalize_api_base(api_base or get_api_base()))
    return parsed.hostname.lower() == "api.track.toggl.com"


# ── Local Toggl request budget ──────────────────────────────────────────────

def _load_request_timestamps(now=None):
    """Return locally recorded Toggl request attempts inside the current window."""
    now = time.time() if now is None else float(now)
    cutoff = now - REQUEST_BUDGET_WINDOW_SECONDS
    raw = _load_json_with_backup(REQUEST_BUDGET_FILE, [])
    if not isinstance(raw, list):
        return []

    timestamps = []
    for value in raw:
        try:
            ts = float(value)
        except (TypeError, ValueError):
            continue
        if cutoff <= ts <= now + 60:
            timestamps.append(ts)
    return timestamps


def _request_budget_applies(api_base=None):
    return _is_toggl_api_base(api_base)


def _save_request_timestamps(timestamps):
    _atomic_write_text(REQUEST_BUDGET_FILE, json.dumps(timestamps, indent=2))


def _record_api_request(now=None):
    """Record an attempted Toggl HTTP request for local budget visibility."""
    now = time.time() if now is None else float(now)
    with _request_budget_lock:
        timestamps = _load_request_timestamps(now=now)
        timestamps.append(now)
        _save_request_timestamps(timestamps)
        return len(timestamps)


def _request_budget_status(now=None):
    timestamps = _load_request_timestamps(now=now)
    used = len(timestamps)
    return {
        "used": used,
        "remaining": max(REQUEST_BUDGET_LIMIT - used, 0),
        "limit": REQUEST_BUDGET_LIMIT,
    }


def _request_budget_remaining(now=None):
    if not _request_budget_applies():
        return REQUEST_BUDGET_LIMIT
    return _request_budget_status(now=now)["remaining"]


def _request_budget_exhausted():
    return _request_budget_applies() and _request_budget_remaining() <= 0


# ── Toggl API ───────────────────────────────────────────────────────────────

def _auth():
    return HTTPBasicAuth(api_token, "api_token")


def _api(method, path, json=None, params=None, token=None, base_url=None):
    """Single API wrapper. 429s are deferred instead of retried inline."""
    global rate_limited_until
    if not (token or api_token):
        raise MissingApiTokenError("No Toggl API token configured")
    api_base = base_url if base_url is not None else get_api_base()
    if _request_budget_applies(api_base):
        _record_api_request()
    started = time.monotonic()
    route = urlsplit(path).path  # Never log query parameters, headers or request bodies.
    try:
        r = requests.request(
            method, f"{api_base}{path}",
            auth=_auth() if token is None else HTTPBasicAuth(token, "api_token"), json=json, params=params, timeout=10,
        )
    except Exception as error:
        toggl_debug.LOGGER.warning('API %s %s failed=%s duration_ms=%.0f', method, route,
                                    type(error).__name__, (time.monotonic() - started) * 1000)
        raise
    toggl_debug.LOGGER.debug('API %s %s status=%s duration_ms=%.0f', method, route,
                             r.status_code, (time.monotonic() - started) * 1000)
    if r.status_code == 429:
        try:
            wait = int(r.headers.get("X-Toggl-Quota-Resets-In", SYNC_INTERVAL_SECONDS))
        except ValueError:
            wait = SYNC_INTERVAL_SECONDS
        _play_sound(SOUND_ERROR)
        rate_limited_until = time.monotonic() + max(wait, 1)
        raise RateLimitedError(max(wait, 1))
    r.raise_for_status()
    return r.json() if r.content else None


def api_get(path, **kw):
    return _api("GET", path, **kw)


def api_post(path, data, **kw):
    return _api("POST", path, json=data, **kw)


def api_put(path, data, **kw):
    return _api("PUT", path, json=data, **kw)


def api_delete(path):
    _api("DELETE", path)


def fetch_me(**kwargs):
    return api_get("/me", **kwargs)


def fetch_current_entry(**kwargs):
    """Get currently running time entry, or None."""
    data = api_get("/me/time_entries/current", **kwargs)
    return data if data else None


def _coerce_datetime(value):
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    else:
        dt = _parse_iso(value)
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def start_entry(workspace_id, description="", project_id=None, task_id=None, start_time=None):
    start = _coerce_datetime(start_time)
    payload = {
        "created_with": "toggl-tray-linux",
        "description": description,
        "start": start.isoformat(),
        "duration": -1 * int(start.timestamp()),
        "workspace_id": workspace_id,
    }
    if project_id:
        payload["project_id"] = project_id
    if task_id is not None:
        payload["task_id"] = task_id
    return api_post(f"/workspaces/{workspace_id}/time_entries", payload)


def stop_entry(workspace_id, entry_id):
    """Stop a running entry by PATCHing it."""
    return _api("PATCH", f"/workspaces/{workspace_id}/time_entries/{entry_id}/stop")


def fetch_entries_for_date(date_obj):
    """Get time entries for a specific local date."""
    if isinstance(date_obj, datetime):
        date_obj = date_obj.date()
    next_date = date_obj + timedelta(days=1)
    start_local = datetime(date_obj.year, date_obj.month, date_obj.day).astimezone()
    end_local = datetime(next_date.year, next_date.month, next_date.day).astimezone()
    params = {
        "start_date": start_local.astimezone(timezone.utc).isoformat(),
        "end_date": end_local.astimezone(timezone.utc).isoformat(),
    }
    return api_get("/me/time_entries", params=params) or []


def fetch_today_entries():
    return fetch_entries_for_date(datetime.now().date())


def update_entry(workspace_id, entry_id, data, **kwargs):
    return api_put(f"/workspaces/{workspace_id}/time_entries/{entry_id}", data, **kwargs)


def delete_entry(workspace_id, entry_id):
    return api_delete(f"/workspaces/{workspace_id}/time_entries/{entry_id}")


# ── Local persistence ──────────────────────────────────────────────────────

def _backup_path(path):
    return path.with_name(path.name + ".bak")


def _ensure_private_state_dir():
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE_DIR.chmod(0o700)
    private_files = (
        CONFIG_FILE, STATE_FILE, PENDING_FILE, LEDGER_FILE, REQUEST_BUDGET_FILE, LOCK_FILE,
        STATE_DIR / "projects.json", STATE_DIR / "tasks.json", STATE_DIR / "task-history.json",
    )
    for path in private_files:
        for candidate in (path, _backup_path(path)):
            if candidate.exists():
                candidate.chmod(0o600)


def _atomic_write_text(path, text, backup=True):
    if path.parent == STATE_DIR:
        _ensure_private_state_dir()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if backup and path.exists():
        try:
            existing = path.read_text()
            json.loads(existing)
            backup_path = _backup_path(path)
            backup_path.write_text(existing)
            backup_path.chmod(0o600)
        except (json.JSONDecodeError, OSError):
            pass
    tmp.write_text(text)
    tmp.chmod(0o600)
    os.replace(tmp, path)
    path.chmod(0o600)


def _load_json_with_backup(path, default):
    for candidate in (path, _backup_path(path)):
        if candidate.exists():
            try:
                return json.loads(candidate.read_text())
            except (json.JSONDecodeError, OSError):
                continue
    return default


def _append_event(event, **data):
    toggl_debug.LOGGER.info('Event %s tracking=%s entry_id=%s project_id=%s task_id=%s', event,
                            state.get('tracking'), data.get('entry_id', state.get('entry_id')),
                            state.get('project_id'), state.get('task_id'))
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **data,
    }
    _ensure_private_state_dir()
    with LEDGER_FILE.open("a") as fp:
        fp.write(json.dumps(record, sort_keys=True) + "\n")
    LEDGER_FILE.chmod(0o600)


def _read_ledger_events(limit=None):
    if not LEDGER_FILE.exists():
        return []
    events = []
    try:
        lines = LEDGER_FILE.read_text().splitlines()
    except OSError:
        return []
    if limit is not None:
        lines = lines[-limit:]
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def save_state():
    # Stamp each new interval with the account it started under, so a later token change cannot hand it to another account.
    if state.get("start_time") != state.get("_account_start"):
        import toggl_projects
        state["_account"] = toggl_projects.account(sys.modules[__name__]) if state.get("start_time") else None
        state["_account_start"] = state.get("start_time")
    _atomic_write_text(STATE_FILE, json.dumps(state, indent=2))


def load_state():
    global _restored_remote_id
    saved = _load_json_with_backup(STATE_FILE, None)
    if isinstance(saved, dict):
        state.update(saved)
    _restored_remote_id = state.get('entry_id') if state.get('tracking') else None


def _clear_remote_ids():
    """Drop provider-specific ids when switching API backends."""
    changed = False
    for key in ("workspace_id", "project_id", "task_id", "entry_id"):
        if state.get(key) is not None:
            state[key] = None
            changed = True
    if changed:
        save_state()

    pending = _load_pending()
    pending_changes = 0
    for item in pending:
        if not isinstance(item, dict):
            continue
        for key in ("workspace_id", "project_id", "task_id", "entry_id"):
            if key in item:
                item.pop(key, None)
                pending_changes += 1
    if pending_changes:
        _save_pending(pending)
    return pending_changes


def switch_api_base(value):
    """Change provider without carrying credentials, ids, or project metadata across origins."""
    global api_token, _project_cache, _task_cache, _task_capability, _last_project_refresh_attempt, _last_tray_icon_path
    if os.environ.get(API_BASE_ENV):
        raise ValueError(f"Unset {API_BASE_ENV} before saving a different API base")
    api_base = set_api_base(value)
    api_token = None
    pending_ids = _clear_remote_ids()
    _project_cache = None
    _task_cache = None
    _task_capability = "unknown"
    _last_project_refresh_attempt = None
    _last_tray_icon_path = None
    return api_base, pending_ids


# ── Offline queue ──────────────────────────────────────────────────────────

pending_lock = threading.RLock()
toggle_lock = threading.RLock()
state_lock = threading.Lock()
_sync_wakeup = threading.Event()


def _load_pending():
    pending = _load_json_with_backup(PENDING_FILE, [])
    return pending if isinstance(pending, list) else []


def _active_pending():
    return [item for item in _load_pending()
            if not (item.get('_verify_running') and item.get('_review_error'))]


def _stop_verification_snapshot(item, account):
    return dict(base=get_api_base(), account=account, fields={
        key: item.get(key) for key in ('start_time', 'stop_time', 'description', 'project_id', 'task_id')})


def _save_pending(queue):
    if queue:
        _atomic_write_text(PENDING_FILE, json.dumps(queue, indent=2))
    else:
        # Persist completion before removing the recovery copy. Callers hold
        # pending_lock, so a newly queued action cannot be removed here.
        _atomic_write_text(PENDING_FILE, "[]", backup=False)
        _backup_path(PENDING_FILE).unlink(missing_ok=True)
        PENDING_FILE.unlink(missing_ok=True)


def queue_action(action, **kwargs):
    """Queue an offline action for later sync."""
    if action == 'stop' and kwargs.get('entry_id') is not None and kwargs['entry_id'] == _restored_remote_id:
        kwargs['_verify_running'] = True
        kwargs['_verify_snapshot'] = _stop_verification_snapshot(kwargs, state.get('_account'))
    if "workspace_id" not in kwargs and state.get("workspace_id"):
        kwargs["workspace_id"] = state["workspace_id"]
    import toggl_projects
    task_context = toggl_projects.context(sys.modules[__name__])
    with pending_lock:
        queue = _load_pending()
        item = {"action": action, "ts": datetime.now(timezone.utc).isoformat(),
                "_queue_id": uuid.uuid4().hex, **kwargs,
                "_task_context": task_context}
        queue.append(item)
        if (_startup_scope is not None and _startup_scope['base'] == get_api_base()
                and (not api_token or not state.get('workspace_id'))):
            _startup_scope['queue_ids'].add(item['_queue_id'])
        _append_event("pending_queued", **item)
        _save_pending(queue)
    _sync_wakeup.set()


def _parse_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _duration_seconds(start_time, stop_time):
    start = _parse_iso(start_time)
    stop = _parse_iso(stop_time)
    if not start or not stop:
        return None
    start = start.replace(microsecond=0)
    stop = stop.replace(microsecond=0)
    return max(int((stop - start).total_seconds()), 0)


def _entry_local_date(entry):
    start = _parse_iso(entry.get("start"))
    return start.astimezone().date() if start else None


def _create_pending_op(indexes, start_time, stop_time, item, paired_item=None):
    other = paired_item or {}
    return {
        "kind": "create",
        "indexes": indexes,
        "start_time": start_time,
        "stop_time": stop_time,
        "duration": _duration_seconds(start_time, stop_time),
        "description": other.get("description", item.get("description", "")),
        "project_id": other.get("project_id", item.get("project_id")),
        "task_id": other.get("task_id", item.get("task_id")),
        "workspace_id": other.get("workspace_id", item.get("workspace_id", state.get("workspace_id"))),
    }


def _completed_entry_payload(op, workspace_id):
    payload = {
        "created_with": "toggl-tray-linux",
        "description": op.get("description", ""),
        "start": op["start_time"],
        "stop": op["stop_time"],
        "duration": op["duration"],
        "workspace_id": workspace_id,
    }
    if op.get("project_id"):
        payload["project_id"] = op["project_id"]
    if op.get("task_id") is not None:
        payload["task_id"] = op["task_id"]
    return payload


def _restored_stop_changes(op):
    """Reconcile the target interval, preserving remote data and explicit local edits."""
    import toggl_projects
    token, base = api_token, get_api_base()
    snapshot = op.get('verify_snapshot') or {}
    if not isinstance(snapshot.get('fields'), dict):
        raise RestoredStopReviewError('Older pending stop needs review; edit it or discard local changes')
    if (snapshot.get('base', base) != base
            or snapshot.get('account') not in (None, toggl_projects.account(sys.modules[__name__]))):
        raise RestoredStopReviewError('Account changed; reconnect the original account or discard local changes')
    auth = dict(token=token, base_url=base)
    try:
        remote = api_get(f"/me/time_entries/{int(op['entry_id'])}", **auth)
    except requests.exceptions.HTTPError as error:
        if error.response is not None and error.response.status_code in (404, 405, 410, 501):
            raise RestoredStopReviewError('Target lookup unavailable; review or discard local changes') from error
        raise
    if (api_token != token or get_api_base() != base or not isinstance(remote, dict)
            or str(remote.get('id')) != str(op['entry_id'])
            or str(remote.get('workspace_id')) != str(op.get('workspace_id') or state.get('workspace_id'))):
        raise RestoredStopReviewError('Target or account changed; review the local changes')
    original = snapshot.get('fields') or {}
    data = {key: op[key] for key in ('description', 'project_id', 'task_id')
            if key in op and op[key] != original.get(key, op[key])}
    edited_start = op.get('start_time') != original.get('start_time', op.get('start_time'))
    edited_stop = op.get('stop_time') != original.get('stop_time', op.get('stop_time'))
    start = _parse_iso(op.get('start_time') if edited_start else remote.get('start'))
    desired_stop = _parse_iso(op.get('stop_time'))
    remote_stop = _parse_iso(remote.get('stop'))
    if remote_stop is None and isinstance(remote.get('duration'), (int, float)) and remote['duration'] >= 0:
        remote_stop = _parse_iso(remote.get('start')) + timedelta(seconds=remote['duration'])
    stop = desired_stop if edited_stop or remote_stop is None else min(remote_stop, desired_stop)
    if start is None or stop is None or start.tzinfo is None or stop.tzinfo is None or stop < start:
        raise RestoredStopReviewError('Restored interval times need correction')
    if edited_start:
        data['start'] = op['start_time']
    if edited_start or edited_stop or stop != remote_stop:
        data.update(stop=stop.isoformat(), duration=int((stop-start).total_seconds()))
    return data, auth


def _pending_operations(queue):
    """Normalize raw pending events into sync/display operations."""
    ops = []
    open_start = None

    for idx, item in enumerate(queue):
        action = item.get("action")

        if action == "start":
            if open_start:
                start_idx, start_item, start_time = open_start
                ops.append({
                    "kind": "open",
                    "indexes": [start_idx],
                    "start_time": start_time,
                    "description": start_item.get("description", ""),
                    "project_id": start_item.get("project_id"),
                    "task_id": start_item.get("task_id"),
                    "workspace_id": start_item.get("workspace_id", state.get("workspace_id")),
                })

            start_time = item.get("start_time") or item.get("ts")
            if item.get("stop_time"):
                ops.append(_create_pending_op([idx], start_time, item["stop_time"], item))
                open_start = None
            else:
                open_start = (idx, item, start_time)

        elif action == "stop":
            stop_time = item.get("stop_time") or item.get("ts")
            if item.get("entry_id"):
                ops.append({
                    "kind": "update_stop",
                    "indexes": [idx],
                    "entry_id": item["entry_id"],
                    "verify_running": item.get('_verify_running', False),
                    "verify_snapshot": item.get('_verify_snapshot'),
                    "review_error": item.get('_review_error'),
                    "start_time": item.get("start_time"),
                    "stop_time": stop_time,
                    "duration": _duration_seconds(item.get("start_time"), stop_time),
                    "description": item.get("description"),
                    **({"project_id": item["project_id"]} if "project_id" in item else {}),
                    **({"task_id": item["task_id"]} if "task_id" in item else {}),
                    "workspace_id": item.get("workspace_id", state.get("workspace_id")),
                })
            elif open_start:
                start_idx, start_item, start_time = open_start
                ops.append(_create_pending_op([start_idx, idx], start_time, stop_time, start_item, item))
                open_start = None
            elif item.get("start_time") and stop_time:
                ops.append(_create_pending_op([idx], item["start_time"], stop_time, item))
            else:
                ops.append({"kind": "invalid", "indexes": [idx]})

        elif action == "delete" and item.get("entry_id"):
            ops.append({
                "kind": "delete",
                "indexes": [idx],
                "entry_id": item["entry_id"],
                "workspace_id": item.get("workspace_id", state.get("workspace_id")),
            })
        else:
            ops.append({"kind": "invalid", "indexes": [idx]})

    if open_start:
        start_idx, start_item, start_time = open_start
        ops.append({
            "kind": "open",
            "indexes": [start_idx],
            "start_time": start_time,
            "description": start_item.get("description", ""),
            "project_id": start_item.get("project_id"),
            "task_id": start_item.get("task_id"),
            "workspace_id": start_item.get("workspace_id", state.get("workspace_id")),
        })

    return ops


def _cloud_entry_matches_pending_open_start(current, op):
    """Return True only when a cloud running entry is plausibly our pending start."""
    if not current:
        return False
    if (current.get("description") or "") != (op.get("description") or ""):
        return False
    if current.get("project_id") != op.get("project_id"):
        return False
    if current.get("task_id") != op.get("task_id"):
        return False

    cloud_start = _parse_iso(current.get("start"))
    pending_start = _parse_iso(op.get("start_time"))
    if not cloud_start or not pending_start:
        return False
    return abs((cloud_start - pending_start).total_seconds()) <= OPEN_START_MATCH_TOLERANCE_SECONDS


def _report_open_start_conflict(current, op):
    _notify(SYNC_CONFLICT_MESSAGE)
    _append_event(
        "sync_conflict",
        kind="open_start",
        pending_start=op.get("start_time"),
        pending_description=op.get("description", ""),
        cloud_entry_id=current.get("id"),
        cloud_start=current.get("start"),
        cloud_description=current.get("description", ""),
    )
    print(
        "Pending open start kept because Toggl already has a different running entry "
        f"({current.get('id')}).",
        file=sys.stderr,
    )


def sync_pending(retry_review=False):
    """Try to sync all pending actions. Returns number of remaining."""
    global rate_limited_until
    with pending_lock:
        queue = _load_pending()
        if not queue:
            return 0
        if not api_token:
            _notify(NO_TOKEN_SYNC_MESSAGE)
            return len(queue)

        synced_indexes = set()
        for op in _pending_operations(queue):
            if op.get('review_error') and not retry_review:
                continue
            if op["kind"] == "invalid":
                print(f"Invalid pending item kept for manual recovery: {op.get('indexes')}", file=sys.stderr)
                continue
            if op["kind"] == "open":
                workspace_id = op.get("workspace_id") or state.get("workspace_id")
                if not workspace_id:
                    continue
                try:
                    current = fetch_current_entry()
                    if current:
                        if _cloud_entry_matches_pending_open_start(current, op):
                            with state_lock:
                                state["tracking"] = True
                                state["entry_id"] = current["id"]
                                state["start_time"] = current["start"]
                                state["description"] = current.get("description", "")
                                state["project_id"] = current.get("project_id")
                                state["task_id"] = current.get("task_id")
                                state["workspace_id"] = current.get("workspace_id") or state.get("workspace_id")
                                save_state()
                            synced_indexes.update(op["indexes"])
                        else:
                            _report_open_start_conflict(current, op)
                    else:
                        start_values = dict(description=op.get("description", ""),
                                            project_id=op.get("project_id"),
                                            start_time=op.get("start_time"))
                        if op.get("task_id") is not None:
                            start_values["task_id"] = op["task_id"]
                        entry = start_entry(workspace_id, **start_values)
                        with state_lock:
                            state["tracking"] = True
                            state["entry_id"] = entry["id"]
                            state["start_time"] = entry.get("start", op.get("start_time"))
                            state["description"] = op.get("description", "")
                            state["project_id"] = op.get("project_id")
                            state["task_id"] = op.get("task_id")
                            save_state()
                        synced_indexes.update(op["indexes"])
                except RateLimitedError as e:
                    rate_limited_until = time.monotonic() + e.retry_after
                    break
                except requests.exceptions.HTTPError as e:
                    status = e.response.status_code if e.response is not None else 0
                    if status in (401, 403):
                        _notify(AUTH_FAILED_SYNC_MESSAGE)
                    print(f"Pending open start kept after HTTP {status}: {e}", file=sys.stderr)
                except Exception as e:
                    print(f"Pending sync error (open start): {e}", file=sys.stderr)
                continue

            try:
                workspace_id = op.get("workspace_id") or state.get("workspace_id")
                if not workspace_id:
                    raise RuntimeError("No workspace_id available for pending sync")

                if op["kind"] == "create":
                    payload = _completed_entry_payload(op, workspace_id)
                    api_post(f"/workspaces/{workspace_id}/time_entries", payload)
                elif op["kind"] == "update_stop":
                    data = {"stop": op["stop_time"]}
                    if op.get("start_time"):
                        data["start"] = op["start_time"]
                    if op.get("duration") is not None:
                        data["duration"] = op["duration"]
                    if op.get("description") is not None:
                        data["description"] = op["description"]
                    if "project_id" in op:
                        data["project_id"] = op["project_id"]
                    if "task_id" in op:
                        data["task_id"] = op["task_id"]
                    auth = {}
                    if op.get('verify_running'):
                        data, auth = _restored_stop_changes(op)
                        if not data:
                            _append_event('restored_stop_already_ended', entry_id=op['entry_id'])
                            synced_indexes.update(op['indexes'])
                            continue
                    try:
                        update_entry(workspace_id, op["entry_id"], data, **auth)
                    except requests.exceptions.HTTPError as e:
                        status = e.response.status_code if e.response is not None else 0
                        if op.get('verify_running'):
                            if status in (404, 405, 410, 501):
                                raise RestoredStopReviewError('Target update unavailable; review or discard local changes') from e
                            raise  # Missing/unsupported targets remain editable, never recreated.
                        if status in (404, 410) and op.get("start_time") and op.get("stop_time"):
                            payload = _completed_entry_payload(op, workspace_id)
                            api_post(f"/workspaces/{workspace_id}/time_entries", payload)
                        else:
                            raise
                elif op["kind"] == "delete":
                    try:
                        delete_entry(workspace_id, op["entry_id"])
                    except requests.exceptions.HTTPError as e:
                        if e.response is not None and e.response.status_code in (404, 410):
                            pass
                        else:
                            raise

                synced_indexes.update(op["indexes"])
            except RestoredStopReviewError as error:
                for index in op['indexes']:
                    queue[index]['_review_error'] = str(error)
                _append_event('restored_stop_needs_review', entry_id=op.get('entry_id'), reason=str(error))
                continue
            except RateLimitedError as e:
                rate_limited_until = time.monotonic() + e.retry_after
                break
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in (401, 403):
                    _notify(AUTH_FAILED_SYNC_MESSAGE)
                print(f"Pending item kept after HTTP {status} ({op.get('kind')}): {e}", file=sys.stderr)
                continue
            except MissingApiTokenError:
                _notify(NO_TOKEN_SYNC_MESSAGE)
                break
            except Exception as e:
                print(f"Pending sync error ({op.get('kind')}): {e}", file=sys.stderr)
                continue

        remaining = [item for idx, item in enumerate(queue) if idx not in synced_indexes]
        _save_pending(remaining)
        if not remaining:
            _clear_status_message()
        return len(remaining)


_consecutive_sync_failures = 0
_sync_failure_notified = False
_last_cloud_poll_at = 0.0


def _record_sync_success():
    global _consecutive_sync_failures, _sync_failure_notified
    _consecutive_sync_failures = 0
    _sync_failure_notified = False


def _record_sync_failure(error):
    global _consecutive_sync_failures, _sync_failure_notified
    _consecutive_sync_failures += 1
    print(f"Sync loop error: {error}", file=sys.stderr)
    if _consecutive_sync_failures >= 3 and not _sync_failure_notified:
        _notify("Toggl sync failing — check your connection")
        _sync_failure_notified = True


def _run_sync_cycle(now=None, retry_review=False):
    """Run one background sync pass while preserving API quota for user actions."""
    global api_token, _last_cloud_poll_at
    now = time.monotonic() if now is None else now
    if now < rate_limited_until:
        return

    # The desktop keyring may not be unlocked when the app autostarts.
    if not api_token:
        lookup_base = get_api_base()
        token = get_api_token()
        _accept_keyring_token(token, lookup_base)
    if api_token and not state.get("workspace_id"):
        if _request_budget_exhausted():
            _notify(REQUEST_BUDGET_SYNC_MESSAGE)
            return
        if not _init_workspace():
            return

    with toggle_lock:
        _finish_startup_scope()

    pending = _load_pending() if retry_review else _active_pending()
    if pending:
        if _request_budget_exhausted():
            _notify(REQUEST_BUDGET_SYNC_MESSAGE)
        else:
            if retry_review:
                sync_pending(retry_review=True)
            else:
                sync_pending()
    elif api_token and now - _last_cloud_poll_at >= CLOUD_POLL_INTERVAL_SECONDS:
        if not _request_budget_applies() or _request_budget_remaining() > REQUEST_BUDGET_BACKGROUND_RESERVE:
            _sync_cloud_state()
            _last_cloud_poll_at = now

    _health_check()
    if icon_ref:
        _refresh_projects()
        _refresh_tasks()
        if toggl_task_tree.refresh(sys.modules[__name__]):
            GLib.idle_add(_refresh_project_menu)
    _record_sync_success()


def sync_loop(stop_event=None):
    """Background thread: retry pending actions and sync cloud state."""
    while stop_event is None or not stop_event.is_set():
        _sync_wakeup.clear()
        try:
            _run_sync_cycle()
        except Exception as e:
            _record_sync_failure(e)
        _sync_wakeup.wait(SYNC_INTERVAL_SECONDS)


def _health_check():
    """Detect and recover from tracking-without-cloud-backing."""
    if not api_token:
        return
    if _request_budget_applies() and _request_budget_remaining() <= REQUEST_BUDGET_BACKGROUND_RESERVE:
        return
    with state_lock:
        tracking = state["tracking"]
        entry_id = state["entry_id"]
    if not tracking or entry_id is not None:
        return
    if _active_pending():
        return
    try:
        with state_lock:
            before = dict(state)
        current = fetch_current_entry()
        with state_lock:
            if state != before:
                return  # A start, stop or switch happened during the request; its reply is stale.
            if current:
                state["entry_id"] = current["id"]
                state["start_time"] = current["start"]
                save_state()
            else:
                state["tracking"] = False
                state["entry_id"] = None
                state["start_time"] = None
                save_state()
                _notify("Timer stopped — was running locally but not on Toggl")
                _play_sound(SOUND_ERROR)
    except Exception:
        pass


def _ensure_pending_ids(queue):
    """Give legacy queue items stable identities before exposing them for editing."""
    changed = False
    for item in queue:
        if isinstance(item, dict) and not item.get("_queue_id"):
            item["_queue_id"] = uuid.uuid4().hex
            changed = True
    return changed


def _pending_entries_from_queue(queue, date_obj=None):
    """Convert one locked queue snapshot into entry-like dictionaries."""
    if not queue:
        return []

    entries = []
    for op in _pending_operations(queue):
        if op["kind"] not in {"create", "open", "update_stop"}:
            continue

        start_time = op.get("start_time")
        stop_time = op.get("stop_time")
        if not start_time:
            continue

        duration = op.get("duration")
        if stop_time is None:
            duration = -1 * int(_parse_iso(start_time).timestamp())

        pending_ids = [queue[idx]["_queue_id"] for idx in op["indexes"]]
        entry_id = op.get("entry_id") or f"pending:{'-'.join(pending_ids)}"
        entries.append({
            "id": entry_id,
            "start": start_time,
            "stop": stop_time,
            "duration": duration,
            "description": op.get("description", ""),
            "workspace_id": op.get("workspace_id") or state.get("workspace_id"),
            "project_id": op.get("project_id"),
            "task_id": op.get("task_id"),
            "_offline": True,
            "_pending_indexes": op["indexes"],
            "_pending_ids": pending_ids,
            "_task_context": queue[op["indexes"][0]].get("_task_context"),
            "_pending_kind": op["kind"],
            "_remote_entry_id": op.get("entry_id"),
            "_review_error": op.get('review_error'),
        })

    if date_obj is None:
        return entries
    return [e for e in entries if _entry_local_date(e) == date_obj]


def _pending_as_entries(date_obj=None):
    """Convert pending actions into stable entry dictionaries for display and editing."""
    with pending_lock:
        queue = _load_pending()
        if _ensure_pending_ids(queue):
            _save_pending(queue)
        return _pending_entries_from_queue(queue, date_obj)


def _pending_entry_candidate(queue, selected):
    """Resolve an entry against the current queue, rejecting stale selections."""
    if _ensure_pending_ids(queue):
        _save_pending(queue)
    selected_ids = selected.get("_pending_ids") if isinstance(selected, dict) else None
    if (not isinstance(selected_ids, list) or not selected_ids
            or not all(isinstance(value, str) for value in selected_ids)):
        return None
    selected_ids = set(selected_ids)
    fields = ("start", "stop", "description", "project_id", "workspace_id",
              "_pending_kind", "_remote_entry_id")
    return next((entry for entry in _pending_entries_from_queue(queue)
                 if set(entry.get("_pending_ids", [])) == selected_ids
                 and all(entry.get(key) == selected.get(key) for key in fields)), None)


def _merge_entries_with_pending(entries, pending_entries):
    pending_remote_ids = {
        entry.get("_remote_entry_id") for entry in pending_entries
        if entry.get("_remote_entry_id")
    }
    visible_entries = [
        entry for entry in entries
        if entry.get("id") not in pending_remote_ids
    ]
    return visible_entries + pending_entries


# ── Icon rendering ──────────────────────────────────────────────────────────

_icon_path_active = None
_icon_path_inactive = None
_last_tray_icon_path = None
_last_menu_key = None
_project_cache = None
_last_project_refresh_attempt = None
_project_cache_lock = threading.Lock()
_task_cache = None
_task_capability = "unknown"
_task_cache_lock = threading.Lock()


def _project_account(token=None):
    # Scope persisted metadata to the credential without storing the credential.
    token = api_token if token is None else token
    identity = json.dumps([get_api_base(), token], separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest() if token else None


def _cached_projects():
    global _project_cache
    if _project_cache is None:
        _project_cache = _load_json_with_backup(STATE_DIR / "projects.json", {})
    if not isinstance(_project_cache, dict) or _project_cache.get("account") != _project_account():
        return []
    projects = _project_cache.get("projects", [])
    return projects if isinstance(projects, list) else []


def _cached_tasks():
    return toggl_tasks.cached(sys.modules[__name__])


def _refresh_tasks(force=False):
    return toggl_tasks.refresh(sys.modules[__name__], force=force)


def _refresh_projects(force=False):
    """Cache project metadata off the render thread, at most once an hour."""
    global _project_cache, _last_project_refresh_attempt
    if not api_token or _rate_limit_active() or _request_budget_remaining() <= REQUEST_BUDGET_BACKGROUND_RESERVE:
        return
    if not _project_cache_lock.acquire(blocking=False):
        return
    try:
        token = api_token
        account = _project_account(token)
        _cached_projects()
        now = time.monotonic()
        if not force:
            if _last_project_refresh_attempt is not None and now - _last_project_refresh_attempt < SYNC_INTERVAL_SECONDS:
                return
            if isinstance(_project_cache, dict) and _project_cache.get("account") == account:
                fetched_at = _project_cache.get("fetched_at")
                if isinstance(fetched_at, (int, float)) and 0 <= time.time() - fetched_at < CLOUD_POLL_INTERVAL_SECONDS:
                    return
        _last_project_refresh_attempt = now
        projects = _api("GET", "/me/projects", token=token)
        if not isinstance(projects, list):
            return
        cache = {
            "account": account, "fetched_at": time.time(),
            "projects": [{k: p.get(k) for k in ("id", "workspace_id", "name", "color", "active")}
                         for p in projects if isinstance(p, dict) and p.get("id") is not None],
        }
        _atomic_write_text(STATE_DIR / "projects.json", json.dumps(cache, indent=2))
        _project_cache = cache
    except Exception as error:
        print(f"Project colors unavailable: {error}", file=sys.stderr)
    finally:
        _project_cache_lock.release()


# Toggl's project palette; tasks without a project get a stable colour from it.
TASK_COLORS = ("#0b83d9", "#9e5bd9", "#d94182", "#e36a00", "#bf7000", "#2da608", "#06a893",
               "#c9806b", "#465bb3", "#990099", "#c7af14", "#566614", "#d92b2b", "#525266")


def _tracking_color():
    """The running project's colour, or a colour derived from the task when there is no project."""
    project_id = state.get("project_id")
    project = next((p for p in _cached_projects() if isinstance(p, dict)
                    and project_id is not None and str(p.get("id")) == str(project_id)
                    and str(p.get("workspace_id")) == str(state.get("workspace_id"))), {})
    color = project.get("color")
    if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        return color
    description = " ".join((state.get("description") or "").split())
    if project_id is not None or not description:
        return None
    digest = hashlib.sha256(description.encode()).digest()
    return TASK_COLORS[digest[0] % len(TASK_COLORS)]


def _current_icon_path():
    """Choose a cached icon without making any network requests."""
    _init_icons()
    if not state.get("tracking"):
        return _icon_path_inactive
    color = _tracking_color()
    if color is None:
        return _icon_path_active
    path = str(STATE_DIR / "icons" / f"project-{color[1:].lower()}.png")
    if not Path(path).exists():
        with Image.open(_icon_path_active) as original:
            tinted = Image.new("RGBA", original.size, color)
            tinted.putalpha(original.getchannel("A"))
            tinted.save(path)
    return path


def _update_tray_icon_once():
    global _last_tray_icon_path, _last_menu_key
    # Rebuild the menu when the running task changes, so its active dot follows the timer.
    key = (state.get("tracking"), state.get("project_id"), state.get("task_id"), state.get("description"))
    if key != _last_menu_key:
        if _last_menu_key is not None:
            _set_tray_menu(build_menu())
        _last_menu_key = key
    path = _current_icon_path()
    if path != _last_tray_icon_path:
        update_tray_icon()
        _last_tray_icon_path = path
    return False


def _init_icons():
    """Pre-render active/inactive icons to temp PNGs for appindicator."""
    global _icon_path_active, _icon_path_inactive
    if _icon_path_active is not None:
        return

    icon_path = Path(__file__).parent / "toggl_icon.webp"
    img = Image.open(icon_path).convert("RGBA").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)

    # Greyed out: desaturate + dim
    from PIL import ImageEnhance
    grey = img.convert("LA").convert("RGBA")
    r, g, b, a = grey.split()
    grey_rgb = ImageEnhance.Brightness(Image.merge("RGB", (r, g, b))).enhance(0.6)
    r2, g2, b2 = grey_rgb.split()
    inactive = Image.merge("RGBA", (r2, g2, b2, a))

    # Add padding to both
    def _pad(src):
        inner = ICON_SIZE - 2 * ICON_PADDING
        shrunk = src.resize((inner, inner), Image.LANCZOS)
        padded = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
        padded.paste(shrunk, (ICON_PADDING, ICON_PADDING))
        return padded

    _ensure_private_state_dir()
    icon_dir = STATE_DIR / "icons"
    icon_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    icon_dir.chmod(0o700)

    _icon_path_active = str(icon_dir / "active.png")
    _icon_path_inactive = str(icon_dir / "inactive.png")
    _pad(img).save(_icon_path_active)
    _pad(inactive).save(_icon_path_inactive)


def render_icon():
    """Return current icon as PIL image."""
    return Image.open(_current_icon_path())


def update_tray_icon():
    """Force appindicator to pick up the icon change."""
    path = _current_icon_path()
    if native_status_icon_ref:
        GLib.idle_add(_set_native_status_icon_path, path)
    elif icon_ref and hasattr(icon_ref, '_appindicator'):
        # Direct appindicator path update
        icon_ref._appindicator.set_icon_full(path, "Toggl")
    elif icon_ref:
        icon_ref.icon = render_icon()


def _set_native_status_icon_path(path):
    if native_status_icon_ref:
        native_status_icon_ref.set_icon_name(path)
    return False


# ── Elapsed time formatting ────────────────────────────────────────────────

def elapsed_str():
    if not state["tracking"] or not state["start_time"]:
        return "Not tracking"
    start = datetime.fromisoformat(state["start_time"])
    elapsed = datetime.now(timezone.utc) - start
    total_secs = int(elapsed.total_seconds())
    h, remainder = divmod(total_secs, 3600)
    m, s = divmod(remainder, 60)
    desc = state.get("description", "") or next((item.get("name") for item in _cached_tasks()
                                                  if str(item.get("id")) == str(state.get("task_id"))), "")
    time_str = f"{h}:{m:02d}:{s:02d}"
    if desc:
        return f"{desc} — {time_str}"
    return time_str


def get_tooltip():
    suffix = f" — {status_message}" if status_message else ""
    if state["tracking"]:
        return f"Toggl: {elapsed_str()}{suffix}"
    return f"Toggl: Stopped{suffix}"


def _panel_timer_label(panel=None, show_timer=True, show_description=False):
    """Build the compact text shown by panel implementations that support it."""
    panel = panel or _panel_timer_snapshot()
    parts = []
    if show_timer:
        parts.append(panel["elapsed"] if panel["tracking"] else "Stopped")
    if show_description:
        description = " ".join(str(panel.get("description") or "").split())
        if description:
            parts.append(description)
    return " · ".join(parts)


def _panel_timer_snapshot():
    with state_lock:
        tracking = state.get("tracking", False)
        start_time = state.get("start_time")
        description = state.get("description", "")
        task_id = state.get("task_id")
    if not description and task_id is not None:
        description = next((item.get("name") for item in _cached_tasks()
                            if str(item.get("id")) == str(task_id)), "")

    elapsed_seconds = 0
    if tracking and start_time:
        try:
            start = datetime.fromisoformat(start_time)
            elapsed_seconds = int((datetime.now(timezone.utc) - start).total_seconds())
        except (TypeError, ValueError):
            pass
    return {
        "tracking": tracking,
        "elapsed": _format_duration(elapsed_seconds),
        "description": description,
    }


def _update_panel_timer_label():
    """Update the optional native text label when the tray backend supports it."""
    label = _panel_timer_label(
        show_timer=get_panel_timer_enabled(),
        show_description=get_panel_description_enabled(),
    )
    if native_status_icon_ref:
        native_status_icon_ref.set_label(label)
        return True
    if not icon_ref:
        return False
    indicator = getattr(icon_ref, "_appindicator", None)
    set_label = getattr(indicator, "set_label", None)
    if not callable(set_label):
        return False

    try:
        set_label(label, PANEL_TIMER_LABEL_GUIDE)
    except TypeError:
        # XApp.StatusIcon exposes the same feature with a one-argument method.
        set_label(label)
    return True


def _update_panel_timer_label_once():
    """Update the panel label once when dispatched through GLib.idle_add."""
    _update_panel_timer_label()
    return False


def _native_status_icon_supported():
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    return XApp is not None and "cinnamon" in desktop and icon_ref is not None


def _update_native_status_menu():
    global native_status_menu_ref
    if not native_status_icon_ref or not icon_ref:
        return False
    create_menu = getattr(icon_ref, "_create_menu", None)
    if not callable(create_menu):
        return False
    native_status_menu_ref = create_menu(icon_ref.menu)
    # Left click opens the project/task popup; right click keeps the full menu.
    native_status_icon_ref.set_primary_menu(None)
    native_status_icon_ref.set_secondary_menu(native_status_menu_ref)
    return False


def _set_tray_menu(menu):
    if not icon_ref:
        return
    icon_ref.menu = menu
    if native_status_icon_ref:
        GLib.idle_add(_update_native_status_menu)


def _on_native_status_icon_press(_icon, x, y, button, _time, _position):
    if button == 1:
        import toggl_tray_popup
        GLib.idle_add(toggl_tray_popup.toggle, sys.modules[__name__], x, y)


def _init_native_status_icon():
    """Use Cinnamon's XApp icon so its panel can render a live text label."""
    global native_status_icon_ref
    if not _native_status_icon_supported():
        return False
    try:
        native_status_icon_ref = XApp.StatusIcon.new()
        path = _current_icon_path()
        native_status_icon_ref.set_icon_name(path)
        native_status_icon_ref.set_tooltip_text(get_tooltip())
        native_status_icon_ref.connect("button-press-event", _on_native_status_icon_press)
        _update_native_status_menu()
        _update_panel_timer_label()
        native_status_icon_ref.set_visible(True)
        return True
    except Exception as error:
        native_status_icon_ref = None
        print(f"Native Cinnamon panel label unavailable: {error}", file=sys.stderr)
        return False


# ── Toggle action ───────────────────────────────────────────────────────────

def _sync_cloud_state():
    """Sync local state with cloud. Skip if pending start/stop would conflict."""
    global rate_limited_until
    if not api_token:
        return
    pending = _active_pending()
    if any(item.get("action") in ("start", "stop") for item in pending):
        return
    try:
        with state_lock:
            before = dict(state)
        current = fetch_current_entry()
        with state_lock:
            if state != before:
                return  # A start, stop or switch happened during the request; its reply is stale.
            if current:
                state["tracking"] = True
                state["entry_id"] = current["id"]
                state["start_time"] = current["start"]
                state["description"] = current.get("description", "")
                state["project_id"] = current.get("project_id")
                state["task_id"] = current.get("task_id")
                state["workspace_id"] = current.get("workspace_id") or state.get("workspace_id")
            else:
                state["tracking"] = False
                state["entry_id"] = None
                state["start_time"] = None
            save_state()
    except RateLimitedError as e:
        rate_limited_until = time.monotonic() + e.retry_after
    except Exception as e:
        print(f"Cloud sync failed: {e}", file=sys.stderr)


def _rate_limit_active():
    return time.monotonic() < rate_limited_until


def _local_save_reason(action):
    if not api_token:
        return f"No Toggl API token — {action} saved locally"
    if _rate_limit_active():
        return f"Toggl rate limit active — {action} saved locally"
    if _request_budget_exhausted():
        return f"Toggl request budget exhausted — {action} saved locally"
    if not state.get("workspace_id"):
        return f"No Toggl workspace yet — {action} saved locally"
    return f"Offline — {action} queued locally"


def toggle_tracking(*_args, prompt_for_description=True):
    """Start or stop tracking locally, queuing failed cloud writes."""
    global icon_ref

    if not toggle_lock.acquire(blocking=False):
        _notify("Timer busy syncing or saving — try Start/Stop again")
        return

    try:
        with state_lock:
            is_tracking = state["tracking"]
            entry_id = state["entry_id"]
            workspace_id = state["workspace_id"]
            description = state.get("description", "")
            project_id = state.get("project_id")
            task_id = state.get("task_id")
            start_time = state.get("start_time")
        _append_event(
            "toggle_requested",
            source="tray",
            was_tracking=is_tracking,
            entry_id=entry_id,
            workspace_id=workspace_id,
            description=description,
        )

        if is_tracking:
            toggl_task_tree.remember(sys.modules[__name__], dict(state))
            # Stop
            now = datetime.now(timezone.utc).isoformat()
            if (
                entry_id and workspace_id and api_token
                and not _rate_limit_active()
                and not _request_budget_exhausted()
            ):
                try:
                    stop_entry(workspace_id, entry_id)
                    _clear_status_message()
                except RateLimitedError:
                    print("Stop rate limited, queuing locally", file=sys.stderr)
                    _notify(_local_save_reason("stop"))
                    queued = dict(entry_id=entry_id, start_time=start_time, stop_time=now,
                                  description=description, project_id=project_id, workspace_id=workspace_id)
                    if task_id is not None:
                        queued["task_id"] = task_id
                    queue_action("stop", **queued)
                except Exception as e:
                    print(f"Stop failed, queuing offline: {e}", file=sys.stderr)
                    _notify(_local_save_reason("stop"))
                    queued = dict(entry_id=entry_id, start_time=start_time, stop_time=now,
                                  description=description, project_id=project_id, workspace_id=workspace_id)
                    if task_id is not None:
                        queued["task_id"] = task_id
                    queue_action("stop", **queued)
            else:
                _notify(_local_save_reason("stop"))
                queued = dict(entry_id=entry_id, start_time=start_time, stop_time=now,
                              description=description, project_id=project_id, workspace_id=workspace_id)
                if task_id is not None:
                    queued["task_id"] = task_id
                queue_action("stop", **queued)
            with state_lock:
                state["tracking"] = False
                state["entry_id"] = None
                state["start_time"] = None
                save_state()
            import toggl_control
            toggl_control.record_stop(sys.modules[__name__], entry_id, start_time, description,
                                      workspace_id, project_id, task_id, now)
            _append_event("toggle_stopped", source="tray", entry_id=entry_id, stop_time=now)
            _play_sound(SOUND_STOP)
        else:
            # Start
            now = datetime.now(timezone.utc)
            entry = None
            if api_token and workspace_id and not _rate_limit_active() and not _request_budget_exhausted():
                try:
                    start_values = dict(description=description, project_id=project_id)
                    if task_id is not None:
                        start_values["task_id"] = task_id
                    entry = start_entry(workspace_id, **start_values)
                except RateLimitedError:
                    print("Start rate limited, queuing locally", file=sys.stderr)
                    _notify(_local_save_reason("start"))
                except Exception as e:
                    print(f"Start failed, queuing offline: {e}", file=sys.stderr)
                    _notify(_local_save_reason("start"))
            else:
                _notify(_local_save_reason("start"))

            if entry:
                with state_lock:
                    state["tracking"] = True
                    state["entry_id"] = entry["id"]
                    state["start_time"] = entry["start"]
                    save_state()
                _clear_status_message()
                _append_event(
                    "toggle_started",
                    source="tray",
                    entry_id=entry["id"],
                    start_time=entry["start"],
                    mode="cloud",
                )
            else:
                with state_lock:
                    state["tracking"] = True
                    state["entry_id"] = None
                    state["start_time"] = now.isoformat()
                    save_state()
                queued = dict(start_time=now.isoformat(), description=description, project_id=project_id)
                if task_id is not None:
                    queued["task_id"] = task_id
                queue_action("start", **queued)
                _append_event(
                    "toggle_started",
                    source="tray",
                    entry_id=None,
                    start_time=now.isoformat(),
                    mode="local",
                )
            _play_sound(SOUND_START)
            if prompt_for_description and not description and task_id is None:
                if icon_ref:
                    _prompt_for_description_async()
                else:
                    _notify("Tracking with no description — right-click tray icon to set one")

        if icon_ref:
            GLib.idle_add(_refresh_tray_menu)
    finally:
        toggle_lock.release()


# ── Notifications ───────────────────────────────────────────────────────────

SOUND_START = "/usr/share/sounds/freedesktop/stereo/bell.oga"
SOUND_STOP = "/usr/share/sounds/freedesktop/stereo/complete.oga"
SOUND_ERROR = "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"


def _reap(proc):
    """Wait on a fire-and-forget child in the background so it doesn't linger as a zombie."""
    threading.Thread(target=proc.wait, daemon=True).start()


def _notify(body):
    """Publish an in-app status without sending a desktop notification."""
    global status_message
    status_message = str(body or "").strip()
    if status_message:
        print(status_message, file=sys.stderr)
    if icon_ref:
        GLib.idle_add(_refresh_tray_menu)


def _clear_status_message():
    global status_message
    if not status_message:
        return
    status_message = ""
    if icon_ref:
        GLib.idle_add(_refresh_tray_menu)


def _play_sound(path):
    try:
        _reap(subprocess.Popen(
            ["paplay", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))
    except FileNotFoundError:
        pass


# ── GTK dialogs ─────────────────────────────────────────────────────────────

def _gtk_input_dialog(title, label_text, placeholder="", default=""):
    result = {"value": None}
    done = threading.Event()

    def run():
        dialog = Gtk.Dialog(title=title, modal=True)
        dialog.set_keep_above(True)
        dialog.set_resizable(True)
        dialog.set_default_size(420, -1)
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dialog.set_default_response(Gtk.ResponseType.OK)

        box = dialog.get_content_area()
        box.set_spacing(12)
        box.set_margin_start(18)
        box.set_margin_end(18)
        box.set_margin_top(16)
        box.set_margin_bottom(12)

        label = Gtk.Label(label=label_text)
        label.set_xalign(0)
        box.add(label)

        entry = Gtk.Entry()
        entry.set_placeholder_text(placeholder)
        entry.set_text(default)
        entry.set_activates_default(True)
        entry.set_width_chars(36)
        box.add(entry)

        dialog.show_all()
        entry.grab_focus()
        resp = dialog.run()
        if resp == Gtk.ResponseType.OK:
            result["value"] = entry.get_text().strip()
        dialog.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        done.set()

    GLib.idle_add(run)
    done.wait()
    return result["value"]


def _gtk_message(title, text):
    done = threading.Event()

    def run():
        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(text)
        dialog.set_keep_above(True)
        dialog.run()
        dialog.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        done.set()

    GLib.idle_add(run)
    done.wait()


def _gtk_confirm(title, text):
    result = {"confirmed": False}
    done = threading.Event()

    def run():
        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=title,
        )
        dialog.format_secondary_text(text)
        dialog.set_keep_above(True)
        resp = dialog.run()
        result["confirmed"] = resp == Gtk.ResponseType.YES
        dialog.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        done.set()

    GLib.idle_add(run)
    done.wait()
    return result["confirmed"]


# ── Menu callbacks ──────────────────────────────────────────────────────────


def on_toggle_panel_timer(icon, item):
    enabled = set_panel_timer_enabled(not get_panel_timer_enabled())
    if not _update_panel_timer_label() and enabled:
        _notify("This desktop's tray does not support a visible timer label")
    if icon_ref:
        _set_tray_menu(build_menu())


def on_toggle_panel_description(icon, item):
    set_panel_description_enabled(not get_panel_description_enabled())
    _update_panel_timer_label()
    if icon_ref:
        _set_tray_menu(build_menu())


def on_open_timer(icon, item):
    import toggl_timer_window
    GLib.idle_add(toggl_timer_window.show, sys.modules[__name__])


def on_toggle(icon, item):
    threading.Thread(target=toggle_tracking, daemon=True).start()


def _update_open_pending_description(desc):
    pending_entries = [
        entry for entry in _pending_as_entries()
        if entry.get("_pending_kind") == "open"
    ]
    if not pending_entries:
        return False
    return _update_pending_entry(pending_entries[-1], {"description": desc})


def _refresh_tray_menu():
    if not icon_ref:
        return False
    update_tray_icon()
    icon_ref.title = get_tooltip()
    _set_tray_menu(build_menu())
    return False


def _set_tray_tooltip(tooltip):
    """Apply GTK-backed icon text only from the GLib main loop."""
    if icon_ref:
        icon_ref.title = tooltip
    if native_status_icon_ref:
        native_status_icon_ref.set_tooltip_text(tooltip)
    return False


def _apply_description(desc):
    with state_lock:
        state["description"] = desc
        tracking = state.get("tracking")
        entry_id = state.get("entry_id")
        workspace_id = state.get("workspace_id")
        save_state()

    if tracking and entry_id and workspace_id:
        try:
            update_entry(workspace_id, entry_id, {"description": desc})
        except Exception:
            pass
    elif tracking:
        _update_open_pending_description(desc)

    if icon_ref:
        GLib.idle_add(_refresh_tray_menu)


def _show_description_dialog():
    """Show a non-blocking editor that remains responsive when opened from XApp."""
    dialog = Gtk.Dialog(title="Description", modal=True)
    dialog.set_keep_above(True)
    dialog.set_resizable(True)
    dialog.set_default_size(420, -1)
    dialog.add_buttons(
        Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
        Gtk.STOCK_OK, Gtk.ResponseType.OK,
    )
    dialog.set_default_response(Gtk.ResponseType.OK)

    box = dialog.get_content_area()
    box.set_spacing(12)
    box.set_margin_start(18)
    box.set_margin_end(18)
    box.set_margin_top(16)
    box.set_margin_bottom(12)

    label = Gtk.Label(label="Time entry description:")
    label.set_xalign(0)
    box.add(label)

    entry = Gtk.Entry()
    entry.set_placeholder_text("e.g. Client work")
    entry.set_text(state.get("description", ""))
    entry.set_width_chars(36)
    box.add(entry)

    def respond(_dialog, response):
        if response == Gtk.ResponseType.OK:
            worker = threading.Thread(
                target=_apply_description,
                args=(entry.get_text().strip(),),
                daemon=True,
            )
            worker.start()
        dialog.destroy()

    entry.connect("activate", lambda _entry: dialog.response(Gtk.ResponseType.OK))
    dialog.connect("response", respond)
    dialog.show_all()
    entry.grab_focus()
    return False


def _prompt_for_description_async():
    GLib.idle_add(_show_description_dialog)


def on_set_description(icon, item):
    _prompt_for_description_async()


def on_view_today(icon, item):
    import toggl_entries_window
    GLib.idle_add(toggl_entries_window._show_entries_window)


def _capture_cli_output(func, args):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = func(args)
    parts = [text.strip() for text in (stdout.getvalue(), stderr.getvalue()) if text.strip()]
    text = "\n\n".join(parts)
    if code not in (0, None):
        text = f"{text}\n\nExit code: {code}" if text else f"Exit code: {code}"
    return text or "No output."


def on_doctor(icon, item):
    def _do():
        try:
            import toggl_cli
            args = type("Args", (), {"cloud": False})()
            text = _capture_cli_output(toggl_cli._cli_doctor, args)
        except Exception as e:
            text = f"Doctor failed: {e}"
        _gtk_message("Toggl Doctor", text)

    threading.Thread(target=_do, daemon=True).start()


def on_audit_today(icon, item):
    def _do():
        try:
            import toggl_cli
            today = datetime.now().date().isoformat()
            args = type("Args", (), {
                "date_from": today,
                "date_to": today,
                "local_only": False,
                "gap_minutes": 120,
            })()
            text = _capture_cli_output(toggl_cli._cli_audit, args)
        except Exception as e:
            text = f"Audit failed: {e}"
        _gtk_message("Toggl Audit Today", text)

    threading.Thread(target=_do, daemon=True).start()


def _update_pending_entry(entry, update):
    with pending_lock:
        queue = _load_pending()
        candidate = _pending_entry_candidate(queue, entry)
        if candidate is None:
            return False
        indexes = set(candidate["_pending_indexes"])

        for idx in indexes:
            item = queue[idx]
            action = item.get("action")
            if item.get('_verify_running') and not item.get('_verify_snapshot'):
                item['_verify_snapshot'] = _stop_verification_snapshot(
                    item, (item.get('_task_context') or {}).get('account'))
            item.pop('_review_error', None)

            if "description" in update:
                item["description"] = update["description"]
            if "start" in update and (action == "start" or "start_time" in item or len(indexes) == 1):
                item["start_time"] = update["start"]
            if "stop" in update and (action == "stop" or "stop_time" in item or (len(indexes) == 1 and action == "start")):
                item["stop_time"] = update["stop"]

        _save_pending(queue)
        return True


def _delete_pending_entry(entry):
    with pending_lock:
        queue = _load_pending()
        candidate = _pending_entry_candidate(queue, entry)
        if candidate is None:
            return False
        indexes = set(candidate["_pending_indexes"])

        remaining = [item for idx, item in enumerate(queue) if idx not in indexes]
        remote_id = entry.get("_remote_entry_id")
        if remote_id and not candidate.get('_review_error'):
            remaining.append({
                "action": "delete",
                "ts": datetime.now(timezone.utc).isoformat(),
                "_queue_id": uuid.uuid4().hex,
                "entry_id": remote_id,
                "workspace_id": entry.get("workspace_id") or state.get("workspace_id"),
            })
        _save_pending(remaining)

    if entry.get("_pending_kind") == "open" and state.get("start_time") == entry.get("start"):
        state["tracking"] = False
        state["entry_id"] = None
        state["start_time"] = None
        save_state()
    return True


def _parse_hhmm(text, reference_dt):
    """Parse 'HH:MM' into a datetime using reference_dt's date and timezone."""
    try:
        parts = text.split(":")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return None
        return reference_dt.replace(hour=h, minute=m, second=0, microsecond=0)
    except (ValueError, IndexError):
        return None


def on_set_token(icon, item):
    def _do():
        token = _gtk_input_dialog("API Token", "Enter Toggl/OpenTickly API token:", placeholder="hex token")
        if token:
            global api_token
            with toggle_lock:
                api_token = token
                store_api_token(token)
                _init_workspace()

    threading.Thread(target=_do, daemon=True).start()


def on_quit(icon, item):
    save_state()
    if native_status_icon_ref:
        native_status_icon_ref.set_visible(False)
    icon.stop()


# ── Menu ────────────────────────────────────────────────────────────────────

def _apply_tray_project(project_id, expected, context=None):
    import toggl_control
    with state_lock:
        if (state == expected and str(state.get("project_id")) == str(project_id)
                and state.get("task_id") is None):
            return True
    try:
        payload = {"changes": {"project_id": project_id, "task_id": None}, "expected": expected}
        if context is not None:
            payload["context"] = context
        toggl_control.dispatch(sys.modules[__name__], "edit-current", payload)
        return True
    except Exception as error:
        _notify(f"Project not changed: {error}")
        if icon_ref:
            GLib.idle_add(_refresh_project_menu)
        return False


def _refresh_project_menu():
    if icon_ref:
        _set_tray_menu(build_menu())
    return False


def _project_choice_action(project_id, context, account_context=None):
    def selected(_icon, _item):
        with state_lock:
            expected = dict(state)
        if str(expected.get("workspace_id")) != str(context.get("workspace_id")):
            _notify("Project list changed; reopen Project and choose again")
            if icon_ref:
                GLib.idle_add(_refresh_project_menu)
            return
        threading.Thread(target=_apply_tray_project, args=(project_id, expected, account_context), daemon=True).start()
    return selected


def on_refresh_tray_projects(_icon, _item):
    def refresh():
        _refresh_projects(force=True)
        _refresh_tasks(force=True)
        toggl_task_tree.refresh(sys.modules[__name__], force=True)
        GLib.idle_add(_refresh_project_menu)
    threading.Thread(target=refresh, daemon=True).start()


def on_new_project(_icon, _item):
    import toggl_project_window
    GLib.idle_add(toggl_project_window.show, sys.modules[__name__])


def on_edit_selected_project(_icon, _item):
    import toggl_project_window
    GLib.idle_add(toggl_project_window.edit_selected, sys.modules[__name__])


def on_manage_projects(_icon, _item):
    import toggl_project_window
    GLib.idle_add(toggl_project_window.manage, sys.modules[__name__])


def on_open_task_popup(_icon, _item):
    import toggl_tray_popup
    GLib.idle_add(toggl_tray_popup.toggle_at_pointer, sys.modules[__name__])


def _tray_project_items():
    """Project choices and project management; tasks are picked in the popup."""
    import toggl_projects
    with state_lock:
        current = dict(state)
    context = toggl_projects.context(sys.modules[__name__])
    projects = [p for p in _cached_projects() if isinstance(p, dict)
                and str(p.get("workspace_id")) == str(current.get("workspace_id"))
                and (p.get("active") is not False or str(p.get("id")) == str(current.get("project_id")))]
    def choice(project_id, name):
        return pystray.MenuItem(name, _project_choice_action(project_id, current, context), radio=True,
                               checked=lambda _item: str(state.get("workspace_id")) == str(current.get("workspace_id"))
                               and str(state.get("project_id")) == str(project_id))
    items = [choice(None, "No project")]
    items.extend(choice(p["id"], p.get("name") or str(p["id"])) for p in projects)
    if current.get("project_id") is not None and not any(str(p["id"]) == str(current["project_id"]) for p in projects):
        items.append(choice(current["project_id"], "Current project (not cached)"))
    items.extend((pystray.Menu.SEPARATOR,
                  pystray.MenuItem("New Project…", on_new_project),
                  pystray.MenuItem("Edit Selected Project…", on_edit_selected_project,
                                   enabled=current.get("project_id") is not None and any(
                                       str(p["id"]) == str(current["project_id"]) for p in projects)),
                  pystray.MenuItem("Manage Projects…", on_manage_projects),
                  pystray.MenuItem("Refresh projects and tasks", on_refresh_tray_projects)))
    return items


def build_menu():
    toggle_label = "Stop tracking" if state["tracking"] else "Start tracking"
    status_items = ()
    if status_message:
        status_items = (
            pystray.MenuItem(f"Status: {status_message}", lambda _icon, _item: None, enabled=False),
            pystray.Menu.SEPARATOR,
        )
    return pystray.Menu(
        *status_items,
        pystray.MenuItem("Projects, tasks, and activities…", on_open_task_popup),
        pystray.MenuItem("Open timer", on_open_timer, default=True),
        pystray.MenuItem("Project", pystray.Menu(*_tray_project_items())),
        pystray.MenuItem(
            "Show timer in panel",
            on_toggle_panel_timer,
            checked=lambda _item: get_panel_timer_enabled(),
        ),
        pystray.MenuItem(
            "Show description in panel",
            on_toggle_panel_description,
            checked=lambda _item: get_panel_description_enabled(),
        ),
        pystray.MenuItem(toggle_label, on_toggle),
        pystray.MenuItem("Edit description...", on_set_description),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Today's entries", on_view_today),
        pystray.MenuItem("Doctor", on_doctor),
        pystray.MenuItem("Audit today", on_audit_today),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Set API token...", on_set_token),
        pystray.MenuItem("Quit", on_quit),
    )


# ── Global hotkey ───────────────────────────────────────────────────────────

def _report_hotkey_failure(error):
    print(f"Hotkey listener failed: {error}", file=sys.stderr)
    _notify("Toggl hotkey unavailable — use tray menu or terminal commands")
    _play_sound(SOUND_ERROR)


def _run_hotkey_listener(display_factory=Display):
    """Grab Ctrl+Shift+T globally via X11. Returns False if setup fails."""
    try:
        dpy = display_factory()
        x_errors = []

        def _x_error_handler(error, _request):
            x_errors.append(error)

        if hasattr(dpy, "set_error_handler"):
            dpy.set_error_handler(_x_error_handler)

        root = dpy.screen().root
        keycode = dpy.keysym_to_keycode(XK.string_to_keysym("T"))

        # Grab with all modifier combos that include Ctrl+Shift
        # (NumLock=Mod2, CapsLock=Lock, ScrollLock=Mod3 can be on too)
        CTRL_SHIFT = X.ControlMask | X.ShiftMask
        IGNORE_MASKS = [0, X.Mod2Mask, X.LockMask, X.Mod2Mask | X.LockMask]

        for extra in IGNORE_MASKS:
            root.grab_key(
                keycode,
                CTRL_SHIFT | extra,
                True,           # owner_events
                X.GrabModeAsync,
                X.GrabModeAsync,
            )

        if hasattr(dpy, "sync"):
            dpy.sync()
        if x_errors:
            raise RuntimeError(x_errors[0])

        while True:
            event = dpy.next_event()
            if event.type == X.KeyPress:
                threading.Thread(target=toggle_tracking, daemon=True).start()
    except Exception as e:
        _report_hotkey_failure(e)
        return False


def start_hotkey_listener():
    """Start the global hotkey listener thread."""
    def _grab_loop():
        _run_hotkey_listener()

    t = threading.Thread(target=_grab_loop, daemon=True)
    t.start()


# ── Update loop ─────────────────────────────────────────────────────────────

def update_loop(stop_event=None):
    """Update icon tooltip and optional panel label with elapsed time."""
    while True:
        if stop_event is not None:
            if stop_event.wait(1):
                return
        else:
            time.sleep(1)
        if icon_ref:
            tooltip = get_tooltip()
            GLib.idle_add(_set_tray_tooltip, tooltip)
            GLib.idle_add(_update_panel_timer_label_once)
            GLib.idle_add(_update_tray_icon_once)


# ── Command-line recovery tools ─────────────────────────────────────────────

def _format_duration(seconds):
    seconds = max(int(seconds or 0), 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _entry_duration_seconds(entry):
    duration = entry.get("duration", 0) or 0
    if duration < 0:
        duration = int(time.time()) + duration
    return max(int(duration), 0)


# ── Init ────────────────────────────────────────────────────────────────────

def _acquire_instance_lock():
    _ensure_private_state_dir()
    lock_fp = open(LOCK_FILE, "a+")
    LOCK_FILE.chmod(0o600)
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fp.seek(0)
        pid = lock_fp.read().strip()
        detail = f" (PID {pid})" if pid else ""
        print(f"Another instance is already running{detail}.", file=sys.stderr)
        print("Stop that process before starting another copy; do not delete the lock file.", file=sys.stderr)
        sys.exit(1)
    lock_fp.seek(0)
    lock_fp.truncate()
    lock_fp.write(str(os.getpid()))
    lock_fp.flush()
    return lock_fp


def _init_workspace():
    """Fetch workspace and project info, sync running entry."""
    global api_token, rate_limited_until, _last_cloud_poll_at, _restored_remote_id
    if not api_token:
        return False
    token, base = api_token, get_api_base()
    with state_lock:
        before = dict(state)
    pending = _active_pending()
    has_conflicting_pending = any(
        item.get("action") in ("start", "stop") for item in pending
    )
    if has_conflicting_pending and before.get("workspace_id"):
        return True

    try:
        workspace = before.get('workspace_id')
        if not workspace:
            workspace = fetch_me(token=token, base_url=base)['default_workspace_id']
        current = None if has_conflicting_pending else fetch_current_entry(token=token, base_url=base)
        recent = []
        if not has_conflicting_pending and not current and not before.get('description'):
            try:
                recent = api_get('/me/time_entries', token=token, base_url=base)
            except Exception:
                pass
        with state_lock:
            if state != before or api_token != token or get_api_base() != base:
                return False  # User work or an account change won while the request ran.
            state['workspace_id'] = workspace
            if not has_conflicting_pending:
                if current:
                    state.update(tracking=True, entry_id=current['id'], start_time=current['start'],
                                 description=current.get('description', ''), project_id=current.get('project_id'),
                                 task_id=current.get('task_id'), workspace_id=current.get('workspace_id') or workspace)
                else:
                    state.update(tracking=False, entry_id=None, start_time=None)
                    if isinstance(recent, list) and recent:
                        state['description'] = recent[0].get('description') or state.get('description', '')
                        if not state.get('project_id'):
                            state['project_id'] = recent[0].get('project_id')
            save_state()
            _restored_remote_id = None
        _last_cloud_poll_at = time.monotonic()
        return True
    except RateLimitedError as e:
        rate_limited_until = time.monotonic() + e.retry_after
        return False
    except Exception as e:
        print(f"Init failed: {e}", file=sys.stderr)
        return False


def _begin_startup_scope():
    global _startup_scope
    _startup_scope = dict(base=get_api_base(), account=state.get('_account'),
                          workspace=state.get('workspace_id'), queue_ids=set())


def _accept_keyring_token(token, lookup_base):
    global api_token
    with toggle_lock:
        if token and not api_token and get_api_base() == lookup_base:
            api_token = token
            if _startup_scope is not None and _startup_scope['base'] == lookup_base:
                import toggl_projects
                _startup_scope['resolved_account'] = toggl_projects.account(sys.modules[__name__])


def _finish_startup_scope():
    """Bind only work created in this launch to its unchanged keyring identity.

    Call under toggle_lock after workspace discovery, before replaying the queue.
    """
    global _startup_scope
    if _startup_scope is None or not api_token:
        return
    import toggl_projects
    scope = toggl_projects.context(sys.modules[__name__])
    startup = _startup_scope
    if (startup['base'] != get_api_base() or startup.get('resolved_account') != scope['account']
            or startup['account'] not in (None, scope['account'])
            or startup['workspace'] not in (None, scope['workspace_id'])):
        _startup_scope = None
        return
    if not scope['workspace_id']:
        return
    starts = set()
    with pending_lock:
        queue = _load_pending()
        changed = False
        for item in queue:
            context = item.get('_task_context') or {}
            if (item.get('_queue_id') in startup['queue_ids']
                    and context.get('account') in (None, scope['account'])
                    and context.get('workspace_id') in (None, scope['workspace_id'])):
                item['_task_context'] = scope
                item['workspace_id'] = scope['workspace_id']
                verification = item.get('_verify_snapshot')
                if isinstance(verification, dict) and verification.get('account') is None:
                    verification['account'] = scope['account']
                changed = True
                if item.get('action') == 'start':
                    starts.add(item.get('start_time'))
        if changed:
            _save_pending(queue)
        _startup_scope = None
    with state_lock:
        if state.get('tracking') and state.get('start_time') in starts and state.get('_account') is None:
            state['_account'] = scope['account']
            save_state()
    for entry in _pending_as_entries():
        if entry.get('start') in starts and entry.get('_task_context') == scope:
            toggl_task_tree.remember(sys.modules[__name__], dict(
                tracking=True, start_time=entry['start'], description=entry.get('description', ''),
                workspace_id=entry.get('workspace_id'), project_id=entry.get('project_id'),
                task_id=entry.get('task_id'), _account=scope['account']))


def _bootstrap_and_sync(stop_event):
    """Keyring and cloud setup run after the tray event loop becomes ready."""
    global api_token
    try:
        lookup_base = get_api_base()
        token = get_api_token()
        if stop_event.is_set():
            return
        _accept_keyring_token(token, lookup_base)
        if api_token:
            _init_workspace()
            with toggle_lock:
                _finish_startup_scope()
        else:
            print('No API token found; saved timers remain available offline', file=sys.stderr)
        _refresh_projects()
        _refresh_tasks()
        toggl_task_tree.refresh(sys.modules[__name__])
        GLib.idle_add(_refresh_project_menu)
    except Exception as error:
        _record_sync_failure(error)
    if not stop_event.is_set():
        sync_loop(stop_event)


def main():
    global icon_ref, api_token

    import toggl_cli
    cli_result = toggl_cli._run_cli(sys.argv[1:])
    if cli_result is not None:
        return cli_result

    # Single instance lock
    lock_fp = _acquire_instance_lock()
    try:
        debug_log = toggl_debug.DebugLog(STATE_DIR, lambda: [api_token, os.environ.get('TOGGL_API_TOKEN')])
        debug_log.capture_glib(GLib)
    except OSError as error:
        print(f'Debug logging unavailable: {error}', file=sys.stderr)
        debug_log = None

    # Init GTK for thread safety
    GLib.threads_init = lambda: None  # already init'd by import
    Gtk.init([])

    load_state()
    _begin_startup_scope()
    toggl_debug.LOGGER.info('Tray starting tracking=%s entry_id=%s pending_count=%s',
                            state.get('tracking'), state.get('entry_id'), len(_load_pending()))

    import toggl_control
    control_server = toggl_control.start_server(sys.modules[__name__])
    stop_event = threading.Event()

    # Start hotkey listener
    start_hotkey_listener()

    # Start update thread
    updater = threading.Thread(target=update_loop, args=(stop_event,), daemon=True)
    updater.start()

    # Create and run tray icon
    icon_ref = pystray.Icon("toggl-tray", render_icon(), get_tooltip(), build_menu())
    native = _init_native_status_icon()
    import toggl_watchdog
    watchdog = toggl_watchdog.UIWatchdog()
    watchdog.start(GLib)
    def ready(icon):
        if not native:
            icon.visible = True
        threading.Thread(target=_bootstrap_and_sync, args=(stop_event,),
                         name='cloud-sync', daemon=True).start()
    try:
        _update_panel_timer_label()
        icon_ref.run(setup=ready)
    finally:
        stop_event.set()
        _sync_wakeup.set()
        watchdog.stop()
        control_server.shutdown()
        control_server.server_close()
        lock_fp.close()
        toggl_debug.LOGGER.info('Tray stopped')
        if debug_log is not None:
            debug_log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
