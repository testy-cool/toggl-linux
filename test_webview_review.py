"""Claude review regressions: polling, startup, geometry and Unicode transport."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import toggl_control as control
import toggl_tray as app
import toggl_webview as host
import toggl_webview_launcher as launcher


def test_a_poll_waiting_for_pending_sync_does_not_take_the_timer_mutation_lock():
    waiting, release = threading.Event(), threading.Event()
    def pending(*_args):
        waiting.set()
        assert release.wait(2)
        return []
    with patch.object(app, '_pending_as_entries', side_effect=pending):
        thread = threading.Thread(target=control.dispatch, args=(app, 'work-snapshot', {}))
        thread.start()
        try:
            assert waiting.wait(1)
            acquired = app.toggle_lock.acquire(blocking=False)
            if acquired:
                app.toggle_lock.release()
            assert acquired, 'A read-only poll blocked tray Start/Stop while waiting for pending sync'
        finally:
            release.set()
            thread.join(3)
    assert not thread.is_alive()


def test_first_open_and_reopen_use_absolute_desktop_coordinates():
    class Event:
        def __init__(self): self.handlers=[]
        def __iadd__(self, fn): self.handlers.append(fn); return self
        def fire(self):
            for fn in self.handlers: fn()
    position=[]
    native=SimpleNamespace(move=lambda x,y: position.append((x,y)))
    window=SimpleNamespace(native=native, events=SimpleNamespace(closing=Event(),shown=Event()))
    # Emulate pywebview GTK's screen-relative positioning on monitor 0 at x=1920.
    def relative_move(x,y): native.move(1920+x,y)
    window.move=relative_move
    def create(*_args, **kwargs):
        if kwargs.get('x') is not None: relative_move(kwargs['x'],kwargs['y'])
        return window
    with patch.dict('sys.modules', {'webview':SimpleNamespace(create_window=create)}), \
         patch.object(app.GLib,'idle_add',side_effect=lambda fn,*args: fn(*args)):
        bridge=host.Bridge(Path('/private-test'))
        host.create_window(bridge,x=2000,y=80)
        window.events.shown.fire()
        assert position[-1] == (2000,80)
        host.move_window(window,2050,90)
        assert position[-1] == (2050,90)


def test_a_live_child_without_javascript_readiness_is_replaced_by_the_fallback():
    import os
    view=launcher.Launcher(app)
    process=MagicMock()
    process.poll.return_value=None
    view.process=process
    read_fd,write_fd=os.pipe()
    try:
        with patch.object(view,'_fallback') as fallback, \
             patch.object(app.GLib,'idle_add',side_effect=lambda fn,*args: fn(*args)):
            view._wait_for_ready(process,read_fd,'work',dict(x=10,y=40,width=600,height=780),timeout=0.01)
        process.terminate.assert_called_once()
        fallback.assert_called_once()
    finally:
        os.close(write_fd)


def test_late_failure_callback_does_not_open_a_fallback_for_a_replaced_child():
    view=launcher.Launcher(app)
    old,new=MagicMock(),MagicMock()
    view.process=new
    with patch.object(view,'_fallback') as fallback:
        view._fallback_current(old,'work',dict(x=0,y=0,width=600,height=780))
    fallback.assert_not_called()


def test_an_unpaired_surrogate_cannot_poison_future_socket_snapshots():
    server=control.start_server(app)
    try:
        result=control.request(app.STATE_DIR,'edit-current',{'changes':{'description':'\ud83d'}})
        assert result['state']['description'] == '\ud83d'
        assert control.request(app.STATE_DIR,'snapshot',{})['state']['description'] == '\ud83d'
        assert control.request(app.STATE_DIR,'work-snapshot',{})['state']['description'] == '\ud83d'
        control.request(app.STATE_DIR,'edit-current',{'changes':{'description':'Readable again'}})
        assert app.state['description'] == 'Readable again'
    finally:
        server.shutdown()
        server.server_close()


def test_javascript_readiness_acknowledges_the_private_pipe_without_fallback():
    import os
    read_fd,write_fd=os.pipe()
    view=launcher.Launcher(app)
    process=MagicMock()
    view.process=process
    bridge=host.Bridge(Path('/private-test'),managed=True,ready_fd=write_fd)
    assert bridge.ready() == {'mode':'work'}
    with patch.object(view,'_fallback') as fallback:
        view._wait_for_ready(process,read_fd,'work',dict(x=0,y=0,width=600,height=780),timeout=0.1)
    assert view.ready_process is process
    process.terminate.assert_not_called()
    fallback.assert_not_called()


def test_a_crash_after_readiness_waits_for_the_next_explicit_open_request():
    view=launcher.Launcher(app)
    process=MagicMock()
    process.wait.return_value=1
    view.process=process
    view.ready_process=process
    with patch.object(view,'_fallback') as fallback, \
         patch.object(app.GLib,'idle_add',side_effect=lambda fn,*args: fn(*args)):
        view._watch(process,'work',dict(x=0,y=0,width=600,height=780))
    fallback.assert_not_called()
