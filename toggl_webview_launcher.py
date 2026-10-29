"""Keep one work-window process; the GTK tray never runs a second GUI loop."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import toggl_debug

_launcher = None


def placement(area, x, y):
    width, height = min(600, area.width), min(780, area.height)
    left = min(max(area.x, x-width//2), area.x+area.width-width)
    top = y if y < area.y+area.height//2 else y-height
    return dict(x=left, y=max(area.y, min(top, area.y+area.height-height)), width=width, height=height)


def _position(x, y):
    from gi.repository import Gdk
    display = Gdk.Display.get_default()
    if x is None or y is None:
        pointer = display.get_default_seat().get_pointer()
        if pointer:
            _, x, y = pointer.get_position()
        else:
            area = display.get_primary_monitor().get_workarea()
            x, y = area.x+area.width//2, area.y
    return placement(display.get_monitor_at_point(x, y).get_workarea(), x, y)


class Launcher:
    def __init__(self, app):
        self.app = app
        self.process = None
        self.lock = threading.Lock()
        self.stopping = False

    def show(self, mode, toggle, geometry):
        root = Path(__file__).resolve().parent
        if importlib.util.find_spec('webview') is None or not (root/'toggl_web_assets/index.html').is_file():
            self._fallback(mode, geometry)
            return
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.stdin.write(json.dumps(dict(action='show', mode=mode, toggle=toggle, **geometry))+'\n')
                    self.process.stdin.flush()
                    return
                except (BrokenPipeError, OSError):
                    pass
            command = [sys.executable, str(root/'toggl_webview.py'), '--managed', '--state-dir', str(self.app.STATE_DIR), '--mode', mode]
            for key, value in geometry.items():
                command.extend(('--'+key, str(value)))
            environment = os.environ.copy()
            environment.pop('TOGGL_API_TOKEN', None)
            self.app._ensure_private_state_dir()
            log_path = self.app.STATE_DIR/'work-ui.log'
            fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'a') as output:
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
                                                cwd=root, env=environment, text=True, start_new_session=True)
            self.stopping = False
            process = self.process
            threading.Thread(target=self._watch, args=(process, mode, geometry), daemon=True).start()

    def _watch(self, process, mode, geometry):
        code = process.wait()
        if code and not self.stopping and self.process is process:
            toggl_debug.LOGGER.error('Work window exited with status %s; see work-ui.log', code)
            self.app.GLib.idle_add(self._fallback, mode, geometry)

    def _fallback(self, mode, geometry):
        toggl_debug.LOGGER.warning('Web interface unavailable; using the GTK compatibility window')
        if mode == 'timer':
            import toggl_timer_window
            toggl_timer_window.show(self.app)
        else:
            import toggl_tray_popup
            toggl_tray_popup.toggle(self.app, geometry['x']+geometry['width']//2, geometry['y'])
        return False

    def stop(self):
        with self.lock:
            self.stopping = True
            if self.process is not None and self.process.stdin is not None:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass


def show(app, x=None, y=None, mode='work', toggle=False):
    global _launcher
    if _launcher is None:
        _launcher = Launcher(app)
    _launcher.show(mode, toggle, _position(x, y))
    return False


def stop():
    if _launcher is not None:
        _launcher.stop()
