#!/usr/bin/env python3
"""Toggl Track tray timer — Ctrl+Shift+T to toggle tracking."""

import os
import sys
import json
import time
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
STATE_FILE = Path.home() / ".local" / "share" / "toggl-tray" / "state.json"
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
            _notify("Toggl", f"Rate limited — retrying in {wait}s")
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
    return api_patch(f"/workspaces/{workspace_id}/time_entries/{entry_id}/stop", {})


def fetch_today_entries():
    """Get today's time entries."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    params = {"start_date": today.isoformat(), "end_date": (today + timedelta(days=1)).isoformat()}
    return api_get("/me/time_entries", params=params) or []


def update_entry(workspace_id, entry_id, data):
    return api_put(f"/workspaces/{workspace_id}/time_entries/{entry_id}", data)


def delete_entry(workspace_id, entry_id):
    return api_delete(f"/workspaces/{workspace_id}/time_entries/{entry_id}")


# ── State persistence ──────────────────────────────────────────────────────

def save_state():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def load_state():
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text())
            state.update(saved)
        except (json.JSONDecodeError, KeyError):
            pass


# ── Icon rendering ──────────────────────────────────────────────────────────

def _load_icon_base():
    """Load and cache the base icon image."""
    global _icon_active, _icon_inactive
    if _icon_active is not None:
        return
    icon_path = Path(__file__).parent / "toggl_icon.webp"
    img = Image.open(icon_path).convert("RGBA").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    _icon_active = img
    # Greyed out: desaturate + reduce opacity
    grey = img.convert("LA").convert("RGBA")
    # Dim it slightly
    r, g, b, a = grey.split()
    from PIL import ImageEnhance
    grey_rgb = Image.merge("RGB", (r, g, b))
    grey_rgb = ImageEnhance.Brightness(grey_rgb).enhance(0.6)
    r2, g2, b2 = grey_rgb.split()
    _icon_inactive = Image.merge("RGBA", (r2, g2, b2, a))


_icon_active = None
_icon_inactive = None


def render_icon():
    """Return Toggl icon — colored when tracking, greyed when off."""
    _load_icon_base()
    src = _icon_active if state["tracking"] else _icon_inactive
    # Add transparent padding so Cinnamon renders it smaller
    inner = ICON_SIZE - 2 * ICON_PADDING
    shrunk = src.resize((inner, inner), Image.LANCZOS)
    padded = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    padded.paste(shrunk, (ICON_PADDING, ICON_PADDING))
    return padded


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

def toggle_tracking(*_args):
    """Start or stop tracking."""
    global icon_ref

    if state["tracking"]:
        # Stop
        try:
            if state["entry_id"] and state["workspace_id"]:
                stop_entry(state["workspace_id"], state["entry_id"])
            state["tracking"] = False
            state["entry_id"] = None
            state["start_time"] = None
            save_state()
            _notify("Toggl", "Tracking stopped")
        except Exception as e:
            _notify("Toggl Error", f"Failed to stop: {e}")
    else:
        # Start
        try:
            entry = start_entry(
                state["workspace_id"],
                description=state.get("description", ""),
                project_id=state.get("project_id"),
            )
            state["tracking"] = True
            state["entry_id"] = entry["id"]
            state["start_time"] = entry["start"]
            save_state()
            _notify("Toggl", "Tracking started")
        except Exception as e:
            _notify("Toggl Error", f"Failed to start: {e}")

    if icon_ref:
        icon_ref.icon = render_icon()
        icon_ref.title = get_tooltip()
        icon_ref.menu = build_menu()


# ── Notifications ───────────────────────────────────────────────────────────

def _notify(title, body):
    try:
        subprocess.Popen(
            ["notify-send", "-a", "Toggl", title, body],
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
    def _do():
        try:
            entries = fetch_today_entries()
        except Exception as e:
            _gtk_message("Error", f"Failed to fetch entries: {e}")
            return

        if not entries:
            _gtk_message("Today's Entries", "No entries today.")
            return

        total_secs = 0
        lines = []
        for e in sorted(entries, key=lambda x: x.get("start", "")):
            start = datetime.fromisoformat(e["start"]).astimezone()
            desc = e.get("description") or "(no description)"
            dur = e.get("duration", 0)
            if dur < 0:
                # Running entry
                dur = int(time.time()) + dur
                running = " ⏵"
            else:
                running = ""
            total_secs += max(dur, 0)
            h, rem = divmod(dur, 3600)
            m, s = divmod(rem, 60)
            lines.append(f"  {start.strftime('%H:%M')}  {h}:{m:02d}:{s:02d}{running}  {desc}")

        th, trem = divmod(total_secs, 3600)
        tm, ts = divmod(trem, 60)
        header = f"Today — {th}:{tm:02d}:{ts:02d} total\n{'─' * 50}\n"
        _show_entries_window(header + "\n".join(lines), entries)

    threading.Thread(target=_do, daemon=True).start()


def _show_entries_window(text, entries):
    """Show today's entries in a GTK window with edit/delete buttons."""
    done = threading.Event()

    def run():
        win = Gtk.Window(title="Today's Entries")
        win.set_default_size(500, 400)
        win.set_keep_above(True)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)

        textview = Gtk.TextView()
        textview.set_editable(False)
        textview.set_monospace(True)
        textview.get_buffer().set_text(text)
        scroll.add(textview)
        vbox.pack_start(scroll, True, True, 0)

        # Edit last entry button
        if entries:
            hbox = Gtk.Box(spacing=8)
            edit_btn = Gtk.Button(label="Edit Last Entry")
            edit_btn.connect("clicked", lambda b: _on_edit_entry(win, entries[-1]))
            hbox.pack_start(edit_btn, False, False, 0)

            del_btn = Gtk.Button(label="Delete Last Entry")
            del_btn.connect("clicked", lambda b: _on_delete_entry(win, entries[-1]))
            hbox.pack_start(del_btn, False, False, 0)
            vbox.pack_start(hbox, False, False, 0)

        win.add(vbox)
        win.connect("destroy", lambda w: done.set())
        win.show_all()

    GLib.idle_add(run)


def _on_edit_entry(parent_win, entry):
    def _do():
        eid = entry["id"]
        wid = entry.get("workspace_id", state["workspace_id"])
        desc = entry.get("description", "")

        new_desc = _gtk_input_dialog("Edit Entry", "Description:", default=desc)
        if new_desc is not None:
            try:
                update_entry(wid, eid, {"description": new_desc})
                _notify("Toggl", f"Entry updated: {new_desc}")
            except Exception as e:
                _notify("Toggl Error", f"Update failed: {e}")

    threading.Thread(target=_do, daemon=True).start()


def _on_delete_entry(parent_win, entry):
    def _do():
        eid = entry["id"]
        wid = entry.get("workspace_id", state["workspace_id"])
        desc = entry.get("description", "(no description)")

        if _gtk_confirm("Delete Entry", f"Delete '{desc}'?"):
            try:
                delete_entry(wid, eid)
                _notify("Toggl", f"Entry deleted: {desc}")
            except Exception as e:
                _notify("Toggl Error", f"Delete failed: {e}")

    threading.Thread(target=_do, daemon=True).start()


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

    # Create and run tray icon
    icon_ref = pystray.Icon("toggl-tray", render_icon(), get_tooltip(), build_menu())
    icon_ref.run()


if __name__ == "__main__":
    main()
