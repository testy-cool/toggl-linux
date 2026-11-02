#!/usr/bin/env python3
"""Exercise the real React/WebKit window against an isolated, offline tray."""
import argparse
import atexit
from datetime import datetime, timedelta, timezone
import faulthandler
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

atexit.register(faulthandler.cancel_dump_traceback_later)
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
import toggl_task_tree
import toggl_tasks
import toggl_today
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
        dict(id=7, workspace_id=1, name='Research', color='#7db8a0', active=True),
        dict(id=8, workspace_id=1, name='Empty project', active=True)])
    app._task_cache = dict(context=control.snapshot(app)['project_context'], tasks=[
        dict(id=31, workspace_id=1, project_id=6, name='Improve navigation', active=True),
        dict(id=32, workspace_id=1, project_id=7, name='Unused task', active=True)])
    app._task_capability = 'supported'
    now = datetime.now(timezone.utc)
    midnight = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    prior = dict(id=991, workspace_id=1, project_id=6, task_id=31,
                 description='Previous today session', start=midnight.isoformat(),
                 stop=(midnight+(now-midnight)/2).isoformat(), duration=1)
    recent = dict(id=992, workspace_id=1, project_id=6, task_id=None,
                  description='Review recent notes', start=(midnight+(now-midnight)/4).isoformat(),
                  stop=(midnight+3*(now-midnight)/4).isoformat(), duration=1)
    toggl_today.remember(app, prior)
    toggl_today.remember(app, recent)
    toggl_task_tree._save(app, dict(context=control.snapshot(app)['project_context'],
                                    fetched_at=time.time(), entries=[recent], local_entries=[],
                                    hidden_activities=[]))
    app._today_cache['fetched_at'] = time.time()  # A private, already-loaded cloud fixture.
    server = control.start_server(app)
    bridge = Bridge(root, managed=True)
    window = create_window(bridge, width=600, height=780)
    errors = []

    def js(code):
        return window.evaluate_js(code)

    def click(label):
        if label in ('New task', 'New timer'):
            click('New work')
            until(lambda: js("!!document.querySelector('[role=menuitem]')"))
            js(f"Array.from(document.querySelectorAll('[role=menuitem]')).find(e=>e.textContent.trim()==={json.dumps(label)}).click()")
            return
        code = f"(Array.from(document.querySelectorAll('button')).find(b => (b.getAttribute('aria-label') || b.textContent.trim()) === {json.dumps(label)}) || Array.from(document.querySelectorAll('[role=treeitem]')).find(e=>e.getAttribute('aria-label')==={json.dumps(label)})?.querySelector('[data-slot=tree-item-label]'))"
        until(lambda: js(f'Boolean({code}) && !({code}).disabled'))
        js(f"const button={code}; if(button.getAttribute('role')==='tab'||button.getAttribute('aria-haspopup')==='menu') button.dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',bubbles:true}})); else button.click()")

    def text(value):
        js(f"const input = document.getElementById('work-description'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,{json.dumps(value)}); input.dispatchEvent(new Event('input',{{bubbles:true}}));")
        until(lambda: js(f"document.getElementById('work-description')?.value === {json.dumps(value)}"))

    def search(value):
        js(f"const input=document.querySelector('input[aria-label=\"Search work\"]'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,{json.dumps(value)}); input.dispatchEvent(new Event('input',{{bubbles:true}}));")
        until(lambda: js(f"document.querySelector('input[aria-label=\"Search work\"]').value === {json.dumps(value)}"))

    def choose(label, value):
        selector = json.dumps(f'button[role="combobox"][aria-label="{label}"]')
        js(f"document.querySelector({selector}).dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',bubbles:true}}))")
        until(lambda: js("!!document.querySelector('[role=option]')"))
        js(f"Array.from(document.querySelectorAll('[role=option]')).find(e=>e.textContent.trim()==={json.dumps(value)}).click()")

    def reload_react():
        js("document.documentElement.dataset.smokeReload='old';location.reload()")
        def ready():
            try:
                return js("document.documentElement.dataset.smokeReload!=='old' && !!document.querySelector('[role=tree][aria-label=\"Projects and work\"]') && typeof window.pywebview?.api?.command==='function'")
            except Exception:
                return False
        until(ready, timeout=30)

    def menu(label, option):
        code = f"Array.from(document.querySelectorAll('button')).find(b=>b.getAttribute('aria-label')==={json.dumps('More actions for '+label)})"
        until(lambda: js(f'Boolean({code}) && !({code}).disabled'))
        js(f"({code}).dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',bubbles:true}}))")
        until(lambda: js("!!document.querySelector('[role=menuitem]')"))
        js(f"Array.from(document.querySelectorAll('[role=menuitem]')).find(e=>e.textContent.trim()==={json.dumps(option)}).click()")

    def metadata(_app, action, payload):
        assert payload['context'] == control.snapshot(app)['project_context']
        tasks = app._task_cache['tasks']
        if action == 'task-create':
            saved = dict(id=max([t['id'] for t in tasks]+[41])+1, workspace_id=1,
                         project_id=payload['project_id'], name=payload['name'], active=True)
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
            assert window.events.shown.wait(30), 'Native window never became visible'
            assert bridge._ready.wait(20), 'JavaScript bridge never became ready'
            until(lambda: window.native.get_mapped())
            until(lambda: js("document.body.textContent.includes('Improve navigation')"))
            before_theme = dict(app.state)
            for label, theme in (("GrokNight", "grok-night"), ("Gruvbox", "gruvbox")):
                click("Choose theme")
                until(lambda: js("!!document.querySelector('[role=menuitemradio]')"))
                js(f"Array.from(document.querySelectorAll('[role=menuitemradio]')).find(e=>e.textContent.trim()==={json.dumps(label)}).click()")
                until(lambda: js(f"document.documentElement.dataset.theme === {json.dumps(theme)}"))
                assert control.snapshot(app)["ui_theme"] == theme
                assert json.loads((app.STATE_DIR / "appearance.json").read_text())["theme"] == theme
                assert app.state == before_theme, "Theme changes must preserve the timer"
            reload_react()
            until(lambda: js("document.documentElement.dataset.theme === 'gruvbox'"))
            assert js("(()=>{const active=document.querySelector('[role=tab][data-state=active]'),inactive=document.querySelector('[role=tab][data-state=inactive]');return getComputedStyle(active).backgroundColor!==getComputedStyle(inactive).backgroundColor})()"), 'Selected tab must stay visible without focus'
            leaf = 'document.querySelector(\'[role=treeitem][aria-label="Unused task"]\')'
            until(lambda: js(f"!!({leaf})"))
            assert js(f"({leaf}).getAttribute('data-folder')") == 'false'
            assert not js(f"({leaf}).hasAttribute('aria-expanded')")
            assert not js(f"!!({leaf}).querySelector('.work-name svg,button[aria-label=\"Unused task\"]')")
            assert js("!!document.querySelector('button[aria-label=\"Start Unused task\"]')")
            assert js("!!document.querySelector('button[aria-label=\"Edit task Unused task\"]')")
            js(f"({leaf}).dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',code:'Enter',bubbles:true}}));({leaf}).dispatchEvent(new KeyboardEvent('keyup',{{key:'Enter',code:'Enter',bubbles:true}}));({leaf}).querySelector('.work-name').click()")
            assert not js("!!document.getElementById('work-description')")
            assert not app.state['tracking']
            assert js("document.querySelector('[role=treeitem][aria-label=\"Empty project\"]').getAttribute('data-folder')") == 'false'
            assert js("!!document.querySelector('button[aria-label=\"New task in Empty project\"]')")
            assert js("parseInt(getComputedStyle(document.querySelector('.project-name')).fontWeight)>=600"), 'Project headings must stand out from work rows'
            assert js("document.querySelector('[role=treeitem][aria-label=\"Unused task\"]').getAttribute('data-work-kind')==='task'")
            assert js("Array.from(document.querySelectorAll('.work-section-heading')).some(e=>e.textContent==='Saved tasks')")
            assert js("Array.from(document.querySelectorAll('.work-section-heading')).some(e=>e.textContent==='Recent activity')")
            assert js("document.querySelector('[role=treeitem][aria-label=\"Review recent notes\"]').getAttribute('data-work-kind')==='activity'")
            assert js("!!document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"] button.edit-entries')")
            assert js("!!document.querySelector('[role=treeitem][aria-label=\"Review recent notes\"] button.edit-entries')")
            assert not js("!!document.querySelector('.work-kind')"), 'Saved and recent work must be grouped instead of labeled per row'
            assert js("(()=>{const plus=document.querySelector('button[aria-label=\"New task in Studio\"]').getBoundingClientRect(),total=document.querySelector('.work-time').getBoundingClientRect();return plus.right<total.left})()"), 'Project creation must sit beside the project name, outside Today'
            assert not js("!!document.querySelector('[role=treeitem][aria-label=\"No project\"]')")
            fetched = app._today_cache['fetched_at']
            app._today_cache.update(fetched_at=None, incomplete=False)
            until(lambda: js("document.querySelector('footer').textContent.includes('estimated')"))
            assert js(f"({leaf}).querySelector('.work-time').textContent") == '—'
            app._today_cache['incomplete'] = True
            until(lambda: js("document.querySelector('footer').textContent.includes('estimated')"))
            assert not js("document.querySelector('footer').textContent.includes('Invalid Date')")
            assert js(f"({leaf}).getAttribute('data-folder')") == 'false'
            app._today_cache.update(fetched_at=fetched, incomplete=False)
            until(lambda: js(f"({leaf}).querySelector('.work-time').textContent==='0m'"))
            assert js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"] .work-name').tagName") == 'SPAN'
            js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]').focus()")
            assert js("document.activeElement === document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]')")
            control.record_delete(app, prior)
            until(lambda: js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]').getAttribute('data-folder')==='false'"))
            assert js("document.activeElement === document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]')")
            js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',code:'ArrowDown',bubbles:true}));document.activeElement.dispatchEvent(new KeyboardEvent('keyup',{key:'ArrowDown',code:'ArrowDown',bubbles:true}))")
            until(lambda: js("document.activeElement?.getAttribute('aria-label')==='Review recent notes'"))
            toggl_today.remember(app, prior)
            until(lambda: js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]').getAttribute('data-folder')==='true'"))

            # The first saved choice comes from a filtered tree. Projects absent
            # from that filter stay unknown, so they retain the expanded default.
            choose('Filter by project', 'Studio')
            until(lambda: not js("!!document.querySelector('[role=treeitem][aria-label=Research]')"))
            click('Improve navigation')
            until(lambda: js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]').getAttribute('aria-expanded')==='true'"))
            preferences_path = root/'ui-preferences.json'
            until(lambda: preferences_path.exists() and control.snapshot(app)['work_preferences']['expanded_tasks'] == ['31'])
            filtered_preferences = control.snapshot(app)['work_preferences']
            assert filtered_preferences == {
                'configured': True,
                'known_projects': ['6'],
                'expanded_projects': ['6'],
                'expanded_tasks': ['31'],
            }
            assert js("typeof localStorage === 'undefined'"), 'Private WebKit must not be mistaken for durable localStorage'

            # Reloading creates a fresh React tree and reads the private file.
            # The filter itself resets, the saved task reopens, and Research
            # remains default-expanded because it was unseen, not collapsed.
            reload_react()
            until(lambda: js("!!document.querySelector('[role=treeitem][aria-label=Research]')"))
            assert js("document.querySelector('button[aria-label=\"Filter by project\"]').textContent.trim()==='All projects'")
            until(lambda: js("document.querySelector('[role=treeitem][aria-label=Research]').getAttribute('aria-expanded')==='true'"))
            until(lambda: js("document.querySelector('[role=treeitem][aria-label=Studio]').getAttribute('aria-expanded')==='true'"))
            until(lambda: js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]').getAttribute('aria-expanded')==='true'"))
            assert js("typeof localStorage === 'undefined'")

            # A project that was an empty leaf was never explicitly collapsed.
            # Its first task must therefore be visible now and after a reload.
            click('New task in Empty project')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Created in empty project')
            assert js("document.querySelector('button[aria-label=Project]').textContent.trim()==='Empty project'")
            click('Create task')
            until(lambda: any(t['name'] == 'Created in empty project' for t in app._cached_tasks()))
            until(lambda: js("document.querySelector('[role=treeitem][aria-label=\"Empty project\"]').getAttribute('aria-expanded')==='true'"))
            assert js("!!document.querySelector('[role=treeitem][aria-label=\"Created in empty project\"]')")
            picture('created-empty-project.png')
            reload_react()
            until(lambda: js("document.querySelector('[role=treeitem][aria-label=\"Empty project\"]').getAttribute('aria-expanded')==='true'"))
            until(lambda: js("!!document.querySelector('[role=treeitem][aria-label=\"Created in empty project\"]')"))
            until(lambda: js("document.querySelector('[role=treeitem][aria-label=Research]').getAttribute('aria-expanded')==='true'"))
            until(lambda: js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]').getAttribute('aria-expanded')==='true'"))

            click('Studio')
            until(lambda: js("!Array.from(document.querySelectorAll('button')).some(b=>b.getAttribute('aria-label')==='Start Improve navigation')"))
            search('Research')
            until(lambda: js("document.querySelector('[role=treeitem][tabindex=\"0\"]')?.getAttribute('aria-label') === 'Research'"))
            js("window.treeErrors=[];window.addEventListener('error',e=>window.treeErrors.push(e.message));document.querySelector('[role=treeitem][aria-label=Research]').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',code:'ArrowRight',bubbles:true}))")
            assert js('window.treeErrors.length') == 0
            search('Improve')
            until(lambda: js("!!Array.from(document.querySelectorAll('button')).find(b=>b.getAttribute('aria-label')==='Start Improve navigation')"))
            search('')
            until(lambda: js("!Array.from(document.querySelectorAll('button')).some(b=>b.getAttribute('aria-label')==='Start Improve navigation')"))
            click('Studio')
            click('New task in Research')
            until(lambda: js("document.querySelector('button[aria-label=Project]')?.textContent.trim()==='Research'"))
            click('Cancel')
            until(lambda: js("!!document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]')"))
            if not js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]').getAttribute('aria-expanded')==='true'"):
                click('Improve navigation')
                until(lambda: js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]').getAttribute('aria-expanded')==='true'"))
            click('Improve navigation')
            until(lambda: js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]').getAttribute('aria-expanded')==='false'"))
            js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"] button.edit-entries').click()")
            until(lambda: js("document.activeElement?.getAttribute('aria-label')==='Correct times for Previous today session'"))
            assert not js("!!document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"] button.edit-entries')")
            assert not js("!!document.getElementById('work-description')")
            until(lambda: js("document.body.textContent.includes('Previous today session')"))
            assert not js("!!document.querySelector('input[aria-label=\"Start of Previous today session\"]')")
            assert js("!!document.querySelector('button[aria-label=\"Correct times for Previous today session\"]')")
            assert js("!/[AP]M/.test(document.querySelector('button[aria-label=\"Correct times for Previous today session\"]').textContent)"), 'Session ranges must use 24-hour local time'
            assert js("(()=>{const interval=document.querySelector('[data-interval]'),duration=interval.querySelector('.work-time').getBoundingClientRect(),parent=document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"] .work-time').getBoundingClientRect(),edit=interval.querySelector('[data-slot=item-actions]').getBoundingClientRect();return Math.abs(duration.right-parent.right)<=2&&edit.right<duration.left})()"), 'Session durations must align below task totals, with editing in its own area'
            js("const range=document.querySelector('button[aria-label=\"Correct times for Previous today session\"]');range.focus();range.click()")
            until(lambda: js("document.activeElement?.getAttribute('aria-label')==='Start of Previous today session'"))
            js("const done=Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Done');done.focus();done.click()")
            until(lambda: js("document.activeElement?.getAttribute('aria-label')==='Correct times for Previous today session'"))
            assert not js("!!document.querySelector('input[aria-label=\"Start of Previous today session\"]')")
            click('Edit entry Previous today session')
            until(lambda: js("!!document.getElementById('work-description')"))
            assert js("!!document.getElementById('work-start').value && !document.querySelector('input[type=datetime-local]')")
            click('Cancel')
            assert not app.state['tracking']
            code="Array.from(document.querySelectorAll('button')).find(b=>b.getAttribute('aria-label')==='More actions for Improve navigation')"
            until(lambda: js(f'Boolean({code})'))
            js(f"({code}).dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',bubbles:true}}))")
            until(lambda: js("!!document.querySelector('[role=menu]')"))
            js("document.querySelector('[role=menu]').click()")
            assert not js("!!document.getElementById('work-description')")
            assert js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]').getAttribute('aria-expanded')==='true'")
            js("document.querySelector('[role=menu]').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
            until(lambda: js("!document.querySelector('[role=menu]')"))
            click('Start Improve navigation')
            until(lambda: app.state['tracking'] and app.state['task_id'] == 31)
            started = app.state['start_time']
            until(lambda: js("!!document.querySelector('[data-testid=current-timer]')"))
            assert not js("!!document.querySelector('button[aria-label=\"Start Improve navigation\"]')"), 'Running work must not keep a redundant start control'
            assert js("document.querySelector('[data-running=true]').getAttribute('data-work-kind')==='task'"), 'Running work must retain its saved-task structure'
            app._task_cache['tasks'][0]['active'] = False
            until(lambda: js("!!document.querySelector('[data-running=true] button[aria-label^=\"Edit timer\"]')"))
            assert js("document.querySelector('[data-running=true]').getAttribute('data-work-kind')==='task'"), 'An archived current task must not be grouped as recent activity'
            app._task_cache['tasks'][0]['active'] = True
            until(lambda: js("document.querySelector('[data-running=true]')?.getAttribute('data-work-kind')==='task'"))
            assert not js("!!document.getElementById('work-description')")
            if not js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]').getAttribute('aria-expanded')==='true'"):
                click('Improve navigation')
                until(lambda: js("document.querySelector('[role=treeitem][aria-label=\"Improve navigation\"]').getAttribute('aria-expanded')==='true'"))
            app.state['description'] = 'Research notes on compact work panel hierarchy'
            until(lambda: js("document.querySelector('.current-title')?.textContent.trim()==='Research notes on compact work panel hierarchy'"))
            picture('work-panel.png')
            window.resize(375, 480)
            until(lambda: js("innerWidth===375 && innerHeight===480"))
            assert js("document.querySelector('.current-work').getBoundingClientRect().width>=300"), 'The running title must receive the full narrow row'
            assert js("document.querySelector('[data-testid=current-timer]').getBoundingClientRect().height<=80"), 'The two-line running timer must stay compact'
            assert js("(()=>{const work=document.querySelector('.current-work').getBoundingClientRect(),start=document.querySelector('.current-start').getBoundingClientRect(),title=document.querySelector('.current-title').getBoundingClientRect(),context=document.querySelector('.current-project').getBoundingClientRect();return work.bottom<=start.top&&context.top>=title.bottom-1})()"), 'Running work must use a readable secondary context line above its controls'
            assert js("document.querySelector('.current-work').title.includes('Research notes on compact work panel hierarchy')")
            assert js("document.querySelector('.current-work .current-project').textContent.includes('Improve navigation')")
            assert js("Array.from(document.querySelectorAll('.work-time')).every(e=>getComputedStyle(e).whiteSpace==='nowrap')")
            picture('compact-work.png')
            window.resize(600, 780)
            until(lambda: js("innerWidth===600 && innerHeight===780"))
            click('Edit running timer')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Corrected inside the window')
            picture('edit-timer.png')
            window.resize(375, 480)
            until(lambda: js("innerWidth===375 && innerHeight===480"))
            assert js("(()=>{const v=document.querySelector('[data-slot=scroll-area-viewport]').getBoundingClientRect(),task=document.querySelector('button[aria-label=Task]').getBoundingClientRect();return task.top>=v.top&&task.bottom<=v.bottom})()"), 'The compact editor must show Task without scrolling'
            assert not js("!!document.querySelector('.app-content > footer')")
            picture('compact-editor.png')
            window.resize(600, 780)
            until(lambda: js("innerWidth===600 && innerHeight===780"))
            click('Save changes')
            until(lambda: app.state['description'] == 'Corrected inside the window')
            assert app.state['start_time'] == started and app.state['task_id'] == 31
            assert app._load_pending()[0]['description'] == app.state['description']
            until(lambda: js("!document.getElementById('work-description')"))
            metrics = js("JSON.stringify({minFont:Math.min(...Array.from(document.querySelectorAll('body *')).filter(e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height).map(e=>parseFloat(getComputedStyle(e).fontSize))),width:document.documentElement.clientWidth,overflow:document.documentElement.scrollWidth>document.documentElement.clientWidth})")
            if isinstance(metrics, str):
                metrics = json.loads(metrics)
            assert metrics['minFont'] >= 12 and not metrics['overflow'], metrics
            painted_background = js("(()=>{const c=document.createElement('canvas');c.width=c.height=1;const x=c.getContext('2d');x.fillStyle=getComputedStyle(document.body).backgroundColor;x.fillRect(0,0,1,1);return '#'+Array.from(x.getImageData(0,0,1,1).data).slice(0,3).map(v=>v.toString(16).padStart(2,'0')).join('')})()")
            assert window.background_color.lower() == painted_background
            assert js("parseFloat(getComputedStyle(document.querySelector('.work-name')).fontSize)") == 14
            until(lambda: js("Array.from(document.fonts).some(f=>f.family.includes('Inter')&&f.status==='loaded')"))
            assert js("document.querySelector('[data-testid=current-timer]').getBoundingClientRect().height") <= 80
            click('Edit running timer')
            until(lambda: js("!!document.getElementById('work-description')"))
            assert js("parseFloat(getComputedStyle(document.querySelector('.app-header .current-project')).fontSize)") == 12
            assert not js("!!document.querySelector('.app-content > footer')"), 'Today controls must not take up editor space'
            choose('Project', 'Research')
            click('Save changes')
            until(lambda: app.state['project_id'] == 7)
            assert app.state['task_id'] is None and app.state['start_time'] == started
            until(lambda: js("!document.getElementById('work-description')"))
            click('Edit running timer')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Draft survives closing and stopping')
            js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',isComposing:true,bubbles:true}))")
            assert window.native.get_mapped()
            js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
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
            until(lambda: any(t['name'] == 'Created in the webview' for t in app._cached_tasks()))
            created_task_id = next(t['id'] for t in app._cached_tasks()
                                   if t['name'] == 'Created in the webview')
            until(lambda: js("!document.getElementById('work-description')"))
            click('Tasks')
            assert not js("!!document.querySelector('.work-kind')"), 'The Tasks tab does not need repeated Task labels'
            menu('Created in the webview', 'Edit task')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Renamed in the webview')
            click('Save changes')
            until(lambda: any(t['name'] == 'Renamed in the webview' for t in app._cached_tasks()))
            until(lambda: js("!document.getElementById('work-description')"))
            menu('Renamed in the webview', 'Archive task')
            until(lambda: next(t for t in app._cached_tasks()
                               if t['id'] == created_task_id)['active'] is False)
            until(lambda: js("document.body.textContent.includes('Restore')"))
            for compact_width in (375, 360):
                window.resize(compact_width, 480)
                until(lambda: js(f"innerWidth==={compact_width} && innerHeight===480"))
                assert js("(()=>{const row=document.querySelector('[data-archived]');return row.querySelector('.work-name').getBoundingClientRect().width>=240&&row.querySelector('.work-label').getBoundingClientRect().height<=40})()"), 'Archived task names must keep useful width beside Restore'
                picture(f'archived-task-{compact_width}.png')
            click('Restore')
            until(lambda: next(t for t in app._cached_tasks()
                               if t['id'] == created_task_id)['active'] is True)
            window.resize(600, 780)
            until(lambda: js("innerWidth===600 && innerHeight===780"))
            menu('Renamed in the webview', 'Edit task')
            until(lambda: js("document.body.textContent.includes('Edit saved task')"))
            click('Create in another project')
            until(lambda: js("document.body.textContent.includes('Create task in another project')"))
            until(lambda: js("document.activeElement?.id==='work-description'"))
            assert js("document.getElementById('work-description').value==='Renamed in the webview copy'")
            choose('Project', 'Research')
            click('Create task')
            until(lambda: any(t['name'] == 'Renamed in the webview copy' and t['project_id'] == 7
                              for t in app._cached_tasks()))
            original = next(t for t in app._cached_tasks()
                            if t['id'] == created_task_id)
            assert original['project_id'] == 6 and original['name'] == 'Renamed in the webview'
            until(lambda: js("!document.getElementById('work-description')"))
            menu('Renamed in the webview', 'Edit task')
            click('Create in another project')
            until(lambda: js("document.getElementById('work-description')?.value==='Renamed in the webview copy 2'"))
            text('RENAMED IN THE WEBVIEW')
            assert js("document.getElementById('work-description').getAttribute('aria-invalid')==='true'")
            assert js("document.body.textContent.includes('already exists in this workspace')")
            assert js("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Create task').disabled")
            click('Cancel')
            until(lambda: js("!document.getElementById('work-description')"))
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
            app._save_pending([])
            app.api_token = 'private-sync-fixture'
            app._project_cache['account'] = control.toggl_projects.account(app)
            app._task_cache['context'] = control.toggl_projects.context(app)
            time.sleep(3)  # Let the cached status poll observe the new private fixture account.
            click('New timer')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Pending timer opened from Today')
            with patch.object(app, 'start_entry', side_effect=app.requests.ConnectionError('offline fixture')):
                click('Start timer')
                until(lambda: app.state['tracking'] and app.state['entry_id'] is None)
            until(lambda: js("!document.getElementById('work-description')"))
            click('Today')
            until(lambda: js("document.body.textContent.includes('Pending timer opened from Today')"))
            click('Edit entry Pending timer opened from Today')
            until(lambda: js("document.body.textContent.includes('Edit running timer')"))
            text('Draft survives receiving a cloud ID')
            cloud = dict(id=701, workspace_id=1, project_id=app.state['project_id'], task_id=None,
                         description=app.state['description'], start=app.state['start_time'], stop=None, duration=-1)
            with patch.object(app, 'fetch_current_entry', return_value=None), patch.object(app, 'start_entry', return_value=cloud):
                assert app.sync_pending() == 0
            until(lambda: js("!document.body.textContent.includes('1 pending sync')"))
            until(lambda: js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.trim()==='Save changes'&&!b.disabled)"))
            assert js("document.getElementById('work-description').value === 'Draft survives receiving a cloud ID'")
            with patch.object(app, 'update_entry', return_value=cloud) as update:
                click('Save changes')
                until(lambda: app.state['description'] == 'Draft survives receiving a cloud ID')
                assert update.call_args.args[1] == 701
            until(lambda: js("!document.getElementById('work-description')"))
            click('Edit running timer')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Draft survives completed-entry sync')
            with patch.object(app, 'stop_entry', side_effect=app.requests.ConnectionError('offline fixture')):
                click('Stop timer and keep draft')
                until(lambda: not app.state['tracking'])
            until(lambda: js("document.body.textContent.includes('Edit time entry')"))
            pending = app._pending_as_entries()[0]
            completed = {key: pending.get(key) for key in ('id','workspace_id','project_id','task_id','description','start','stop','duration')}
            with patch.object(app, 'update_entry', return_value=completed):
                assert app.sync_pending() == 0
            with patch.object(app, 'update_entry', return_value=completed) as update:
                click('Save changes')
                until(lambda: update.called)
                until(lambda: js("!document.getElementById('work-description')"))
                assert update.call_args.args[1] == 701
                assert update.call_args.args[2] == {'description':'Draft survives completed-entry sync'}
            assert not app.state['tracking'] and not app._load_pending()
            click('New timer')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Typed start then Stop')
            first = dict(cloud, id=702, start=datetime.now(timezone.utc).isoformat(), stop=None, description='Typed start then Stop')
            with patch.object(app, 'start_entry', return_value=first):
                click('Start timer')
                until(lambda: app.state['tracking'] and app.state['entry_id'] == 702)
            started = "document.querySelector('input[aria-label=\"Started at\"]')"
            until(lambda: js(f'!!{started}'))
            def type_start(value):
                js(f"const input={started}; input.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,{json.dumps(value)}); input.dispatchEvent(new Event('input',{{bubbles:true}}));")
                until(lambda: js(f'{started}.value') == value)
            local_midnight = datetime.now(timezone.utc).astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            type_start('00:01')
            # Half-typed text stays with its timer: another timer starting must discard it.
            app.state.update(entry_id=703, start_time=(local_midnight+timedelta(minutes=2)).isoformat())
            until(lambda: js(f'{started}.value') == (local_midnight+timedelta(minutes=2)).strftime('%H:%M'))
            def slow_update(_workspace, entry_id, _changes, **_kwargs):
                time.sleep(0.4)
                return dict(first, id=entry_id)
            type_start('00:01')
            with patch.object(app, 'update_entry', side_effect=slow_update) as update, \
                 patch.object(app, 'stop_entry', return_value=dict(first, id=703, stop=datetime.now(timezone.utc).isoformat(), duration=60)) as stopped:
                # Leaving the field and clicking Stop arrive together, as they do with a mouse.
                js(f"{started}.dispatchEvent(new FocusEvent('focusout',{{bubbles:true}})); Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Stop').click()")
                until(lambda: stopped.called and not app.state['tracking'])
                assert update.call_count == 1 and update.call_args.args[1] == 703 and 'start' in update.call_args.args[2]
            click('New timer')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Offline interval before a new-work draft')
            with patch.object(app,'start_entry',side_effect=app.requests.ConnectionError('offline fixture')):
                click('Start timer')
                until(lambda: app.state['tracking'] and app.state['entry_id'] is None)
            until(lambda: js("!document.getElementById('work-description')"))
            click('New timer')
            until(lambda: js("!!document.getElementById('work-description')"))
            text('Next work survives current-timer sync')
            current = dict(id=901,workspace_id=1,project_id=app.state['project_id'],task_id=None,
                           description=app.state['description'],start=app.state['start_time'],stop=None,duration=-1)
            with patch.object(app,'fetch_current_entry',return_value=None),patch.object(app,'start_entry',return_value=current):
                assert app.sync_pending() == 0
            until(lambda: js("!document.body.textContent.includes('1 pending sync')"))
            assert js("document.getElementById('work-description').value === 'Next work survives current-timer sync'")
            next_entry=dict(current,id=902,start=datetime.now(timezone.utc).isoformat(),description='Next work survives current-timer sync')
            with patch.object(app,'stop_entry',return_value={}) as stop,patch.object(app,'start_entry',return_value=next_entry) as start:
                click('Start timer')
                until(lambda: app.state['entry_id'] == 902)
                until(lambda: js("!document.getElementById('work-description')"))
                assert stop.call_args.args[1] == 901 and start.call_count == 1
            with patch.object(app,'stop_entry',return_value={}):
                click('Stop')
                until(lambda: not app.state['tracking'])
            print(json.dumps(dict(real_webkit=True, real_react=True, persistent_theme_picker=True, private_offline_start=True,
                                  empty_work_leaf=True, empty_project_leaf=True, inert_leaf_keyboard=True,
                                  daily_loading_states=True, folder_to_leaf_focus=True, bundled_inter=True, work_font_px=14,
                                  row_click_expands_without_start=True, grouped_saved_and_recent=True,
                                  file_backed_expansion=True, fresh_react_expansion_reload=True,
                                  filtered_default_expansion=True, new_project_creation_visible=True,
                                  private_webkit_local_storage=False, daily_intervals=True,
                                  time_focus_transfer=True, edit_entries_focus=True,
                                  ranges_24_hour=True, running_title_priority=True,
                                  same_interval_edit=True, project_move=True, hidden_draft=True,
                                  draft_after_stop=True, task_create_rename_archive_restore=True,
                                  cross_project_task_copy=True, task_copy_validation=True,
                                  pending_entry_edit=True, timer_conflict=True, account_conflict=True,
                                  full_interval_review=True, draft_after_sync=True,
                                  pending_today_editor=True, new_draft_after_sync=True,
                                  stop_retained=True, **metrics)), flush=True)
        except BaseException as error:
            errors.append(error)
            import traceback
            traceback.print_exc()
            try:
                print('Native readiness:', dict(mapped=window.native.get_mapped() if window.native else False,
                      injected=window.events._pywebviewready.is_set(), loaded=window.events.loaded.is_set()), flush=True)
                print('Preference diagnostics:', control.snapshot(app)['work_preferences'], flush=True)
                print('UI diagnostics:', js("JSON.stringify({url:location.href,ready:document.readyState,visibility:document.visibilityState,bridge:typeof window.pywebview,api:typeof window.pywebview?.api,methods:Object.keys(window.pywebview?.api??{}),readyMethod:typeof window.pywebview?.api?.ready,body:document.body.innerText,html:document.head.innerHTML})"), flush=True)
            except Exception as diagnostic:
                print('UI diagnostics failed:', str(diagnostic), flush=True)
            picture('failure.png')
        finally:
            faulthandler.dump_traceback_later(20)
            print('Native shutdown: destroying work window', flush=True)
            bridge._quitting = True
            window.destroy()

    original_read = app.api_get
    def daily_read(path, **kwargs):
        if path == '/me/time_entries':
            return []  # Account-scoped read fixtures; never the user's service.
        return original_read(path, **kwargs)

    try:
        with patch.object(app.requests, 'request', side_effect=AssertionError('Account network is forbidden')) as network, \
             patch.object(app, 'api_get', side_effect=daily_read), \
             patch.object(app, '_play_sound'), patch.object(app, '_notify'), \
             patch.object(toggl_tasks, 'write', side_effect=metadata):
            webview.start(verify, gui='gtk', private_mode=True)
            print('Native shutdown: GUI loop finished', flush=True)
            network.assert_not_called()
        if errors:
            raise errors[0]
    finally:
        server.shutdown()
        server.server_close()
        print('Native shutdown: control server stopped', flush=True)
