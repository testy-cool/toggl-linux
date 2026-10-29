#!/usr/bin/env python3
"""Exercise the real React/WebKit window against an isolated, offline tray."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

os.environ['PYSTRAY_BACKEND'] = 'gtk'
os.environ['GSETTINGS_BACKEND'] = 'memory'
os.environ.pop('TOGGL_API_TOKEN', None)
parser = argparse.ArgumentParser()
parser.add_argument('--screenshot-dir', type=Path)
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import GLib, Gdk
import toggl_tray as app
import toggl_control as control
import toggl_tasks
from toggl_webview import Bridge, create_window
import webview


def until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError('Webview operation did not finish before its deadline')


with tempfile.TemporaryDirectory(prefix='toggl-webview-') as scratch:
    root = Path(scratch)
    for name, filename in dict(STATE_DIR='', CONFIG_FILE='config.json', STATE_FILE='state.json',
                               PENDING_FILE='pending.json', LEDGER_FILE='events.jsonl',
                               REQUEST_BUDGET_FILE='request_budget.json', LOCK_FILE='lock').items():
        setattr(app, name, root/filename)
    app.api_token = None
    app.state.update(tracking=False, entry_id=None, start_time=None, workspace_id=1,
                     project_id=None, task_id=None, description='')
    app._project_cache = dict(account=None, projects=[
        dict(id=6, workspace_id=1, name='Studio', color='#779bd8', active=True),
        dict(id=7, workspace_id=1, name='Research', color='#7db8a0', active=True)])
    app._task_cache = dict(context=control.snapshot(app)['project_context'], tasks=[
        dict(id=31, workspace_id=1, project_id=6, name='Improve navigation', active=True)])
    app._task_capability = 'supported'
    server = control.start_server(app)
    bridge = Bridge(root, managed=True)
    window = create_window(bridge, width=600, height=780)
    errors = []

    def js(code):
        return window.evaluate_js(code)

    def click(label):
        code = f"Array.from(document.querySelectorAll('button')).find(b => (b.getAttribute('aria-label') || b.textContent.trim()) === {json.dumps(label)})"
        assert js(f'Boolean({code})'), label
        js(f"const button={code}; if(button.getAttribute('role')==='tab') button.dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',bubbles:true}})); else button.click()")

    def text(value):
        js(f"const input = document.getElementById('work-description'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,{json.dumps(value)}); input.dispatchEvent(new Event('input',{{bubbles:true}}));")
        until(lambda: js(f"document.getElementById('work-description')?.value === {json.dumps(value)}"))

    def choose(label, value):
        js(f"document.querySelector('button[role=combobox][aria-label={label}]').dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',bubbles:true}}))")
        until(lambda: js("!!document.querySelector('[role=option]')"))
        js(f"Array.from(document.querySelectorAll('[role=option]')).find(e=>e.textContent.trim()==={json.dumps(value)}).click()")

    def menu(label, option):
        js(f"Array.from(document.querySelectorAll('button')).find(b=>b.getAttribute('aria-label')==={json.dumps('More actions for '+label)}).dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',bubbles:true}}))")
        until(lambda: js("!!document.querySelector('[role=menuitem]')"))
        js(f"Array.from(document.querySelectorAll('[role=menuitem]')).find(e=>e.textContent.trim()==={json.dumps(option)}).click()")

    def metadata(_app, action, payload):
        assert payload['context'] == control.snapshot(app)['project_context']
        tasks = app._task_cache['tasks']
        if action == 'task-create':
            saved = dict(id=42, workspace_id=1, project_id=payload['project_id'], name=payload['name'], active=True)
            tasks.append(saved)
        else:
            saved = next(t for t in tasks if t['id'] == payload['task']['id'])
            assert saved == payload['task'], 'The task editor sent a stale target'
            saved.update({key: payload[key] for key in ('name', 'active') if key in payload})
        return dict(saved)

    def picture(name):
        if not args.screenshot_dir:
            return
        time.sleep(0.2)  # Capture the rendered frame after React's controlled-input update.
        ready = threading.Event()
        def capture():
            try:
                args.screenshot_dir.mkdir(parents=True, exist_ok=True)
                width, height = window.native.get_size()
                pixels = Gdk.pixbuf_get_from_window(window.native.get_window(), 0, 0, width, height)
                if pixels is not None:
                    pixels.savev(str(args.screenshot_dir/name), 'png', [], [])
            finally:
                ready.set()
            return False
        GLib.idle_add(capture)
        assert ready.wait(5)

    def verify():
        try:
            assert bridge._ready.wait(15), 'JavaScript bridge never became ready'
            until(lambda: window.native.get_mapped())
            until(lambda: js("document.body.textContent.includes('Improve navigation')"))
            click('Start Improve navigation')
            until(lambda: app.state['tracking'] and app.state['task_id'] == 31)
            started = app.state['start_time']
            until(lambda: js("document.body.textContent.includes('Running now')"))
            picture('work-panel.png')
            click('Edit running timer')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Corrected inside the window')
            picture('edit-timer.png')
            click('Save changes')
            until(lambda: app.state['description'] == 'Corrected inside the window')
            assert app.state['start_time'] == started and app.state['task_id'] == 31
            assert app._load_pending()[0]['description'] == app.state['description']
            until(lambda: js("!document.getElementById('work-description')"))
            metrics = js("JSON.stringify({minFont:Math.min(...Array.from(document.querySelectorAll('body *')).filter(e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height).map(e=>parseFloat(getComputedStyle(e).fontSize))),width:document.documentElement.clientWidth,overflow:document.documentElement.scrollWidth>document.documentElement.clientWidth})")
            if isinstance(metrics, str):
                metrics = json.loads(metrics)
            assert metrics['minFont'] >= 12 and not metrics['overflow'], metrics
            click('Edit running timer')
            until(lambda: js("!!document.getElementById('work-description')"))
            choose('Project', 'Research')
            click('Save changes')
            until(lambda: app.state['project_id'] == 7)
            assert app.state['task_id'] is None and app.state['start_time'] == started
            until(lambda: js("!document.getElementById('work-description')"))
            click('Edit running timer')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Draft survives closing and stopping')
            bridge.close()
            until(lambda: not window.native.get_mapped())
            window.show()
            until(lambda: window.native.get_mapped())
            assert js("document.getElementById('work-description').value === 'Draft survives closing and stopping'")
            click('Stop timer and keep draft')
            until(lambda: not app.state['tracking'])
            until(lambda: js("document.body.textContent.includes('Edit time entry')"))
            assert js("document.getElementById('work-description').value === 'Draft survives closing and stopping'")
            click('Save changes')
            until(lambda: app._pending_as_entries()[0]['description'] == 'Draft survives closing and stopping')
            until(lambda: js("!document.getElementById('work-description')"))
            assert app._load_pending(), 'The offline interval was not retained'
            click('New task')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Created in the webview')
            choose('Project', 'Studio')
            click('Create task')
            until(lambda: any(t['id'] == 42 for t in app._cached_tasks()))
            until(lambda: js("!document.getElementById('work-description')"))
            click('Tasks')
            menu('Created in the webview', 'Rename task')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Renamed in the webview')
            click('Save changes')
            until(lambda: any(t['name'] == 'Renamed in the webview' for t in app._cached_tasks()))
            until(lambda: js("!document.getElementById('work-description')"))
            menu('Renamed in the webview', 'Archive task')
            until(lambda: next(t for t in app._cached_tasks() if t['id'] == 42)['active'] is False)
            until(lambda: js("document.body.textContent.includes('Restore')"))
            click('Restore')
            until(lambda: next(t for t in app._cached_tasks() if t['id'] == 42)['active'] is True)
            assert not app.state['tracking']
            click('Today')
            until(lambda: js("document.body.textContent.includes('Draft survives closing and stopping')"))
            click('Edit entry Draft survives closing and stopping')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Corrected today entry')
            click('Save changes')
            until(lambda: app._pending_as_entries()[0]['description'] == 'Corrected today entry')
            until(lambda: js("!document.getElementById('work-description')"))
            assert not app.state['tracking']
            click('New timer')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Kept during a conflict')
            app.state['description'] = 'Changed elsewhere'
            js("document.querySelector('form').requestSubmit()")
            until(lambda: js("document.body.textContent.includes('Timer changed')"))
            assert js("document.getElementById('work-description').value === 'Kept during a conflict'")
            assert not app.state['tracking']
            app.api_token = 'another-fixture-account'
            until(lambda: js("document.body.textContent.includes('Your account or workspace changed')"))
            assert js("document.getElementById('work-description').value === 'Kept during a conflict'")
            assert js("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Start timer').disabled")
            app.api_token = None
            click('Cancel')
            now = datetime.now(timezone.utc)
            midnight = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            legacy_start = midnight.isoformat()
            legacy_stop = (midnight+(now-midnight)/2).isoformat()
            app._save_pending([dict(action='stop', entry_id=99, workspace_id=1, project_id=6,
                                    task_id=None, description='Legacy interval review', start_time=legacy_start,
                                    stop_time=legacy_stop, _verify_running=True)])
            click('Today')
            until(lambda: js("document.body.textContent.includes('Legacy interval review')"))
            click('Edit entry Legacy interval review')
            until(lambda: js("!!document.getElementById('work-stop')"))
            assert js("!!document.getElementById('work-start').value && !!document.getElementById('work-stop').value")
            assert js("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Save changes').disabled")
            text('Fully reviewed inside the window')
            click('Approve displayed interval')
            click('Save changes')
            until(lambda: app._load_pending()[0].get('_verify_snapshot', {}).get('review_all_fields') is True)
            assert app._load_pending()[0]['start_time'] == legacy_start
            assert app._load_pending()[0]['stop_time'] == legacy_stop
            assert app._load_pending()[0]['description'] == 'Fully reviewed inside the window'
            until(lambda: js("!document.getElementById('work-description')"))
            print(json.dumps(dict(real_webkit=True, real_react=True, private_offline_start=True,
                                  same_interval_edit=True, project_move=True, hidden_draft=True,
                                  draft_after_stop=True, task_create_rename_archive_restore=True,
                                  pending_entry_edit=True, timer_conflict=True, account_conflict=True,
                                  full_interval_review=True, stop_retained=True, **metrics)), flush=True)
        except BaseException as error:
            errors.append(error)
            import traceback
            traceback.print_exc()
            try:
                print('Native readiness:', dict(mapped=window.native.get_mapped(),
                      injected=window.events._pywebviewready.is_set(), loaded=window.events.loaded.is_set()), flush=True)
                print('UI diagnostics:', js("JSON.stringify({url:location.href,ready:document.readyState,visibility:document.visibilityState,bridge:typeof window.pywebview,api:typeof window.pywebview?.api,methods:Object.keys(window.pywebview?.api??{}),readyMethod:typeof window.pywebview?.api?.ready,body:document.body.innerText,html:document.head.innerHTML})"), flush=True)
            except Exception as diagnostic:
                print('UI diagnostics failed:', str(diagnostic), flush=True)
            picture('failure.png')
        finally:
            bridge._quitting = True
            window.destroy()

    try:
        with patch.object(app.requests, 'request', side_effect=AssertionError('Account network is forbidden')) as network, \
             patch.object(app, '_play_sound'), patch.object(app, '_notify'), \
             patch.object(toggl_tasks, 'write', side_effect=metadata):
            webview.start(verify, gui='gtk', private_mode=True)
            network.assert_not_called()
        if errors:
            raise errors[0]
    finally:
        server.shutdown()
        server.server_close()
