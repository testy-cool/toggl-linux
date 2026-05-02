#!/usr/bin/env python3
"""Toggl Track tray timer — Ctrl+Shift+T to toggle tracking."""

import os
import sys
import json
import time
import fcntl
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
LOCK_FILE = STATE_DIR / "toggl-tray.lock"
ICON_SIZE = 64
ICON_PADDING = 6  # transparent padding around the icon


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


# ── API Token ───────────────────────────────────────────────────────────────

def get_api_token():
    """Get Toggl API token from env or GNOME keyring."""
    token = os.environ.get("TOGGL_API_TOKEN")
    if token:
        return token
    try:
        result = subprocess.run(
            ["secret-tool", "lookup", "service", "toggl", "username", "api_token"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def store_api_token(token):
    """Store token in GNOME keyring."""
    try:
        subprocess.run(
            ["secret-tool", "store", "--label=Toggl API Token",
             "service", "toggl", "username", "api_token"],
            input=token, text=True, timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ── Toggl API ───────────────────────────────────────────────────────────────

def _auth():
    return HTTPBasicAuth(api_token, "api_token")


def _api(method, path, json=None, params=None):
    """Single API wrapper with 429 retry."""
    for attempt in range(3):
        r = requests.request(
            method, f"{API_BASE}{path}",
            auth=_auth(), json=json, params=params, timeout=10,
        )
        if r.status_code == 429:
            wait = int(r.headers.get("X-Toggl-Quota-Resets-In", 5))
            _play_sound(SOUND_ERROR)
            time.sleep(min(wait, 30))
            continue
        r.raise_for_status()
        return r.json() if r.content else None
    raise Exception("Rate limited after 3 retries")


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


def start_entry(workspace_id, description="", project_id=None):
    now = datetime.now(timezone.utc)
    payload = {
        "created_with": "toggl-tray-linux",
        "description": description,
        "start": now.isoformat(),
        "duration": -1 * int(now.timestamp()),
        "workspace_id": workspace_id,
    }
    if project_id:
        payload["project_id"] = project_id
    return api_post(f"/workspaces/{workspace_id}/time_entries", payload)


def stop_entry(workspace_id, entry_id):
    """Stop a running entry by PATCHing it."""
    return _api("PATCH", f"/workspaces/{workspace_id}/time_entries/{entry_id}/stop")


def fetch_entries_for_date(date_obj):
    """Get time entries for a specific date."""
    start = datetime(date_obj.year, date_obj.month, date_obj.day, tzinfo=timezone.utc)
    params = {"start_date": start.isoformat(), "end_date": (start + timedelta(days=1)).isoformat()}
    return api_get("/me/time_entries", params=params) or []


def fetch_today_entries():
    return fetch_entries_for_date(datetime.now(timezone.utc))


def update_entry(workspace_id, entry_id, data):
    return api_put(f"/workspaces/{workspace_id}/time_entries/{entry_id}", data)


def delete_entry(workspace_id, entry_id):
    return api_delete(f"/workspaces/{workspace_id}/time_entries/{entry_id}")


# ── State persistence ──────────────────────────────────────────────────────

def save_state():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def load_state():
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text())
            state.update(saved)
        except (json.JSONDecodeError, KeyError):
            pass


# ── Offline queue ──────────────────────────────────────────────────────────

pending_lock = threading.Lock()


def _load_pending():
    if PENDING_FILE.exists():
        try:
            return json.loads(PENDING_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return []


def _save_pending(queue):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps(queue, indent=2))


def queue_action(action, **kwargs):
    """Queue an offline action for later sync."""
    with pending_lock:
        queue = _load_pending()
        queue.append({"action": action, "ts": datetime.now(timezone.utc).isoformat(), **kwargs})
        _save_pending(queue)


def sync_pending():
    """Try to sync all pending actions. Returns number of remaining."""
    with pending_lock:
        queue = _load_pending()
        if not queue:
            return 0
        remaining = []
        for item in queue:
            try:
                if item["action"] == "start":
                    entry = start_entry(
                        state["workspace_id"],
                        description=item.get("description", ""),
                        project_id=item.get("project_id"),
                    )
                    # Update the start time to what we recorded offline
                    if item.get("start_time"):
                        update_entry(state["workspace_id"], entry["id"],
                                     {"start": item["start_time"]})
                    # If we already stopped locally, stop this entry too
                    if item.get("stop_time"):
                        update_entry(state["workspace_id"], entry["id"],
                                     {"stop": item["stop_time"], "duration": item.get("duration", 0)})
                elif item["action"] == "stop":
                    if item.get("entry_id"):
                        stop_entry(state["workspace_id"], item["entry_id"])
                    elif item.get("start_time") and item.get("stop_time"):
                        # Entry was started+stopped offline, create complete entry
                        start_dt = datetime.fromisoformat(item["start_time"])
                        stop_dt = datetime.fromisoformat(item["stop_time"])
                        dur = int((stop_dt - start_dt).total_seconds())
                        api_post(f"/workspaces/{state['workspace_id']}/time_entries", {
                            "created_with": "toggl-tray-linux",
                            "description": item.get("description", ""),
                            "start": item["start_time"],
                            "stop": item["stop_time"],
                            "duration": dur,
                            "workspace_id": state["workspace_id"],
                        })
            except Exception:
                remaining.append(item)
        _save_pending(remaining)
        return len(remaining)


def sync_loop():
    """Background thread: retry pending actions every 30s."""
    while True:
        time.sleep(30)
        try:
            left = sync_pending()
            if left == 0 and PENDING_FILE.exists():
                PENDING_FILE.unlink(missing_ok=True)
        except Exception:
            pass


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
        update_tray_icon()


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
    """Sync local state with cloud. Cloud is always truth. Returns True if online."""
    try:
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
        save_state()
        return True
    except Exception as e:
        print(f"Cloud sync failed: {e}", file=sys.stderr)
        return False


def toggle_tracking(*_args):
    """Start or stop tracking. Cloud is source of truth — sync before acting."""
    global icon_ref

    online = _sync_cloud_state()

    if state["tracking"]:
        # Stop
        now = datetime.now(timezone.utc).isoformat()
        if online:
            try:
                stop_entry(state["workspace_id"], state["entry_id"])
            except Exception as e:
                print(f"Stop failed: {e}", file=sys.stderr)
                _play_sound(SOUND_ERROR)
                return
        else:
            queue_action("stop", entry_id=state.get("entry_id"),
                         start_time=state.get("start_time"), stop_time=now,
                         description=state.get("description", ""))
        state["tracking"] = False
        state["entry_id"] = None
        state["start_time"] = None
        save_state()
        _play_sound(SOUND_STOP)
    else:
        # Start
        now = datetime.now(timezone.utc)
        if online:
            try:
                entry = start_entry(
                    state["workspace_id"],
                    description=state.get("description", ""),
                    project_id=state.get("project_id"),
                )
                state["tracking"] = True
                state["entry_id"] = entry["id"]
                state["start_time"] = entry["start"]
            except Exception as e:
                print(f"Start failed: {e}", file=sys.stderr)
                _play_sound(SOUND_ERROR)
                return
        else:
            state["tracking"] = True
            state["entry_id"] = None
            state["start_time"] = now.isoformat()
            queue_action("start", start_time=now.isoformat(),
                         description=state.get("description", ""),
                         project_id=state.get("project_id"))
        save_state()
        _play_sound(SOUND_START)

    if icon_ref:
        update_tray_icon()
        icon_ref.title = get_tooltip()
        icon_ref.menu = build_menu()


# ── Notifications ───────────────────────────────────────────────────────────

SOUND_START = "/usr/share/sounds/freedesktop/stereo/bell.oga"
SOUND_STOP = "/usr/share/sounds/freedesktop/stereo/complete.oga"
SOUND_ERROR = "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga"


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

    desc_label = Gtk.Label(label=desc)
    desc_label.set_xalign(0)
    desc_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
    hbox.pack_start(desc_label, True, True, 0)

    edit_btn = Gtk.Button(label="Edit")
    edit_btn.connect("clicked", lambda b: _on_edit_entry(entry, win, entries, listbox, ctx))
    hbox.pack_end(edit_btn, False, False, 0)

    row.add(hbox)
    return row


def _on_edit_entry(entry, parent_win, entries, listbox, ctx):
    """Open edit dialog for a time entry — description, start, stop times."""
    def _do():
        result = _gtk_edit_entry_dialog(entry)
        if result is None:
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
        return
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
    return pystray.Menu(
        pystray.MenuItem(toggle_label, on_toggle, default=True),
        pystray.MenuItem("Set description...", on_set_description),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Today's entries", on_view_today),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Set API token...", on_set_token),
        pystray.MenuItem("Quit", on_quit),
    )


# ── Global hotkey ───────────────────────────────────────────────────────────

def start_hotkey_listener():
    """Grab Ctrl+Shift+T globally via X11 — no app sees the keypress."""
    def _grab_loop():
        dpy = Display()
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

        while True:
            event = dpy.next_event()
            if event.type == X.KeyPress:
                threading.Thread(target=toggle_tracking, daemon=True).start()

    t = threading.Thread(target=_grab_loop, daemon=True)
    t.start()


# ── Update loop ─────────────────────────────────────────────────────────────

def update_loop():
    """Update icon tooltip with elapsed time."""
    while True:
        time.sleep(1)
        if icon_ref:
            icon_ref.title = get_tooltip()


# ── Init ────────────────────────────────────────────────────────────────────

def _init_workspace():
    """Fetch workspace and project info, sync running entry."""
    global api_token
    if not api_token:
        return False

    try:
        me = fetch_me()
        state["workspace_id"] = me["default_workspace_id"]

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

        save_state()
        return True
    except Exception as e:
        print(f"Init failed: {e}", file=sys.stderr)
        return False


def main():
    global icon_ref, api_token

    # Single instance lock
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_fp = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Another instance is already running.", file=sys.stderr)
        sys.exit(1)
    lock_fp.write(str(os.getpid()))
    lock_fp.flush()

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


if __name__ == "__main__":
    main()
