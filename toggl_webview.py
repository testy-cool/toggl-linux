"""Isolated HTML work window. All timer/account writes go through the tray socket."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading

import toggl_control

ASSETS = Path(__file__).resolve().parent / 'toggl_web_assets'
ACTIONS = frozenset({'work-snapshot', 'refresh', 'refresh-today', 'sync', 'start', 'switch', 'resume', 'stop',
                     'edit-current', 'edit-entry', 'task-create', 'task-update',
                     'project-create', 'project-update', 'hide-activity'})


class Bridge:
    def __init__(self, state_dir, managed=False, mode='work', ready_fd=None):
        self._state_dir = Path(state_dir)
        self._window = None
        self._managed = managed
        self._mode = mode
        self._ready = threading.Event()
        self._quitting = False
        self._visible = False
        self._ready_fd = ready_fd

    def command(self, action, payload=None):
        """One bounded, allowlisted bridge; JavaScript cannot select a socket or file."""
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError('Unsupported work-panel command')
        payload = {} if payload is None else payload
        if not isinstance(payload, dict):
            raise ValueError('Command payload must be an object')
        return toggl_control.request(self._state_dir, action, payload)

    def ready(self):
        self._ready.set()
        fd, self._ready_fd = self._ready_fd, None
        if fd is not None:
            try:
                os.write(fd, b'ready\n')
            except OSError:
                pass
            finally:
                os.close(fd)
        return {'mode': self._mode}

    def close(self):
        if self._managed:
            self._window.hide()
            self._visible = False
        else:
            self._window.destroy()

    def _closing(self):
        if self._managed and not self._quitting:
            self.close()
            return False

    def _listen(self):
        try:
            for line in sys.stdin:
                if len(line) > 4096:
                    continue
                try:
                    message = json.loads(line)
                    if message.get('action') != 'show':
                        continue
                    if not self._ready.wait(30):
                        continue
                    if message.get('toggle') and self._visible:
                        self.close()
                        continue
                    move_window(self._window, int(message['x']), int(message['y']))
                    self._window.show()
                    self._visible = True
                    if message.get('mode') == 'timer':
                        self._window.evaluate_js("window.dispatchEvent(new Event('toggl-edit-current'))")
                    elif message.get('mode') == 'today':
                        self._window.evaluate_js("window.dispatchEvent(new Event('toggl-open-today'))")
                except (ValueError, KeyError, TypeError):
                    continue
        finally:
            self._quitting = True
            self._window.destroy()


def move_window(window, x, y):
    from gi.repository import GLib
    # pywebview's GTK move() adds monitor 0's origin; tray coordinates are absolute.
    GLib.idle_add(window.native.move, x, y)


def create_window(bridge, *, x=None, y=None, width=600, height=780):
    import webview
    index = ASSETS / 'index.html'
    if not index.is_file():
        raise RuntimeError('The work interface is not built; run npm ci --prefix ui && npm run build --prefix ui')
    # Relative local content starts pywebview's static server at this asset directory,
    # never at the repository or the user-data folder.
    window = webview.create_window('Toggl Work', str(index), js_api=bridge,
                                    width=width, height=height,
                                    min_size=(360, 480), resizable=True,
                                    frameless=True, easy_drag=False, hidden=False,
                                    background_color='#17191d', text_select=True)
    bridge._window = window
    bridge._visible = True
    window.events.closing += bridge._closing
    if x is not None and y is not None:
        window.events.shown += lambda: move_window(window, x, y)
    return window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path.home()/'.local/share/toggl-tray')
    parser.add_argument('--managed', action='store_true')
    parser.add_argument('--mode', choices=('work', 'timer', 'today'), default='work')
    parser.add_argument('--x', type=int)
    parser.add_argument('--y', type=int)
    parser.add_argument('--width', type=int, default=600)
    parser.add_argument('--height', type=int, default=780)
    parser.add_argument('--ready-fd', type=int)
    args = parser.parse_args()
    bridge = Bridge(args.state_dir, args.managed, args.mode, args.ready_fd)
    create_window(bridge, x=args.x, y=args.y, width=args.width, height=args.height)
    import webview
    webview.settings['ALLOW_DOWNLOADS'] = False
    webview.settings['ALLOW_FILE_URLS'] = False
    webview.start(bridge._listen if args.managed else None, gui='gtk', private_mode=True)


if __name__ == '__main__':
    main()
