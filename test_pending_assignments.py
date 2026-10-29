"""Pending entry assignments survive edits, replay and running-timer display."""
from unittest.mock import patch
import pytest
import toggl_tray as app

START = '2026-10-29T00:00:00+00:00'
STOP = '2026-10-29T00:01:00+00:00'


@pytest.fixture
def metadata():
    projects = [dict(id=7, workspace_id='ws_123', name='Before'),
                dict(id=8, workspace_id='ws_123', name='After')]
    tasks = [dict(id=31, project_id=7, workspace_id='ws_123', name='Before', active=True),
             dict(id=32, project_id=8, workspace_id='ws_123', name='After', active=True)]
    with patch.object(app, '_cached_projects', return_value=projects), \
         patch.object(app, '_cached_tasks', return_value=tasks):
        yield


@pytest.mark.parametrize('kind', ['paired', 'completed', 'remote-stop', 'open'])
def test_pending_assignment_is_persisted_for_each_queue_shape(metadata, kind):
    values = dict(start_time=START, description='Details', project_id=7, task_id=31)
    if kind == 'remote-stop':
        app.queue_action('stop', **values, entry_id=99, stop_time=STOP)
    elif kind == 'completed':
        app.queue_action('start', **values, stop_time=STOP)
    else:
        app.queue_action('start', **values)
        if kind == 'paired':
            app.queue_action('stop', stop_time=STOP)
    selected = app._pending_as_entries()[0]
    assert app._update_pending_entry(selected, {'project_id': '8', 'task_id': '32'})
    assert all(item.get('project_id') == 8 and item.get('task_id') == 32 for item in app._load_pending())
    operation = app._pending_operations(app._load_pending())[0]
    assert (operation['project_id'], operation['task_id']) == (8, 32)
    if kind in ('paired', 'completed'):
        payload = app._completed_entry_payload(operation, 'ws_123')
        assert (payload['project_id'], payload['task_id']) == (8, 32)


def test_changing_project_clears_an_incompatible_task(metadata):
    app.queue_action('start', start_time=START, project_id=7, task_id=31)
    assert app._update_pending_entry(app._pending_as_entries()[0], {'project_id': 8})
    assert app._load_pending()[0]['task_id'] is None


def test_open_pending_edit_updates_the_current_timer_and_saved_state(metadata):
    app.state.update(tracking=True, entry_id=None, start_time=START, project_id=7, task_id=31)
    app.queue_action('start', start_time=START, project_id=7, task_id=31)
    assert app._update_pending_entry(app._pending_as_entries()[0], {'project_id': 8, 'task_id': 32})
    assert (app.state['project_id'], app.state['task_id']) == (8, 32)
    assert app._load_json_with_backup(app.STATE_FILE, {})['task_id'] == 32


def test_stale_task_selection_is_rejected_without_changing_queue(metadata):
    app.queue_action('start', start_time=START, project_id=7, task_id=31)
    selected = app._pending_as_entries()[0]
    queue = app._load_pending()
    queue[0]['task_id'] = None
    app._save_pending(queue)
    assert app._update_pending_entry(selected, {'description': 'Old form'}) is False
    assert app._load_pending()[0].get('description', '') == ''


def test_invalid_task_does_not_report_success_or_modify_queue(metadata):
    app.queue_action('start', start_time=START, project_id=7, task_id=31)
    before = app._load_pending()
    assert app._update_pending_entry(app._pending_as_entries()[0], {'task_id': 999}) is False
    assert app._load_pending() == before
