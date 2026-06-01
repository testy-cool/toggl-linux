#!/usr/bin/env python3
"""Toggl Track tray timer — Ctrl+Shift+T to toggle tracking."""

import os
import sys
import json
import time
import fcntl
import io
import argparse
import shutil
import contextlib
import threading
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth
from PIL import Image
import pystray
from Xlib import X, XK
from Xlib.display import Display
from Xlib.ext import record
from Xlib.protocol import rq
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

# ── Config ──────────────────────────────────────────────────────────────────

API_BASE = "https://api.track.toggl.com/api/v9"
STATE_DIR = Path.home() / ".local" / "share" / "toggl-tray"
STATE_FILE = STATE_DIR / "state.json"
PENDING_FILE = STATE_DIR / "pending.json"
LEDGER_FILE = STATE_DIR / "events.jsonl"
REQUEST_BUDGET_FILE = STATE_DIR / "request_budget.json"
LOCK_FILE = STATE_DIR / "toggl-tray.lock"
APPLICATIONS_DIR = Path.home() / ".local" / "share" / "applications"
DESKTOP_FILE = APPLICATIONS_DIR / "toggl-tray.desktop"
AUTOSTART_DIR = Path.home() / ".config" / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "toggl-tray.desktop"
ICON_SIZE = 64
ICON_PADDING = 6  # transparent padding around the icon
SYNC_INTERVAL_SECONDS = 300
CLOUD_POLL_INTERVAL_SECONDS = 3600
NOTIFY_REPEAT_SECONDS = 3600
NO_TOKEN_SYNC_MESSAGE = "No Toggl API token — pending entries cannot sync"
AUTH_FAILED_SYNC_MESSAGE = "Toggl auth failed — pending entries kept locally"
REQUEST_BUDGET_LIMIT = 30
REQUEST_BUDGET_WINDOW_SECONDS = 3600
REQUEST_BUDGET_BACKGROUND_RESERVE = 6
REQUEST_BUDGET_SYNC_MESSAGE = "Toggl request budget exhausted — pending sync paused"
SYNC_CONFLICT_MESSAGE = "Toggl conflict — local start kept pending"
OPEN_START_MATCH_TOLERANCE_SECONDS = 300


# ── State ───────────────────────────────────────────────────────────────────

state = {
    "tracking": False,
    "entry_id": None,
    "start_time": None,  # ISO string
    "workspace_id": None,
    "project_id": None,
    "description": "",
}

icon_ref = None
api_token = None
rate_limited_until = 0.0
_request_budget_lock = threading.Lock()


class RateLimitedError(Exception):
    """Raised when Toggl asks us to wait before making more requests."""

    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__(f"Rate limited for {retry_after} seconds")


class MissingApiTokenError(Exception):
    """Raised when an API call is attempted without a configured token."""


# ── API Token ───────────────────────────────────────────────────────────────

def get_api_token():
    """Get Toggl API token from env or GNOME keyring."""
    token = os.environ.get("TOGGL_API_TOKEN")
    if token:
        return token
    lookups = (
        ["secret-tool", "lookup", "service", "toggl", "username", "api_token"],
        ["secret-tool", "lookup", "application", "toggl-tray"],
    )
    for cmd in lookups:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            break
    return None


