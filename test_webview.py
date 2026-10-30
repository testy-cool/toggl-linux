"""The web UI can only use guarded commands against its fixed local socket."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import toggl_tray as app
import toggl_control as control
import toggl_projects
from toggl_webview import Bridge


def test_work_snapshot_uses_cached_history_without_account_requests():
    with patch.object(app, '_api') as api:
        result = control.dispatch(app, 'work-snapshot', {})
    assert result['state'] == app.state and isinstance(result['activities'], list)
    api.assert_not_called()


def test_hide_is_scoped_and_does_not_change_the_timer():
    original = dict(app.state)
    payload = dict(context=toggl_projects.context(app), project_id=None, description='Hidden work')
    with patch.object(app, '_api') as api:
        control.dispatch(app, 'hide-activity', payload)
    assert app.state == original
    api.assert_not_called()
    app.api_token = 'another-account'
    with pytest.raises(control.ControlError, match='changed'):
        control.dispatch(app, 'hide-activity', payload)


@pytest.mark.parametrize('action', ['set-api-base', 'arbitrary', 'open-file', None, ['switch']])
def test_bridge_rejects_actions_outside_the_ui_contract(action):
    with patch('toggl_control.request') as request:
        with pytest.raises(ValueError):
            Bridge(Path('/private-test')).command(action, {})
    request.assert_not_called()


def test_bridge_uses_the_fixed_socket_and_preserves_guard_payload():
    payload = dict(expected=dict(entry_id=11), context={'account': 'test'}, changes={'description': 'Literal text'})
    with patch('toggl_control.request', return_value={'state': {}}) as request:
        assert Bridge(Path('/private-test')).command('edit-current', payload) == {'state': {}}
    request.assert_called_once_with(Path('/private-test'), 'edit-current', payload)


def test_hide_keeps_the_same_webview_and_draft_in_memory():
    bridge = Bridge(Path('/private-test'), managed=True)
    bridge._window = MagicMock()
    bridge.close()
    bridge._window.hide.assert_called_once_with()
    bridge._window.destroy.assert_not_called()


def test_late_frontend_readiness_does_not_reopen_a_window_the_user_closed():
    bridge = Bridge(Path('/private-test'), managed=True)
    bridge._window = MagicMock()
    bridge.close()
    assert bridge.ready() == {'mode': 'work'}
    assert not bridge._visible
    bridge._window.show.assert_not_called()


def test_tray_reuses_one_window_process_and_does_not_pass_its_api_token():
    import toggl_webview_launcher as launcher
    view = launcher.Launcher(app)
    process = MagicMock()
    process.poll.return_value = None
    geometry = dict(x=10, y=40, width=600, height=780)
    with patch.object(launcher.importlib.util, 'find_spec', return_value=True), \
         patch.object(launcher.subprocess, 'Popen', return_value=process) as start, \
         patch.object(launcher.threading, 'Thread'), \
         patch.dict(launcher.os.environ, TOGGL_API_TOKEN='fixture-token'):
        view.show('work', True, geometry)
        view.show('timer', False, geometry)
    assert start.call_count == 1
    assert 'TOGGL_API_TOKEN' not in start.call_args.kwargs['env']
    process.stdin.write.assert_called_once()
    view.stop()
    process.stdin.close.assert_called_once()


def test_window_placement_stays_inside_each_monitor():
    from types import SimpleNamespace
    from toggl_webview_launcher import placement
    area = SimpleNamespace(x=2560, y=40, width=1920, height=1040)
    rect = placement(area, 4479, 1080)
    assert rect['x'] >= area.x and rect['y'] >= area.y
    assert rect['x']+rect['width'] <= area.x+area.width
    assert rect['y']+rect['height'] <= area.y+area.height


def test_socket_keeps_valid_unicode_descriptions_editable_and_stoppable():
    app.api_token = None
    original = '🧪' * 10000
    updated = '🕒' * 10000
    server = control.start_server(app)
    try:
        with patch.object(app, '_play_sound'):
            control.dispatch(app, 'start', {'changes': {'description': original}})
            result = control.request(app.STATE_DIR, 'edit-current', {
                'changes': {'description': updated}, 'expected': dict(app.state),
                'context': toggl_projects.context(app)})
            assert result['state']['description'] == updated
            result = control.request(app.STATE_DIR, 'stop', {'expected': result['state'],
                                                           'context': result['project_context']})
        assert not result['state']['tracking']
        assert app._pending_as_entries()[0]['description'] == updated
    finally:
        server.shutdown()
        server.server_close()


def test_socket_still_rejects_commands_larger_than_its_bounded_frame():
    with patch.object(control.socket, 'socket') as connection:
        with pytest.raises(control.ControlError, match='large'):
            control.request(Path('/private-test'), 'edit-current', {'description': 'x' * control.MAX_COMMAND})
    connection.return_value.__enter__.return_value.sendall.assert_not_called()


@pytest.mark.parametrize('count', [9, 100])
def test_history_response_stays_bounded_without_duplicate_or_partial_descriptions(count):
    import json
    activities = [dict(description=str(i) + '🧪' * 9990, last_used=i, count=1) for i in range(count)]
    groups = [dict(id=None, name='No project', last_used=count, activities=activities, tasks=activities)]
    server = control.start_server(app)
    try:
        with patch.object(control.toggl_task_tree, 'tree', return_value=groups):
            result = control.request(app.STATE_DIR, 'work-snapshot', {})
        assert len(json.dumps({'ok': True, 'result': result}, ensure_ascii=False).encode()) < control.MAX_MESSAGE
        returned = result['activities'][0]['activities']
        assert {k:v for k,v in returned[0].items() if k != 'key'} == activities[-1]
        assert all({k:v for k,v in row.items() if k != 'key'} in activities for row in returned)
        assert len(returned) + result['activities_omitted'] == count
        assert 'tasks' not in result['activities'][0]
        assert bool(result['activities_omitted']) == (count == 100)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize('missing', ['seat', 'pointer', 'primary', 'display'])
def test_position_handles_missing_native_devices_and_monitors(missing):
    from types import SimpleNamespace
    import toggl_webview_launcher as launcher
    from gi.repository import Gdk
    display = MagicMock()
    area = SimpleNamespace(x=1920, y=40, width=1280, height=720)
    display.get_primary_monitor.return_value.get_workarea.return_value = area
    display.get_monitor.return_value.get_workarea.return_value = area
    display.get_monitor_at_point.return_value.get_workarea.return_value = area
    display.get_default_seat.return_value.get_pointer.return_value = None
    if missing == 'seat': display.get_default_seat.return_value = None
    if missing == 'primary': display.get_primary_monitor.return_value = None
    with patch.object(Gdk.Display, 'get_default', return_value=None if missing == 'display' else display):
        geometry = launcher._position(None, None)
    assert geometry['width'] > 0 and geometry['height'] > 0
    if missing != 'display':
        assert geometry['x'] >= area.x and geometry['y'] >= area.y


def test_stopping_yesterdays_offline_timer_returns_its_exact_editable_interval():
    from datetime import datetime, timedelta, timezone
    app.api_token = None
    yesterday = (datetime.now(timezone.utc).astimezone().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)).isoformat()
    with patch.object(app, '_play_sound'):
        control.dispatch(app, 'start', {'changes': {'description': 'Overnight'}})
        control.dispatch(app, 'edit-current', {'changes': {'start': yesterday}})
        result = control.dispatch(app, 'stop', {'expected': dict(app.state)})
    assert not result['state']['tracking']
    assert any(entry['start'] == yesterday for entry in result['entries'])
    stopped = result['stopped_entry']
    assert stopped['start'] == yesterday and stopped['_offline']
    control.dispatch(app, 'edit-entry', {'entry': stopped, 'changes': {'description': 'Kept overnight'}})
    assert app._pending_as_entries()[0]['description'] == 'Kept overnight'


def test_legacy_pending_review_requires_approval_and_keeps_time_corrections():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    start, stop = (now-timedelta(hours=3)).isoformat(), (now-timedelta(hours=1)).isoformat()
    app._save_pending([dict(action='stop', entry_id=99, workspace_id=1, description='Older',
                            project_id=None, task_id=None, start_time=start, stop_time=stop,
                            _verify_running=True)])
    original = app._pending_as_entries()[0]
    changes = {'description': 'Reviewed', 'start': (now-timedelta(hours=2)).isoformat(),
               'stop': (now-timedelta(minutes=30)).isoformat()}
    before = app._load_pending()
    with pytest.raises(control.ControlError, match='approve'):
        control.dispatch(app, 'edit-entry', {'entry': original, 'changes': changes})
    assert app._load_pending() == before
    control.dispatch(app, 'edit-entry', {'entry': original, 'changes': changes, 'reviewed_interval': True})
    result = app._pending_as_entries()[0]
    assert result['start'] == changes['start'] and result['stop'] == changes['stop']
    assert result['description'] == changes['description']
    assert app._load_pending()[0]['_verify_snapshot']['review_all_fields'] is True


@pytest.mark.parametrize('changes', [{'start': 'invalid'}, {'stop': 'invalid'},
    {'start': '9999-01-01T00:00:00+00:00'}, {'stop': '2000-01-01T00:00:00+00:00'}])
def test_invalid_entry_times_cannot_approve_a_pending_review(changes):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    app._save_pending([dict(action='stop', entry_id=99, workspace_id=1, description='Older',
                            start_time=(now-timedelta(hours=2)).isoformat(),
                            stop_time=(now-timedelta(hours=1)).isoformat(), _verify_running=True)])
    original = app._pending_as_entries()[0]
    before = app._load_pending()
    with pytest.raises(control.ControlError, match='times'):
        control.dispatch(app, 'edit-entry', {'entry': original, 'changes': changes, 'reviewed_interval': True})
    assert app._load_pending() == before


@pytest.mark.parametrize('running', [False, True])
def test_cloud_time_corrections_send_consistent_duration_and_keep_timer_state(running):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    original_start = (now-timedelta(hours=2)).isoformat()
    start = (now-timedelta(hours=3)).isoformat()
    stop = (now-timedelta(hours=1)).isoformat()
    entry = dict(id=99, workspace_id=1, project_id=None, task_id=None, description='Cloud',
                 start=original_start, stop=None if running else stop, duration=-1 if running else 3600)
    app.api_token = 'fixture'
    app._control_entries = [entry]
    app._control_entries_date = now.astimezone().date().isoformat()
    app.state.update(tracking=running, entry_id=99 if running else None,
                     workspace_id=1, start_time=original_start if running else None)
    changes = {'start': start}
    with patch.object(app, 'update_entry') as update:
        control.dispatch(app, 'edit-entry', {'entry': dict(entry), 'changes': changes})
    sent = update.call_args.args[2]
    assert sent['start'] == start
    assert sent['duration'] == (-int(datetime.fromisoformat(start).timestamp()) if running else 7200)
    assert app.state['tracking'] is running
    if running:
        assert app.state['start_time'] == start
        assert 'start' not in app.state and 'duration' not in app.state
