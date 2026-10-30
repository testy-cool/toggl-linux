"""Keep one work-window process; the GTK tray never runs a second GUI loop."""
import importlib.util
import json
import os
import select
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
    monitor = None
    if x is None or y is None:
        seat = display.get_default_seat() if display else None
        pointer = seat.get_pointer() if seat else None
        if pointer:
            _, x, y = pointer.get_position()
        else:
            monitor = (display.get_primary_monitor() or display.get_monitor(0)) if display else None
            area = monitor.get_workarea() if monitor else None
            x, y = (area.x+area.width//2, area.y) if area else (0, 0)
    monitor = (display.get_monitor_at_point(x, y) or monitor or display.get_monitor(0)) if display else None
    if monitor:
        return placement(monitor.get_workarea(), x, y)
    return dict(x=x, y=y, width=600, height=780)


class Launcher:
    def __init__(self, app):
        self.app = app
        self.process = None
        self.lock = threading.Lock()
        self.stopping = False
        self.failed_process = None
        self.ready_process = None
        self.requested = None

    def show(self, mode, toggle, geometry):
        root = Path(__file__).resolve().parent
        if importlib.util.find_spec('webview') is None or not (root/'toggl_web_assets/index.html').is_file():
            self._fallback(mode, geometry)
            return
        with self.lock:
            self.requested = (mode, geometry)
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
                read_fd, write_fd = os.pipe()
                try:
                    self.process = subprocess.Popen(command+['--ready-fd', str(write_fd)],
                                                    stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
                                                    pass_fds=(write_fd,), cwd=root, env=environment,
                                                    text=True, start_new_session=True)
                except OSError as error:
                    os.close(read_fd)
                    toggl_debug.LOGGER.error('Work window could not start: %s', error)
                    self.app.GLib.idle_add(self._fallback_current, self.process, mode, geometry)
                    return
                finally:
                    os.close(write_fd)
            self.stopping = False
            process = self.process
            threading.Thread(target=self._watch, args=(process, mode, geometry), daemon=True).start()
            threading.Thread(target=self._wait_for_ready, args=(process, read_fd, mode, geometry), daemon=True).start()

    def _watch(self, process, mode, geometry):
        code = process.wait()
        self._recover(process, mode, geometry, f'exited with status {code}')

    def _wait_for_ready(self, process, fd, mode, geometry, timeout=35):
        try:
            if select.select([fd], [], [], timeout)[0] and os.read(fd, 32) == b'ready\n':
                with self.lock:
                    self.ready_process = process
                return
            with self.lock:
                if self.stopping or self.process is not process:
                    return
                if process.poll() is None:
                    process.terminate()
            self._recover(process, mode, geometry, 'did not become ready')
        finally:
            os.close(fd)

    def _recover(self, process, mode, geometry, reason):
        with self.lock:
            if self.stopping or self.process is not process or self.failed_process is process:
                return
            self.failed_process = process
        toggl_debug.LOGGER.error('Work window %s; see work-ui.log', reason)
        self.app.GLib.idle_add(self._fallback_current, process, mode, geometry)

    def _fallback_current(self, process, mode, geometry):
        if not self.stopping and self.process is process:
            mode, geometry = self.requested or (mode, geometry)
            return self._fallback(mode, geometry)
        return False

    def _fallback(self, mode, geometry):
        toggl_debug.LOGGER.warning('Web interface unavailable; using the GTK compatibility window')
        if mode == 'timer':
            import toggl_timer_window
            toggl_timer_window.show(self.app)
        elif mode == 'today':
            self.app.on_view_today(None, None)
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
                if self.ready_process is not self.process and self.process.poll() is None:
                    self.process.terminate()


def show(app, x=None, y=None, mode='work', toggle=False):
    global _launcher
    if _launcher is None:
        _launcher = Launcher(app)
    _launcher.show(mode, toggle, _position(x, y))
    return False


def stop():
    if _launcher is not None:
        _launcher.stop()
