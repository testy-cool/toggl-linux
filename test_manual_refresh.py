"""Explicit refresh bypasses metadata age limits while retaining budget and throttling."""
from unittest.mock import call, patch
import toggl_control as control
import toggl_tray as app


def test_manual_refresh_fetches_projects_even_when_the_cache_is_fresh():
    old = dict(id=7, workspace_id='ws_123', name='Cached')
    new = dict(id=8, workspace_id='ws_123', name='Created elsewhere')
    app._project_cache = {'account': app._project_account(), 'fetched_at': app.time.time(), 'projects': [old]}
    app._last_project_refresh_attempt = app.time.monotonic()
    with patch.object(app, '_api', return_value=[old, new]) as api, \
         patch.object(app, '_refresh_tasks'), patch.object(app, 'fetch_today_entries', return_value=[]):
        result = control.dispatch(app, 'refresh', {'force': True})
    assert [project['id'] for project in result['projects']] == [7, 8]
    api.assert_called_once_with('GET', '/me/projects', token=app.api_token)


def test_manual_refresh_keeps_the_one_minute_throttle():
    with patch.object(app, '_refresh_projects') as projects, patch.object(app, '_refresh_tasks') as tasks, \
         patch.object(app, 'fetch_today_entries', return_value=[]):
        control.dispatch(app, 'refresh', {'force': True})
        control.dispatch(app, 'refresh', {'force': True})
    projects.assert_called_once_with(force=True)
    tasks.assert_called_once_with(force=True)


def test_forced_project_refresh_still_respects_request_budget_reserve():
    with patch.object(app, '_request_budget_remaining', return_value=app.REQUEST_BUDGET_BACKGROUND_RESERVE), \
         patch.object(app, '_api') as api, patch.object(app, '_refresh_tasks'):
        control.dispatch(app, 'refresh', {'force': True})
    api.assert_not_called()


def test_automatic_refresh_keeps_fresh_project_metadata():
    app._project_cache = {'account': app._project_account(), 'fetched_at': app.time.time(), 'projects': []}
    with patch.object(app, '_api') as api, patch.object(app, '_refresh_tasks'), \
         patch.object(app, 'fetch_today_entries', return_value=[]):
        control.dispatch(app, 'refresh', {})
        app._control_refreshed_at -= 61
        control.dispatch(app, 'refresh', {})
    api.assert_not_called()


def test_manual_refresh_is_not_swallowed_by_an_automatic_view_refresh():
    with patch.object(app, '_refresh_projects') as projects, patch.object(app, '_refresh_tasks'), \
         patch.object(app, 'fetch_today_entries', return_value=[]):
        control.dispatch(app, 'refresh', {})
        control.dispatch(app, 'refresh', {'force': True})
        control.dispatch(app, 'refresh', {'force': True})
    assert projects.call_args_list == [call(force=False), call(force=True)]
