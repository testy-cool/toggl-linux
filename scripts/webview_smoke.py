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
        js(f'{code}.click()')

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
            js("let input = document.getElementById('work-description'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Corrected inside the window'); input.dispatchEvent(new Event('input',{bubbles:true}));")
            until(lambda: js("document.getElementById('work-description').value === 'Corrected inside the window'"))
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
            click('Stop')
            until(lambda: not app.state['tracking'])
            assert app._load_pending(), 'The offline interval was not retained'
            print(json.dumps(dict(real_webkit=True, real_react=True, private_offline_start=True,
                                  same_interval_edit=True, stop_retained=True, **metrics)), flush=True)
        except BaseException as error:
            errors.append(error)
            import traceback
            traceback.print_exc()
            try:
                print('UI diagnostics:', js("JSON.stringify({url:location.href,ready:document.readyState,bridge:typeof window.pywebview,api:typeof window.pywebview?.api,body:document.body.innerText,html:document.head.innerHTML})"), flush=True)
            except Exception as diagnostic:
                print('UI diagnostics failed:', str(diagnostic), flush=True)
            picture('failure.png')
        finally:
            bridge._quitting = True
            window.destroy()

    try:
        with patch.object(app.requests, 'request', side_effect=AssertionError('Account network is forbidden')) as network, \
             patch.object(app, '_play_sound'), patch.object(app, '_notify'):
            webview.start(verify, gui='gtk', private_mode=True)
            network.assert_not_called()
        if errors:
            raise errors[0]
    finally:
        server.shutdown()
        server.server_close()
