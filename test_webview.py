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
