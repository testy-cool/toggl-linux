"""Pending entry assignments survive edits, replay and running-timer display."""
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
import pytest
import toggl_tray as app
import toggl_entries_window as entries
import toggl_cli as cli

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


def test_future_start_is_rejected_before_queue_or_running_timer_changes(metadata):
    app.state.update(tracking=True, entry_id=None, start_time=START, project_id=7, task_id=31)
    app.queue_action('start', start_time=START, project_id=7, task_id=31)
    before, state = app._load_pending(), dict(app.state)
    future = (app.datetime.now(app.timezone.utc) + app.timedelta(hours=1)).isoformat()
    assert app._update_pending_entry(app._pending_as_entries()[0], {'start': future}) is False
    assert app._load_pending() == before
    assert app.state == state


def test_cli_rejects_future_pending_start_without_changing_active_state(capsys):
    start = (app.datetime.now(app.timezone.utc) - app.timedelta(minutes=10)).isoformat()
    app.state.update(tracking=True, entry_id=None, start_time=start)
    app.queue_action('start', start_time=start)
    state, queue = dict(app.state), app._load_pending()
    future = app.datetime.now(app.timezone.utc) + app.timedelta(minutes=5)
    with patch.object(cli, '_load_cli_runtime'), patch.object(app, '_parse_hhmm', return_value=future), \
         patch.object(app, 'save_state') as save:
        assert cli._cli_set_start(SimpleNamespace(time=future.strftime('%H:%M'))) == 2
    assert app.state == state and app._load_pending() == queue
    save.assert_not_called()
    assert 'Start time set' not in capsys.readouterr().out


def test_cli_does_not_change_active_state_after_a_rejected_pending_edit():
    start = (app.datetime.now(app.timezone.utc) - app.timedelta(minutes=10)).isoformat()
    app.state.update(tracking=True, entry_id=None, start_time=start)
    app.queue_action('start', start_time=start)
    before = dict(app.state)
    with patch.object(cli, '_load_cli_runtime'), patch.object(app, '_update_pending_entry', return_value=False), \
         patch.object(app, 'save_state') as save:
        assert cli._cli_set_start(SimpleNamespace(time='00:00')) == 2
    assert app.state == before
    save.assert_not_called()


def test_description_dialog_preserves_existing_assignments_when_metadata_is_missing():
    start = app.datetime.now(app.timezone.utc) - app.timedelta(minutes=5)
    stop = start + app.timedelta(minutes=1)
    entry = dict(start=start.isoformat(), stop=stop.isoformat(), duration=60,
                 description='Before', project_id=7, task_id=31, workspace_id='ws_123')
    widgets = [MagicMock(), MagicMock(), MagicMock()]
    for widget, value in zip(widgets, ['Changed', start.astimezone().strftime('%H:%M'),
                                      stop.astimezone().strftime('%H:%M')]):
        widget.get_text.return_value = value
    class Combo:
        def __init__(self): self.items = {}; self.selected = None
        def append(self, value, label): self.items[value] = label
        def remove_all(self): self.items.clear()
        def set_active_id(self, value): self.selected = value if value in self.items else None
        def get_active_id(self): return self.selected
        def set_sensitive(self, value): pass
        def connect(self, *_args): pass
    dialog = MagicMock()
    dialog.run.return_value = 1
    with patch.object(entries.Gtk.ResponseType, 'OK', 1), patch.object(entries.Gtk.ResponseType, 'REJECT', 2), \
         patch.object(entries.Gtk, 'Dialog', return_value=dialog), patch.object(entries.Gtk, 'Entry', side_effect=widgets), \
         patch.object(entries.Gtk, 'ComboBoxText', Combo), patch.object(entries.Gtk, 'events_pending', return_value=False), \
         patch.object(app.GLib, 'idle_add', side_effect=lambda callback: callback()), \
         patch.object(app, '_cached_projects', return_value=[]), patch.object(app, '_cached_tasks', return_value=[]):
        result = entries._gtk_edit_entry_dialog(entry)
    assert result == {'description': 'Changed'}
