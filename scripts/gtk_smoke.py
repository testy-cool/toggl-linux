#!/usr/bin/env python3
"""Exercise real GTK against private state and a fake service, never a live account."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from unittest.mock import patch

os.environ['PYSTRAY_BACKEND'] = 'gtk'
os.environ['GSETTINGS_BACKEND'] = 'memory'
os.environ.pop('TOGGL_API_TOKEN', None)

parser = argparse.ArgumentParser()
parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--screenshot-dir', type=Path)
args = parser.parse_args()
sys.path.insert(0, str(args.project_root.resolve()))

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Pango
import toggl_control as control
import toggl_debug
import toggl_entries_window
import toggl_task_window
import toggl_timer_window
import toggl_tray as app
import toggl_tray_popup


def drain():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        drain()
        if time.monotonic() >= deadline:
            raise AssertionError('GTK operation did not complete before its deadline')
        time.sleep(0.01)
    drain()


def widgets(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from widgets(child)


def local_connect(sock, address):
    if sock.family != socket.AF_UNIX:
        raise AssertionError('Native GTK smoke tests cannot connect to the network')
    return original_connect(sock, address)


original_connect = socket.socket.connect
with tempfile.TemporaryDirectory(prefix='toggl-native-gtk-') as scratch:
    root = Path(scratch)
    paths = dict(STATE_DIR=root, CONFIG_FILE=root/'config.json', STATE_FILE=root/'state.json',
                 PENDING_FILE=root/'pending.json', LEDGER_FILE=root/'events.jsonl',
                 REQUEST_BUDGET_FILE=root/'request_budget.json', LOCK_FILE=root/'lock',
                 APPLICATIONS_DIR=root/'applications', DESKTOP_FILE=root/'applications/app.desktop',
                 AUTOSTART_DIR=root/'autostart', AUTOSTART_FILE=root/'autostart/app.desktop', CLI_FILE=root/'bin/toggl')
    for key, value in paths.items():
        setattr(app, key, value)
    app.api_token = None
    app.icon_ref = None
    app.state.update(tracking=False, entry_id=None, start_time=None, workspace_id=1,
                     project_id=None, task_id=None, description='')
    app._project_cache = dict(account=None, projects=[
        dict(id=6, workspace_id=1, name='Smoke project', color='#e56f22', active=True)])
    app._task_cache = dict(context=control.snapshot(app)['project_context'], tasks=[
        dict(id=31, workspace_id=1, project_id=6, name='Smoke task', active=True)])
    app._task_capability = 'supported'
    recorder = toggl_debug.DebugLog(root, lambda: ['smoke-secret-token'])
    recorder.capture_glib(GLib)
    server = control.start_server(app)
    try:
        with patch.object(socket.socket, 'connect', local_connect), \
             patch.object(app.requests, 'request', side_effect=AssertionError('Account API is forbidden')) as network, \
             patch.object(app, '_play_sound'), patch.object(app, '_notify'):
            popup = toggl_tray_popup.TrayPopup(app, 400, 40)
            drain()
            row = next(row for row in popup.list.get_children() if row.data['kind'] == 'task')
            popup._clicked(popup.list, row)
            until(lambda: popup.window is None)
            assert app.state['tracking'] and app.state['task_id'] == 31
            assert app.state['description'] == ''
            assert app._load_pending()[0]['task_id'] == 31
            assert app._panel_timer_snapshot()['description'] == 'Smoke task'

            timer = toggl_timer_window.TimerEditor(app)
            drain()
            assert timer.task.get_active_id() == '31'
            timer.description.set_text('Smoke entry details')
            timer._save()
            until(lambda: not timer.busy)
            assert app.state['description'] == 'Smoke entry details'
            assert app._load_pending()[0]['description'] == 'Smoke entry details'

            # The fake service creates metadata, while actual dispatch switches the
            # private offline timer. This exercises the real task-form callback.
            def fake_service(_directory, action, payload):
                if action == 'task-create':
                    task = dict(id=42, workspace_id=1, project_id=6, name=payload['name'], active=True)
                    app._task_cache['tasks'].append(task)
                    result = control.snapshot(app)
                    result['saved_task'] = task
                    return result
                return control.dispatch(app, action, payload)
            editor = toggl_task_window.TaskEditor(app, control.snapshot(app), 6,
                                                  on_saved=lambda _snapshot: popup._fill())
            editor.name.set_text('Created smoke task')
            with patch.object(control, 'request', side_effect=fake_service):
                editor._save(True)
                until(lambda: editor.window is None)
            assert app.state['task_id'] == 42 and app.state['description'] == ''
            assert len(app._pending_operations(app._load_pending())) == 2
            timer._tick()
            assert timer.task.get_active_id() == '42'

            current = toggl_tray_popup.TrayPopup(app, 400, 40)
            current._close_unless_busy = lambda: False
            current.search.set_text('does not match any saved row')
            current._fill()
            drain()
            assert current.current.get_visible()
            assert current.current_title.get_text() == 'Created smoke task'
            assert current.current_project.get_text() == 'Project: Smoke project'
            manager = toggl_task_window.TaskManager(app)
            with patch.object(app, 'fetch_entries_for_date', return_value=[]):
                toggl_entries_window._show_entries_window()
                until(lambda: any(window.get_title() == 'Toggl Entries' for window in Gtk.Window.list_toplevels()))
            drain()
            sizes = []
            for window in Gtk.Window.list_toplevels():
                for widget in widgets(window):
                    if widget.get_visible():
                        font = widget.get_style_context().get_property('font', Gtk.StateFlags.NORMAL)
                        if font and font.get_size():
                            sizes.append(font.get_size() / Pango.SCALE * 96 / 72)
            assert sizes and min(sizes) >= 12, sizes
            assert current.window.get_size()[0] <= 560, current.window.get_size()
            if args.screenshot_dir:
                args.screenshot_dir.mkdir(parents=True, exist_ok=True)
                width, height = current.window.get_size()
                picture = Gdk.pixbuf_get_from_window(current.window.get_window(), 0, 0, width, height)
                picture.savev(str(args.screenshot_dir/'running-popup.png'), 'png', [], [])

            Gtk.Label().set_markup('<invalid-tag>smoke-secret-token</invalid-tag>')
            def failed_callback():
                raise RuntimeError('Native GTK callback diagnostic probe')
            GLib.idle_add(failed_callback)
            drain()
            log = (root/'debug.log').read_text()
            assert 'Failed to set text' in log
            assert 'Native GTK callback diagnostic probe' in log
            assert 'smoke-secret-token' not in log
            network.assert_not_called()
            print(json.dumps(dict(real_gtk=True, private_offline_switch=True, task_form_callback=True,
                                  timer_edit=True, pinned_current=True, min_font_px=min(sizes),
                                  native_warning=True, callback_traceback=True, network_calls=0)))
    finally:
        for window in Gtk.Window.list_toplevels():
            window.destroy()
        drain()
        server.shutdown()
        server.server_close()
        recorder.close()