def store_api_token(token):
    """Store token in GNOME keyring."""
    try:
        subprocess.run(
            ["secret-tool", "store", "--label=Toggl API Token",
             "service", "toggl", "username", "api_token",
             "application", "toggl-tray"],
            input=token, text=True, timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


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
    return _request_budget_status(now=now)["remaining"]


def _request_budget_exhausted():
    return _request_budget_remaining() <= 0


# ── Toggl API ───────────────────────────────────────────────────────────────

def _auth():
    return HTTPBasicAuth(api_token, "api_token")


def _api(method, path, json=None, params=None):
    """Single API wrapper. 429s are deferred instead of retried inline."""
    global rate_limited_until
    if not api_token:
        raise MissingApiTokenError("No Toggl API token configured")
    _record_api_request()
    r = requests.request(
        method, f"{API_BASE}{path}",
        auth=_auth(), json=json, params=params, timeout=10,
    )
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


def api_post(path, data):
    return _api("POST", path, json=data)


def api_put(path, data):
    return _api("PUT", path, json=data)


def api_patch(path, data):
    return _api("PATCH", path, json=data)


def api_delete(path):
    _api("DELETE", path)


def fetch_me():
    return api_get("/me")


def fetch_current_entry():
    """Get currently running time entry, or None."""
    data = api_get("/me/time_entries/current")
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


def start_entry(workspace_id, description="", project_id=None, start_time=None):
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


def update_entry(workspace_id, entry_id, data):
    return api_put(f"/workspaces/{workspace_id}/time_entries/{entry_id}", data)


def delete_entry(workspace_id, entry_id):
    return api_delete(f"/workspaces/{workspace_id}/time_entries/{entry_id}")


# ── Local persistence ──────────────────────────────────────────────────────

def _backup_path(path):
    return path.with_name(path.name + ".bak")


def _atomic_write_text(path, text, backup=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if backup and path.exists():
        try:
            existing = path.read_text()
            json.loads(existing)
            _backup_path(path).write_text(existing)
        except (json.JSONDecodeError, OSError):
            pass
    tmp.write_text(text)
    os.replace(tmp, path)


def _load_json_with_backup(path, default):
    for candidate in (path, _backup_path(path)):
        if candidate.exists():
            try:
                return json.loads(candidate.read_text())
            except (json.JSONDecodeError, OSError):
                continue
    return default


def _append_event(event, **data):
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **data,
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER_FILE.open("a") as fp:
        fp.write(json.dumps(record, sort_keys=True) + "\n")


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
    _atomic_write_text(STATE_FILE, json.dumps(state, indent=2))


def load_state():
    saved = _load_json_with_backup(STATE_FILE, None)
    if isinstance(saved, dict):
        state.update(saved)


# ── Offline queue ──────────────────────────────────────────────────────────

pending_lock = threading.Lock()
toggle_lock = threading.Lock()
state_lock = threading.Lock()


def _load_pending():
    pending = _load_json_with_backup(PENDING_FILE, [])
    return pending if isinstance(pending, list) else []


def _save_pending(queue):
    _atomic_write_text(PENDING_FILE, json.dumps(queue, indent=2))


def queue_action(action, **kwargs):
    """Queue an offline action for later sync."""
    if "workspace_id" not in kwargs and state.get("workspace_id"):
        kwargs["workspace_id"] = state["workspace_id"]
    with pending_lock:
        queue = _load_pending()
        item = {"action": action, "ts": datetime.now(timezone.utc).isoformat(), **kwargs}
        queue.append(item)
        _append_event("pending_queued", **item)
        _save_pending(queue)


def _parse_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _duration_seconds(start_time, stop_time):
    start = _parse_iso(start_time)
    stop = _parse_iso(stop_time)
    if not start or not stop:
        return None
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
    return payload


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
                    "start_time": item.get("start_time"),
                    "stop_time": stop_time,
                    "duration": _duration_seconds(item.get("start_time"), stop_time),
                    "description": item.get("description"),
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


def sync_pending():
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
                                save_state()
                            synced_indexes.update(op["indexes"])
                        else:
                            _report_open_start_conflict(current, op)
                    else:
                        entry = start_entry(
                            workspace_id,
                            description=op.get("description", ""),
                            project_id=op.get("project_id"),
                            start_time=op.get("start_time"),
                        )
                        with state_lock:
                            state["tracking"] = True
                            state["entry_id"] = entry["id"]
                            state["start_time"] = entry.get("start", op.get("start_time"))
                            state["description"] = op.get("description", "")
                            state["project_id"] = op.get("project_id")
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
                    try:
                        update_entry(workspace_id, op["entry_id"], data)
                    except requests.exceptions.HTTPError as e:
                        status = e.response.status_code if e.response is not None else 0
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


def _run_sync_cycle(now=None):
    """Run one background sync pass while preserving API quota for user actions."""
    global _last_cloud_poll_at
    now = time.monotonic() if now is None else now
    if now < rate_limited_until:
        return

    pending = _load_pending()
    if pending:
        if _request_budget_exhausted():
            _notify(REQUEST_BUDGET_SYNC_MESSAGE)
        else:
            left = sync_pending()
            if left == 0 and PENDING_FILE.exists():
                PENDING_FILE.unlink(missing_ok=True)
    elif api_token and now - _last_cloud_poll_at >= CLOUD_POLL_INTERVAL_SECONDS:
        if _request_budget_remaining() > REQUEST_BUDGET_BACKGROUND_RESERVE:
            _sync_cloud_state()
            _last_cloud_poll_at = now

    _health_check()
    _record_sync_success()


def sync_loop():
    """Background thread: retry pending actions and sync cloud state."""
    while True:
        time.sleep(SYNC_INTERVAL_SECONDS)
        try:
            _run_sync_cycle()
        except Exception as e:
            _record_sync_failure(e)


def _health_check():
    """Detect and recover from tracking-without-cloud-backing."""
    if not api_token:
        return
    if _request_budget_remaining() <= REQUEST_BUDGET_BACKGROUND_RESERVE:
        return
    with state_lock:
        tracking = state["tracking"]
        entry_id = state["entry_id"]
    if not tracking or entry_id is not None:
        return
    if _load_pending():
        return
    try:
        current = fetch_current_entry()
        with state_lock:
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


def _pending_as_entries(date_obj=None):
    """Convert pending offline actions into entry-like dicts for display."""
    queue = _load_pending()
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

        entry_id = op.get("entry_id") or f"pending:{'-'.join(str(i) for i in op['indexes'])}"
        entries.append({
            "id": entry_id,
            "start": start_time,
            "stop": stop_time,
            "duration": duration,
            "description": op.get("description", ""),
            "workspace_id": op.get("workspace_id") or state.get("workspace_id"),
            "_offline": True,
            "_pending_indexes": op["indexes"],
            "_pending_kind": op["kind"],
            "_remote_entry_id": op.get("entry_id"),
        })

    if date_obj is None:
        return entries
    return [e for e in entries if _entry_local_date(e) == date_obj]


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

    icon_dir = STATE_DIR / "icons"
    icon_dir.mkdir(parents=True, exist_ok=True)

    _icon_path_active = str(icon_dir / "active.png")
    _icon_path_inactive = str(icon_dir / "inactive.png")
    _pad(img).save(_icon_path_active)
    _pad(inactive).save(_icon_path_inactive)


def render_icon():
    """Return current icon as PIL image."""
    _init_icons()
    return Image.open(_icon_path_active if state["tracking"] else _icon_path_inactive)


def update_tray_icon():
    """Force appindicator to pick up the icon change."""
    _init_icons()
    if icon_ref and hasattr(icon_ref, '_appindicator'):
        # Direct appindicator path update
        path = _icon_path_active if state["tracking"] else _icon_path_inactive
        icon_ref._appindicator.set_icon_full(path, "Toggl")
    elif icon_ref:
        icon_ref.icon = render_icon()


# ── Elapsed time formatting ────────────────────────────────────────────────

def elapsed_str():
    if not state["tracking"] or not state["start_time"]:
        return "Not tracking"
    start = datetime.fromisoformat(state["start_time"])
    elapsed = datetime.now(timezone.utc) - start
    total_secs = int(elapsed.total_seconds())
    h, remainder = divmod(total_secs, 3600)
    m, s = divmod(remainder, 60)
    desc = state.get("description", "")
    time_str = f"{h}:{m:02d}:{s:02d}"
    if desc:
        return f"{desc} — {time_str}"
    return time_str


def get_tooltip():
    if state["tracking"]:
        return f"Toggl: {elapsed_str()}"
    return "Toggl: Stopped"


# ── Toggle action ───────────────────────────────────────────────────────────

def _sync_cloud_state():
    """Sync local state with cloud. Skip if pending start/stop would conflict."""
    global rate_limited_until
    if not api_token:
        return
    pending = _load_pending()
    if any(item.get("action") in ("start", "stop") for item in pending):
        return
    try:
        current = fetch_current_entry()
        with state_lock:
            if current:
                state["tracking"] = True
                state["entry_id"] = current["id"]
                state["start_time"] = current["start"]
                state["description"] = current.get("description", "")
                state["project_id"] = current.get("project_id")
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


def toggle_tracking(*_args):
    """Start or stop tracking locally, queuing failed cloud writes."""
    global icon_ref

    if not toggle_lock.acquire(blocking=False):
        return

    try:
        with state_lock:
            is_tracking = state["tracking"]
            entry_id = state["entry_id"]
            workspace_id = state["workspace_id"]
            description = state.get("description", "")
            project_id = state.get("project_id")
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
            # Stop
            now = datetime.now(timezone.utc).isoformat()
            if (
                entry_id and workspace_id and api_token
                and not _rate_limit_active()
                and not _request_budget_exhausted()
            ):
                try:
                    stop_entry(workspace_id, entry_id)
                except RateLimitedError:
                    print("Stop rate limited, queuing locally", file=sys.stderr)
                    _notify(_local_save_reason("stop"))
                    queue_action("stop", entry_id=entry_id,
                                 start_time=start_time, stop_time=now,
                                 description=description)
                except Exception as e:
                    print(f"Stop failed, queuing offline: {e}", file=sys.stderr)
                    _notify(_local_save_reason("stop"))
                    queue_action("stop", entry_id=entry_id,
                                 start_time=start_time, stop_time=now,
                                 description=description)
            else:
                _notify(_local_save_reason("stop"))
                queue_action("stop", entry_id=entry_id,
                             start_time=start_time, stop_time=now,
                             description=description)
            with state_lock:
                state["tracking"] = False
                state["entry_id"] = None
                state["start_time"] = None
                save_state()
            _append_event("toggle_stopped", source="tray", entry_id=entry_id, stop_time=now)
            _play_sound(SOUND_STOP)
        else:
            # Start
            now = datetime.now(timezone.utc)
            entry = None
            if api_token and workspace_id and not _rate_limit_active() and not _request_budget_exhausted():
                try:
                    entry = start_entry(
                        workspace_id,
                        description=description,
                        project_id=project_id,
                    )
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
                queue_action("start", start_time=now.isoformat(),
                             description=description,
                             project_id=project_id)
                _append_event(
                    "toggle_started",
                    source="tray",
                    entry_id=None,
                    start_time=now.isoformat(),
                    mode="local",
                )
            _play_sound(SOUND_START)
            if not description:
                _notify("Tracking with no description — right-click to set one")

        if icon_ref:
            update_tray_icon()
            icon_ref.title = get_tooltip()
            icon_ref.menu = build_menu()
    finally:
        toggle_lock.release()


# ── Notifications ───────────────────────────────────────────────────────────

SOUND_START = "/usr/share/sounds/freedesktop/stereo/bell.oga"
SOUND_STOP = "/usr/share/sounds/freedesktop/stereo/complete.oga"
SOUND_ERROR = "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"

_last_notification_at = {}


def _notification_allowed(body, now=None):
    now = time.monotonic() if now is None else now
    previous = _last_notification_at.get(body)
    if previous is not None and now - previous < NOTIFY_REPEAT_SECONDS:
        return False
    _last_notification_at[body] = now
    return True


def _notify(body):
    if not _notification_allowed(body):
        return
    try:
        subprocess.Popen(
            ["notify-send", "-a", "Toggl Tray", "Toggl Tray", body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _play_sound(path):
    try:
        subprocess.Popen(
            ["paplay", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


# ── GTK dialogs ─────────────────────────────────────────────────────────────

def _gtk_input_dialog(title, label_text, placeholder="", default=""):
    result = {"value": None}
    done = threading.Event()

    def run():
        dialog = Gtk.Dialog(title=title, modal=True)
        dialog.set_keep_above(True)
        dialog.set_resizable(False)
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dialog.set_default_response(Gtk.ResponseType.OK)

        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(8)

        label = Gtk.Label(label=label_text)
        label.set_xalign(0)
        box.add(label)

        entry = Gtk.Entry()
        entry.set_placeholder_text(placeholder)
        entry.set_text(default)
        entry.set_activates_default(True)
        box.add(entry)

        dialog.show_all()
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

def on_toggle(icon, item):
    threading.Thread(target=toggle_tracking, daemon=True).start()


def on_set_description(icon, item):
    def _do():
        desc = _gtk_input_dialog(
            "Description", "Time entry description:",
            placeholder="e.g. Client work",
            default=state.get("description", ""),
        )
        if desc is not None:
            state["description"] = desc
            save_state()
            if state["tracking"] and state["entry_id"]:
                try:
                    update_entry(state["workspace_id"], state["entry_id"],
                                 {"description": desc})
                except Exception:
                    pass

    threading.Thread(target=_do, daemon=True).start()


def on_view_today(icon, item):
    GLib.idle_add(_show_entries_window)


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
            args = type("Args", (), {"cloud": False})()
            text = _capture_cli_output(_cli_doctor, args)
        except Exception as e:
            text = f"Doctor failed: {e}"
        _gtk_message("Toggl Doctor", text)

    threading.Thread(target=_do, daemon=True).start()


def on_audit_today(icon, item):
    def _do():
        try:
            today = datetime.now().date().isoformat()
            args = type("Args", (), {
                "date_from": today,
                "date_to": today,
                "local_only": False,
                "gap_minutes": 120,
            })()
            text = _capture_cli_output(_cli_audit, args)
        except Exception as e:
            text = f"Audit failed: {e}"
        _gtk_message("Toggl Audit Today", text)

    threading.Thread(target=_do, daemon=True).start()


def _show_entries_window():
    """Show entries window with day navigation."""
    win = Gtk.Window(title="Entries")
    win.set_default_size(420, 420)
    win.set_keep_above(True)

    ctx = {"date": datetime.now().date(), "entries": []}

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    vbox.set_margin_start(10)
    vbox.set_margin_end(10)
    vbox.set_margin_top(10)
    vbox.set_margin_bottom(10)

    # Day navigation bar
    nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    prev_btn = Gtk.Button(label="<")
    next_btn = Gtk.Button(label=">")
    today_btn = Gtk.Button(label="Today")
    header_label = Gtk.Label()
    header_label.set_hexpand(True)

    nav_box.pack_start(prev_btn, False, False, 0)
    nav_box.pack_start(header_label, True, True, 0)
    nav_box.pack_start(today_btn, False, False, 0)
    nav_box.pack_start(next_btn, False, False, 0)
    vbox.pack_start(nav_box, False, False, 4)

    scroll = Gtk.ScrolledWindow()
    scroll.set_vexpand(True)
    listbox = Gtk.ListBox()
    listbox.set_selection_mode(Gtk.SelectionMode.NONE)
    scroll.add(listbox)
    vbox.pack_start(scroll, True, True, 0)

    empty_label = Gtk.Label(label="No entries")
    empty_label.set_sensitive(False)
    vbox.pack_start(empty_label, True, True, 0)

    def load_date(date_obj):
        ctx["date"] = date_obj
        header_label.set_markup(f"<b>Loading...</b>")
        for child in listbox.get_children():
            listbox.remove(child)
        empty_label.hide()
        next_btn.set_sensitive(date_obj < datetime.now().date())

        def _fetch():
            try:
                entries = fetch_entries_for_date(date_obj)
            except Exception:
                entries = []
            entries = _merge_entries_with_pending(entries, _pending_as_entries(date_obj))
            sorted_entries = sorted(entries, key=lambda x: x.get("start", ""))
            ctx["entries"] = sorted_entries
            GLib.idle_add(_populate, sorted_entries, date_obj)

        threading.Thread(target=_fetch, daemon=True).start()

    def _populate(entries, date_obj):
        for child in listbox.get_children():
            listbox.remove(child)

        total_secs = 0
        for e in entries:
            dur = e.get("duration", 0)
            if dur < 0:
                dur = int(time.time()) + dur
            total_secs += max(dur, 0)

        th, trem = divmod(total_secs, 3600)
        tm, _ = divmod(trem, 60)

        today = datetime.now().date()
        if date_obj == today:
            day_str = "Today"
        elif date_obj == today - timedelta(days=1):
            day_str = "Yesterday"
        else:
            day_str = date_obj.strftime("%a %b %d")

        header_label.set_markup(f"<b>{day_str} — {th}:{tm:02d} total</b>")

        if not entries:
            empty_label.show()
        else:
            empty_label.hide()
            for entry in entries:
                row = _build_entry_row(entry, win, ctx["entries"], listbox, ctx)
                listbox.add(row)

        listbox.show_all()

    prev_btn.connect("clicked", lambda b: load_date(ctx["date"] - timedelta(days=1)))
    next_btn.connect("clicked", lambda b: load_date(ctx["date"] + timedelta(days=1)))
    today_btn.connect("clicked", lambda b: load_date(datetime.now().date()))

    win.add(vbox)
    win.show_all()
    load_date(ctx["date"])


def _build_entry_row(entry, win, entries, listbox, ctx):
    """Build a single clickable row for an entry."""
    start_dt = datetime.fromisoformat(entry["start"]).astimezone()
    dur = entry.get("duration", 0)
    running = dur < 0
    if running:
        dur = int(time.time()) + dur
    h, rem = divmod(max(dur, 0), 3600)
    m, s = divmod(rem, 60)
    desc = entry.get("description") or "(no description)"

    stop_str = "now" if running else ""
    if not running and entry.get("stop"):
        stop_dt = datetime.fromisoformat(entry["stop"]).astimezone()
        stop_str = stop_dt.strftime("%H:%M")

    row = Gtk.ListBoxRow()
    row.set_activatable(True)
    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    hbox.set_margin_start(6)
    hbox.set_margin_end(6)
    hbox.set_margin_top(4)
    hbox.set_margin_bottom(4)

    time_label = Gtk.Label()
    running_marker = " ⏵" if running else ""
    time_label.set_markup(
        f"<tt>{start_dt.strftime('%H:%M')}–{stop_str}</tt>"
        f"  <small>({h}:{m:02d}:{s:02d}{running_marker})</small>"
    )
    time_label.set_xalign(0)
    hbox.pack_start(time_label, False, False, 0)

    offline = entry.get("_offline", False)
    label_text = f"{'⏳ ' if offline else ''}{desc}"
    desc_label = Gtk.Label(label=label_text)
    desc_label.set_xalign(0)
    desc_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
    hbox.pack_start(desc_label, True, True, 0)

    edit_btn = Gtk.Button(label="Edit")
    edit_btn.connect("clicked", lambda b: _on_edit_entry(entry, win, entries, listbox, ctx))
    hbox.pack_end(edit_btn, False, False, 0)

    row.add(hbox)
    return row


def _update_pending_entry(entry, update):
    indexes = set(entry.get("_pending_indexes", []))
    if not indexes:
        return False

    with pending_lock:
        queue = _load_pending()
        if any(idx >= len(queue) for idx in indexes):
            return False

        for idx in indexes:
            item = queue[idx]
            action = item.get("action")

            if "description" in update:
                item["description"] = update["description"]
            if "start" in update and (action == "start" or "start_time" in item or len(indexes) == 1):
                item["start_time"] = update["start"]
            if "stop" in update and (action == "stop" or "stop_time" in item or (len(indexes) == 1 and action == "start")):
                item["stop_time"] = update["stop"]

        _save_pending(queue)
        return True


def _delete_pending_entry(entry):
    indexes = set(entry.get("_pending_indexes", []))
    if not indexes:
        return False

    with pending_lock:
        queue = _load_pending()
        if any(idx >= len(queue) for idx in indexes):
            return False

        remaining = [item for idx, item in enumerate(queue) if idx not in indexes]
        remote_id = entry.get("_remote_entry_id")
        if remote_id:
            remaining.append({
                "action": "delete",
                "ts": datetime.now(timezone.utc).isoformat(),
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


def _on_edit_entry(entry, parent_win, entries, listbox, ctx):
    """Open edit dialog for a time entry — description, start, stop times."""
    def _do():
        result = _gtk_edit_entry_dialog(entry)
        if result is None:
            return
        if entry.get("_offline"):
            if result == "delete":
                desc = entry.get("description", "(no description)")
                if _gtk_confirm("Delete Entry", f"Delete '{desc}'?"):
                    if _delete_pending_entry(entry):
                        _play_sound(SOUND_STOP)
                        _refresh_entries(ctx["date"], entries, listbox, parent_win, ctx)
                    else:
                        _play_sound(SOUND_ERROR)
                return

            if _update_pending_entry(entry, result):
                _play_sound(SOUND_STOP)
                _refresh_entries(ctx["date"], entries, listbox, parent_win, ctx)
            else:
                _play_sound(SOUND_ERROR)
            return

        if result == "delete":
            wid = entry.get("workspace_id", state["workspace_id"])
            desc = entry.get("description", "(no description)")
            if _gtk_confirm("Delete Entry", f"Delete '{desc}'?"):
                try:
                    delete_entry(wid, entry["id"])
                    _play_sound(SOUND_STOP)
                    _refresh_entries(ctx["date"], entries, listbox, parent_win, ctx)
                except Exception:
                    _play_sound(SOUND_ERROR)
            return

        wid = entry.get("workspace_id", state["workspace_id"])
        try:
            update_entry(wid, entry["id"], result)
            _play_sound(SOUND_STOP)
            _refresh_entries(ctx["date"], entries, listbox, parent_win, ctx)
        except Exception:
            _play_sound(SOUND_ERROR)

    threading.Thread(target=_do, daemon=True).start()


def _refresh_entries(date_obj, old_entries, listbox, win, ctx):
    """Re-fetch entries for the given date and rebuild the listbox."""
    try:
        new_entries = fetch_entries_for_date(date_obj)
    except Exception:
        new_entries = []
    new_entries = _merge_entries_with_pending(new_entries, _pending_as_entries(date_obj))
    sorted_entries = sorted(new_entries, key=lambda x: x.get("start", ""))
    old_entries.clear()
    old_entries.extend(sorted_entries)
    ctx["entries"] = sorted_entries
    GLib.idle_add(_rebuild_listbox, listbox, sorted_entries, win, old_entries, ctx)


def _rebuild_listbox(listbox, entries, win, entries_ref, ctx):
    for child in listbox.get_children():
        listbox.remove(child)
    for entry in entries:
        row = _build_entry_row(entry, win, entries_ref, listbox, ctx)
        listbox.add(row)
    listbox.show_all()


def _gtk_edit_entry_dialog(entry):
    """Dialog with description, start time, stop time fields. Returns update dict or 'delete' or None."""
    result = {"value": None}
    done = threading.Event()

    start_dt = datetime.fromisoformat(entry["start"]).astimezone()
    dur = entry.get("duration", 0)
    running = dur < 0
    stop_text = ""
    if not running and entry.get("stop"):
        stop_dt = datetime.fromisoformat(entry["stop"]).astimezone()
        stop_text = stop_dt.strftime("%H:%M")

    def run():
        dialog = Gtk.Dialog(title="Edit Entry", modal=True)
        dialog.set_keep_above(True)
        dialog.set_resizable(False)
        dialog.add_buttons(
            "Delete", Gtk.ResponseType.REJECT,
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dialog.set_default_response(Gtk.ResponseType.OK)

        del_btn = dialog.get_widget_for_response(Gtk.ResponseType.REJECT)
        if del_btn:
            style_ctx = del_btn.get_style_context()
            style_ctx.add_class("destructive-action")

        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(8)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)

        grid.attach(Gtk.Label(label="Description:", xalign=0), 0, 0, 1, 1)
        desc_entry = Gtk.Entry()
        desc_entry.set_text(entry.get("description", ""))
        desc_entry.set_hexpand(True)
        desc_entry.set_activates_default(True)
        grid.attach(desc_entry, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Start (HH:MM):", xalign=0), 0, 1, 1, 1)
        start_entry = Gtk.Entry()
        start_entry.set_text(start_dt.strftime("%H:%M"))
        start_entry.set_max_length(5)
        start_entry.set_width_chars(7)
        grid.attach(start_entry, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="Stop (HH:MM):", xalign=0), 0, 2, 1, 1)
        stop_entry = Gtk.Entry()
        if running:
            stop_entry.set_text("running")
            stop_entry.set_sensitive(False)
        else:
            stop_entry.set_text(stop_text)
        stop_entry.set_max_length(7)
        stop_entry.set_width_chars(7)
        grid.attach(stop_entry, 1, 2, 1, 1)

        box.add(grid)
        dialog.show_all()
        resp = dialog.run()

        if resp == Gtk.ResponseType.REJECT:
            result["value"] = "delete"
        elif resp == Gtk.ResponseType.OK:
            update = {}
            new_desc = desc_entry.get_text().strip()
            if new_desc != entry.get("description", ""):
                update["description"] = new_desc

            new_start_str = start_entry.get_text().strip()
            orig_start_str = start_dt.strftime("%H:%M")
            if new_start_str != orig_start_str:
                parsed = _parse_hhmm(new_start_str, start_dt)
                if parsed:
                    update["start"] = parsed.isoformat()

            if not running:
                new_stop_str = stop_entry.get_text().strip()
                if new_stop_str != stop_text:
                    base = datetime.fromisoformat(entry["stop"]).astimezone() if entry.get("stop") else start_dt
                    parsed = _parse_hhmm(new_stop_str, base)
                    if parsed:
                        update["stop"] = parsed.isoformat()

            if update:
                result["value"] = update

        dialog.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        done.set()

    GLib.idle_add(run)
    done.wait()
    return result["value"]


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
        token = _gtk_input_dialog("API Token", "Enter Toggl API token:", placeholder="hex token")
        if token:
            global api_token
            api_token = token
            store_api_token(token)
            _init_workspace()

    threading.Thread(target=_do, daemon=True).start()


def on_quit(icon, item):
    save_state()
    icon.stop()


# ── Menu ────────────────────────────────────────────────────────────────────

def build_menu():
    toggle_label = "Stop tracking" if state["tracking"] else "Start tracking"
    desc = state.get("description", "")
    desc_label = f"Description: {desc}" if desc else "Set description..."
    return pystray.Menu(
        pystray.MenuItem(toggle_label, on_toggle, default=True),
        pystray.MenuItem(desc_label, on_set_description),
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

def update_loop():
    """Update icon tooltip with elapsed time."""
    while True:
        time.sleep(1)
        if icon_ref:
            icon_ref.title = get_tooltip()


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


def _format_local_dt(value):
    dt = _coerce_datetime(value).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_entry_line(entry):
    start_dt = _coerce_datetime(entry.get("start")).astimezone()
    stop_text = "running"
    if entry.get("stop"):
        stop_text = _coerce_datetime(entry["stop"]).astimezone().strftime("%H:%M")
    source = "local pending" if entry.get("_offline") else "toggl"
    desc = entry.get("description") or "(no description)"
    return (
        f"{start_dt.strftime('%Y-%m-%d %H:%M')} - {stop_text:<7} "
        f"{_format_duration(_entry_duration_seconds(entry)):>9}  "
        f"[{source}] {desc}"
    )


def _print_entries(entries):
    sorted_entries = sorted(entries, key=lambda item: item.get("start", ""))
    total = sum(_entry_duration_seconds(entry) for entry in sorted_entries)
    for entry in sorted_entries:
        print(_format_entry_line(entry))
    print(f"Total: {_format_duration(total)}")


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
        if _backup_path(path).exists():
            try:
                json.loads(_backup_path(path).read_text())
                return "corrupt (backup ok)"
            except (json.JSONDecodeError, OSError):
                pass
        return "corrupt"


def _load_cli_runtime(fetch_workspace=False):
    global api_token
    load_state()
    api_token = get_api_token()
    if fetch_workspace and api_token and not state.get("workspace_id"):
        try:
            me = fetch_me()
            state["workspace_id"] = me["default_workspace_id"]
            save_state()
        except Exception as e:
            print(f"Could not fetch workspace: {e}", file=sys.stderr)


def _cli_status(_args):
    _load_cli_runtime()
    print(f"State file: {STATE_FILE}")
    print(f"Pending file: {PENDING_FILE}")
    print(f"API token: {'yes' if api_token else 'no'}")
    print(f"Workspace: {state.get('workspace_id') or '(unknown)'}")
    print(f"Tracking: {'yes' if state.get('tracking') else 'no'}")
    if state.get("tracking") and state.get("start_time"):
        print(f"Started: {_format_local_dt(state['start_time'])}")
        start = _coerce_datetime(state["start_time"]).astimezone(timezone.utc)
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        print(f"Elapsed: {_format_duration(elapsed)}")
    if state.get("entry_id"):
        print(f"Toggl entry id: {state['entry_id']}")
    elif state.get("tracking"):
        print("Toggl entry id: none (local/offline timer)")

    raw_pending = _load_pending()
    pending_entries = _pending_as_entries()
    print(f"Pending queue: {len(raw_pending)} action(s), {len(pending_entries)} visible entries")
    if pending_entries:
        print("")
        _print_entries(pending_entries)
    return 0


def _cli_doctor(args):
    _load_cli_runtime()
    pending = _load_pending()
    pending_entries = _pending_as_entries()
    ledger_events = _read_ledger_events()
    rate_limit_left = max(int(rate_limited_until - time.monotonic()), 0)
    request_budget = _request_budget_status()

    print("Toggl Tray Doctor")
    print(f"State file: {STATE_FILE} ({_file_json_status(STATE_FILE)})")
    print(f"Pending file: {PENDING_FILE} ({_file_json_status(PENDING_FILE)})")
    print(f"Ledger file: {LEDGER_FILE} ({'ok' if LEDGER_FILE.exists() else 'missing'})")
    print(f"API token: {'yes' if api_token else 'no'}")
    print(f"Workspace: {state.get('workspace_id') or '(unknown)'}")
    print(f"Tracking: {'yes' if state.get('tracking') else 'no'}")
    if state.get("start_time"):
        print(f"Started: {_format_local_dt(state['start_time'])}")
    if state.get("entry_id"):
        print(f"Toggl entry id: {state['entry_id']}")
    elif state.get("tracking"):
        print("Toggl entry id: none (local/offline timer)")
    print(f"Pending actions: {len(pending)}")
    print(f"Pending visible entries: {len(pending_entries)}")
    print(f"Ledger events: {len(ledger_events)}")
    print(f"Rate limit: {'active for ' + str(rate_limit_left) + 's' if rate_limit_left else 'inactive'}")
    print(
        f"Request budget: {request_budget['used']}/{request_budget['limit']} used, "
        f"{request_budget['remaining']} remaining"
    )

    if args.cloud:
        if not api_token:
            print("Cloud current: skipped (no API token)")
        else:
            try:
                current = fetch_current_entry()
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
    pending_entries = _pending_as_entries(date_obj)
    entries = []
    if not args.local_only:
        if api_token:
            try:
                entries = fetch_entries_for_date(date_obj)
            except Exception as e:
                print(f"Could not fetch Toggl entries; showing local pending only: {e}", file=sys.stderr)
        else:
            print("No API token; showing local pending only.", file=sys.stderr)
    entries = _merge_entries_with_pending(entries, pending_entries)
    if not entries:
        print(f"No entries for {date_obj.isoformat()}.")
        if _load_pending():
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
        prev_stop = _coerce_datetime(prev["stop"])
        current_start = _coerce_datetime(current["start"])
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
        pending_entries = _pending_as_entries(date_obj)
        entries = []
        if not args.local_only and api_token:
            try:
                entries = fetch_entries_for_date(date_obj)
            except Exception as e:
                print(f"Could not fetch Toggl entries for {date_obj.isoformat()}: {e}", file=sys.stderr)
        elif not args.local_only and not api_token:
            print("No API token; auditing local pending entries only.", file=sys.stderr)

        merged = _merge_entries_with_pending(entries, pending_entries)
        total = sum(_entry_duration_seconds(entry) for entry in merged)
        blank = sum(1 for entry in merged if not entry.get("description"))
        local_count = sum(1 for entry in merged if entry.get("_offline"))
        gap_count = _entry_gap_count(merged, args.gap_minutes)
        grand_total += total
        print(
            f"{date_obj.isoformat()}: {_format_duration(total)} total, "
            f"{len(merged)} entries, {blank} blank, {local_count} local pending, "
            f"{gap_count} large gaps"
        )
    print(f"Total: {_format_duration(grand_total)}")
    return 0


def _cli_start(args):
    _load_cli_runtime(fetch_workspace=True)
    _append_event("cli_start_requested", description=" ".join(args.description).strip())
    open_pending = [op for op in _pending_operations(_load_pending()) if op.get("kind") == "open"]
    if state.get("tracking") or open_pending:
        if not state.get("tracking") and open_pending:
            op = open_pending[-1]
            state["tracking"] = True
            state["entry_id"] = None
            state["start_time"] = op.get("start_time")
            state["description"] = op.get("description", state.get("description", ""))
            save_state()
        print("Already tracking.")
        if state.get("start_time"):
            print(f"Started: {_format_local_dt(state['start_time'])}")
        return 0

    if args.description:
        state["description"] = " ".join(args.description).strip()
    description = state.get("description", "")
    project_id = state.get("project_id")
    workspace_id = state.get("workspace_id")
    now = datetime.now(timezone.utc).isoformat()

    entry = None
    if api_token and workspace_id:
        try:
            entry = start_entry(workspace_id, description=description, project_id=project_id, start_time=now)
        except Exception as e:
            print(f"Could not start on Toggl; queued locally: {e}", file=sys.stderr)
    elif not api_token:
        print("No API token; queued locally.", file=sys.stderr)
    else:
        print("No workspace id; queued locally.", file=sys.stderr)

    if entry:
        state["tracking"] = True
        state["entry_id"] = entry["id"]
        state["start_time"] = entry.get("start", now)
        save_state()
        _append_event("cli_started", entry_id=entry["id"], start_time=state["start_time"], mode="cloud")
        print(f"Started on Toggl at {_format_local_dt(state['start_time'])}.")
    else:
        state["tracking"] = True
        state["entry_id"] = None
        state["start_time"] = now
        save_state()
        queue_action("start", start_time=now, description=description, project_id=project_id)
        _append_event("cli_started", entry_id=None, start_time=now, mode="local")
        print(f"Started locally at {_format_local_dt(now)}.")
    return 0


def _cli_stop(_args):
    _load_cli_runtime()
    _append_event("cli_stop_requested", was_tracking=state.get("tracking"))
    if not state.get("tracking"):
        open_pending = [op for op in _pending_operations(_load_pending()) if op.get("kind") == "open"]
        if not open_pending:
            print("Not tracking.")
            return 0
        op = open_pending[-1]
        now = datetime.now(timezone.utc).isoformat()
        queue_action(
            "stop",
            start_time=op.get("start_time"),
            stop_time=now,
            description=op.get("description", ""),
            workspace_id=op.get("workspace_id"),
        )
        print(f"Stopped open local pending entry at {_format_local_dt(now)}.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    entry_id = state.get("entry_id")
    workspace_id = state.get("workspace_id")
    start_time = state.get("start_time")
    description = state.get("description", "")

    stopped_on_toggl = False
    if api_token and entry_id and workspace_id:
        try:
            stop_entry(workspace_id, entry_id)
            stopped_on_toggl = True
        except Exception as e:
            print(f"Could not stop on Toggl; queued locally: {e}", file=sys.stderr)

    if not stopped_on_toggl:
        queue_action(
            "stop",
            entry_id=entry_id,
            start_time=start_time,
            stop_time=now,
            description=description,
        )

    state["tracking"] = False
    state["entry_id"] = None
    state["start_time"] = None
    save_state()
    _append_event(
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
    reference = state.get("start_time")
    pending_entries = [entry for entry in _pending_as_entries() if entry.get("_pending_kind") == "open"]
    if not reference and pending_entries:
        reference = pending_entries[-1].get("start")
    reference_dt = _coerce_datetime(reference).astimezone()
    new_start = _parse_hhmm(args.time, reference_dt)
    if not new_start:
        print(f"Invalid time '{args.time}'. Use HH:MM.", file=sys.stderr)
        return 2
    new_start_iso = new_start.isoformat()

    updated = False
    if pending_entries:
        updated = _update_pending_entry(pending_entries[-1], {"start": new_start_iso}) or updated

    if state.get("tracking"):
        state["start_time"] = new_start_iso
        save_state()
        updated = True

    if state.get("entry_id") and state.get("workspace_id") and api_token:
        try:
            update_entry(state["workspace_id"], state["entry_id"], {
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
    if not api_token:
        print("No API token; cannot sync pending entries.", file=sys.stderr)
        return 2
    remaining = sync_pending()
    if remaining == 0 and PENDING_FILE.exists():
        PENDING_FILE.unlink(missing_ok=True)
    _sync_cloud_state()
    print(f"Sync complete. Pending actions remaining: {remaining}")
    return 0


def _desktop_quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _launcher_python():
    repo_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
    if repo_python.exists():
        return repo_python
    return Path(sys.executable).resolve()


def _launcher_icon():
    try:
        _init_icons()
        return Path(_icon_path_inactive)
    except Exception:
        return Path(__file__).resolve().parent / "toggl_icon.webp"


def _desktop_entry_text(autostart=False):
    script = Path(__file__).resolve()
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


def _refresh_desktop_database():
    updater = shutil.which("update-desktop-database")
    if updater:
        subprocess.run(
            [updater, str(APPLICATIONS_DIR)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )


def _cli_install_app(args):
    app_file = _write_desktop_file(DESKTOP_FILE)
    print(f"Installed app launcher: {app_file}")
    if args.autostart:
        autostart_file = _write_desktop_file(AUTOSTART_FILE, autostart=True)
        print(f"Installed autostart launcher: {autostart_file}")
    _refresh_desktop_database()
    print("Look for 'Toggl Tray' in the app launcher.")
    return 0


def _cli_uninstall_app(_args):
    removed = []
    for path in (DESKTOP_FILE, AUTOSTART_FILE):
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
        prog="toggl_tray.py",
        description="Run the Toggl tray app or inspect/recover local tracking state.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="run the tray app")
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
    sub.add_parser("sync", help="try to sync pending local entries now")
    install_app = sub.add_parser("install-app", help="install desktop app launcher")
    install_app.add_argument("--autostart", action="store_true", help="also start Toggl Tray on login")
    sub.add_parser("uninstall-app", help="remove desktop app launcher")
    return parser


def _run_cli(argv):
    if not argv:
        return None
    parser = _build_cli_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return None
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
    if args.command == "start":
        return _cli_start(args)
    if args.command == "stop":
        return _cli_stop(args)
    if args.command == "set-start":
        return _cli_set_start(args)
    if args.command == "sync":
        return _cli_sync(args)
    if args.command == "install-app":
        return _cli_install_app(args)
    if args.command == "uninstall-app":
        return _cli_uninstall_app(args)
    parser.print_help()
    return 2


# ── Init ────────────────────────────────────────────────────────────────────

def _acquire_instance_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_fp = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fp.seek(0)
        pid = lock_fp.read().strip()
        detail = f" (PID {pid})" if pid else ""
        print(f"Another instance is already running{detail}.", file=sys.stderr)
        print(f"If that process is gone, remove {LOCK_FILE} and start again.", file=sys.stderr)
        sys.exit(1)
    lock_fp.seek(0)
    lock_fp.truncate()
    lock_fp.write(str(os.getpid()))
    lock_fp.flush()
    return lock_fp


def _init_workspace():
    """Fetch workspace and project info, sync running entry."""
    global api_token, rate_limited_until, _last_cloud_poll_at
    if not api_token:
        return False
    pending = _load_pending()
    has_conflicting_pending = any(
        item.get("action") in ("start", "stop") for item in pending
    )
    if has_conflicting_pending and state.get("workspace_id"):
        return True

    try:
        if not state.get("workspace_id"):
            me = fetch_me()
            state["workspace_id"] = me["default_workspace_id"]

        if has_conflicting_pending:
            save_state()
            return True

        # Check for running entry
        current = fetch_current_entry()
        if current:
            state["tracking"] = True
            state["entry_id"] = current["id"]
            state["start_time"] = current["start"]
            state["description"] = current.get("description", "")
            state["project_id"] = current.get("project_id")
        else:
            state["tracking"] = False
            state["entry_id"] = None
            state["start_time"] = None
            if not state.get("description"):
                try:
                    recent = api_get("/me/time_entries")
                    if recent and isinstance(recent, list) and len(recent) > 0:
                        last = recent[0]
                        if last.get("description"):
                            state["description"] = last["description"]
                        if last.get("project_id") and not state.get("project_id"):
                            state["project_id"] = last["project_id"]
                except Exception:
                    pass

        save_state()
        _last_cloud_poll_at = time.monotonic()
        return True
    except RateLimitedError as e:
        rate_limited_until = time.monotonic() + e.retry_after
        return False
    except Exception as e:
        print(f"Init failed: {e}", file=sys.stderr)
        return False


def main():
    global icon_ref, api_token

    cli_result = _run_cli(sys.argv[1:])
    if cli_result is not None:
        return cli_result

    # Single instance lock
    lock_fp = _acquire_instance_lock()

    # Init GTK for thread safety
    GLib.threads_init = lambda: None  # already init'd by import
    Gtk.init([])

    load_state()

    api_token = get_api_token()
    if not api_token:
        print("No API token found. Set TOGGL_API_TOKEN env var or use the tray menu.", file=sys.stderr)
        print("You can also run: secret-tool store --label='Toggl API Token' service toggl username api_token", file=sys.stderr)
    else:
        _init_workspace()

    # Start hotkey listener
    start_hotkey_listener()

    # Start update thread
    updater = threading.Thread(target=update_loop, daemon=True)
    updater.start()

    # Start offline sync thread
    syncer = threading.Thread(target=sync_loop, daemon=True)
    syncer.start()

    # Create and run tray icon
    icon_ref = pystray.Icon("toggl-tray", render_icon(), get_tooltip(), build_menu())
    icon_ref.run()
    lock_fp.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
