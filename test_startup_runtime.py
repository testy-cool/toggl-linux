"""Cold cloud setup must not block the tray or overwrite concurrent user work."""
import threading
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
import pytest
import toggl_control as control
import toggl_tray as app
import toggl_watchdog


def test_startup_cloud_reply_cannot_overwrite_a_timer_changed_during_the_request():
    def reply(**_kwargs):
        app.state.update(tracking=True, entry_id=22, description='User switched',
                         start_time='2026-10-29T00:02:00+00:00')
        return dict(id=11, description='Old cloud reply', start='2026-10-29T00:00:00+00:00')
    with patch.object(app, 'fetch_current_entry', side_effect=reply), patch.object(app, 'save_state') as save:
        assert app._init_workspace() is False
    assert app.state['entry_id'] == 22 and app.state['description'] == 'User switched'
    save.assert_not_called()


def test_startup_pins_credentials_and_discards_a_reply_after_account_change():
    token, base = app.api_token, app.get_api_base()
    def reply(**kwargs):
        assert kwargs == {'token': token, 'base_url': base}
        app.api_token = 'replacement-test-token'
        return dict(id=11, start='2026-10-29T00:00:00+00:00')
    before = dict(app.state)
    with patch.object(app, 'fetch_current_entry', side_effect=reply), patch.object(app, 'save_state') as save:
        assert app._init_workspace() is False
    assert app.state == before
    save.assert_not_called()


def test_main_shows_tray_and_control_socket_before_a_slow_keyring_lookup():
    lookup_started, release_lookup = threading.Event(), threading.Event()
    order = []
    app.api_token = None
    def lookup():
        order.append('lookup')
        lookup_started.set()
        assert release_lookup.wait(3), 'Test did not release the keyring worker'
        return None
    class Icon:
        def __init__(self, *_args):
            order.append('icon-created')
        def run(self, setup):
            order.append('event-loop-ready')
            assert not lookup_started.is_set()
            setup(self)
            try:
                assert lookup_started.wait(2)
                assert self.visible is True
                snapshot = control.request(app.STATE_DIR, 'snapshot', {})
                assert snapshot['state']['description'] == ''
                assert not release_lookup.is_set()
            finally:
                release_lookup.set()
    with patch.object(app.sys, 'argv', ['toggl_tray.py', 'run']), \
         patch.object(app.pystray, 'Icon', Icon), patch.object(app, 'render_icon'), \
         patch.object(app, 'start_hotkey_listener'), patch.object(app, '_init_native_status_icon', return_value=False), \
         patch.object(app, '_update_panel_timer_label'), patch.object(app, 'get_api_token', side_effect=lookup), \
         patch.object(app, '_refresh_projects'), patch.object(app, '_refresh_tasks'), \
         patch.object(app.toggl_task_tree, 'refresh'), patch.object(app, 'sync_loop'), \
         patch.object(app.toggl_debug, 'DebugLog'):
        assert app.main() == 0
    assert order.index('event-loop-ready') < order.index('lookup')


def test_quit_during_keyring_lookup_does_not_start_cloud_work():
    stop = threading.Event()
    def lookup():
        stop.set()
        return 'test-token'
    app.api_token = None
    with patch.object(app, 'get_api_token', side_effect=lookup), \
         patch.object(app, '_init_workspace') as initialize, patch.object(app, 'sync_loop') as sync:
        app._bootstrap_and_sync(stop)
    initialize.assert_not_called()
    sync.assert_not_called()
    assert app.api_token is None


@pytest.mark.parametrize('entrypoint', ['startup', 'sync'])
def test_keyring_reply_for_an_old_server_is_discarded(entrypoint):
    base = ['https://old.example.test/api/v9']
    def lookup():
        base[0] = 'https://new.example.test/api/v9'
        return 'old-server-test-token'
    app.api_token = None
    with patch.object(app, 'get_api_base', side_effect=lambda: base[0]), \
         patch.object(app, 'get_api_token', side_effect=lookup), \
         patch.object(app, '_refresh_projects'), patch.object(app, '_refresh_tasks'), \
         patch.object(app.toggl_task_tree, 'refresh'), patch.object(app, 'sync_loop'):
        if entrypoint == 'startup':
            app._bootstrap_and_sync(threading.Event())
        else:
            app._run_sync_cycle()
    assert app.api_token is None


def test_legacy_keyring_migration_cannot_store_into_a_new_server(monkeypatch):
    monkeypatch.delenv('TOGGL_API_TOKEN', raising=False)
    base = ['https://old.example.test/api/v9']
    def lookup(command, **_kwargs):
        if 'opentickly' in command:
            base[0] = 'https://new.example.test/api/v9'
            return SimpleNamespace(returncode=0, stdout='legacy-server-test-token')
        return SimpleNamespace(returncode=1, stdout='')
    with patch.object(app, 'get_api_base', side_effect=lambda: base[0]), \
         patch.object(app.subprocess, 'run', side_effect=lookup), patch.object(app, 'store_api_token') as store:
        assert app.get_api_token() is None
    store.assert_not_called()


def test_legacy_lookup_cannot_overwrite_a_token_saved_while_it_was_running(monkeypatch):
    monkeypatch.delenv('TOGGL_API_TOKEN', raising=False)
    app.api_token = None
    def lookup(command, **_kwargs):
        if 'opentickly' in command:
            app.api_token = 'replacement-test-token'
            return SimpleNamespace(returncode=0, stdout='old-legacy-test-token')
        return SimpleNamespace(returncode=1, stdout='')
    with patch.object(app, 'get_api_base', return_value='https://same.example.test/api/v9'), \
         patch.object(app.subprocess, 'run', side_effect=lookup), patch.object(app, 'store_api_token') as store:
        assert app.get_api_token() is None
    assert app.api_token == 'replacement-test-token'
    store.assert_not_called()


def test_watchdog_records_one_stack_dump_per_stall_and_logs_recovery():
    now = [0.0]
    watchdog = toggl_watchdog.UIWatchdog(threshold=30, clock=lambda: now[0])
    with patch.object(app.toggl_debug.LOGGER, 'error') as error, \
         patch.object(app.toggl_debug.LOGGER, 'info') as info:
        now[0] = 31
        watchdog.check()
        watchdog.check()
        error.assert_called_once()
        assert 'Thread ' in error.call_args.args[2]
        assert 'test_watchdog_records' in error.call_args.args[2]
        assert watchdog.pulse() is True
        info.assert_called_once()
        now[0] = 62
        watchdog.check()
        assert error.call_count == 2
        watchdog.stop()
        assert watchdog.pulse() is False
