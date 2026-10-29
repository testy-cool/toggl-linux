"""Pure task-window helpers enforce the visible task management contract."""
import pytest
from unittest.mock import MagicMock, patch

import toggl_task_window as window
import toggl_tray as app


@pytest.fixture
def editor():
    view = window.TaskEditor.__new__(window.TaskEditor)
    view.app = app
    view.context = {'account': 'test-account', 'workspace_id': 1}
    view.project_id = 6
    view.original = None
    view.pending_start_task = None
    view.start_after_save = True
    view.busy = False
    view.on_saved = MagicMock()
    for name in ('window', 'name', 'save', 'create_start', 'message'):
        setattr(view, name, MagicMock())
    view.name.get_text.return_value = 'Saved task'
    class Inline:
        def __init__(self, target, daemon): self.target = target
        def start(self): self.target()
    with patch.object(window.threading, 'Thread', Inline), \
         patch.object(window.GLib, 'idle_add', side_effect=lambda callback, *args: callback(*args)):
        yield view


def created(view):
    return {'saved_task': {'id': 41, 'project_id': 6, 'workspace_id': 1, 'name': 'Saved task'},
            'state': {'tracking': False, 'entry_id': None}, 'project_context': dict(view.context)}


def test_retry_after_partial_success_starts_the_saved_task_without_creating_again(editor):
    saved = created(editor)
    fresh = dict(saved, state={'tracking': True, 'entry_id': 99})
    with patch.object(window.toggl_control, 'request', side_effect=[saved, RuntimeError('Timer changed'), fresh, fresh]) as request:
        editor._save(True)
        assert editor.pending_start_task['id'] == 41
        editor.create_start.set_label.assert_called_with('Retry Start')
        editor._save(True)
    assert [call.args[1] for call in request.call_args_list] == ['task-create', 'switch', 'snapshot', 'switch']
    assert request.call_args_list[-1].args[2]['expected'] == fresh['state']
    editor.window.destroy.assert_called_once()
    editor.on_saved.assert_any_call(saved)


def test_retry_does_not_restart_a_task_already_started_by_a_timed_out_request(editor):
    saved = created(editor)
    current = dict(saved, state={'tracking': True, 'entry_id': 99, 'project_id': 6, 'task_id': 41})
    with patch.object(window.toggl_control, 'request', side_effect=[saved, TimeoutError('timeout'), current]) as request:
        editor._save(True)
        editor._save(True)
    assert [call.args[1] for call in request.call_args_list] == ['task-create', 'switch', 'snapshot']
    editor.window.destroy.assert_called_once()


def test_retry_rejects_an_account_change_without_switching_or_recreating(editor):
    saved = created(editor)
    changed = dict(saved, project_context={'account': 'other', 'workspace_id': 1})
    with patch.object(window.toggl_control, 'request', side_effect=[saved, RuntimeError('failed'), changed]) as request:
        editor._save(True)
        editor._save(True)
    assert [call.args[1] for call in request.call_args_list] == ['task-create', 'switch', 'snapshot']
    assert 'Account or workspace changed' in editor.message.set_text.call_args.args[0]
    editor.window.destroy.assert_not_called()


def test_failed_creation_keeps_the_draft_editable(editor):
    with patch.object(window.toggl_control, 'request', side_effect=RuntimeError('Offline')):
        editor._save(True)
    assert editor.pending_start_task is None
    editor.name.set_sensitive.assert_called_with(True)
    editor.window.destroy.assert_not_called()


def test_task_name_is_trimmed_and_bounded():
    assert window.task_name("  Real task  ") == "Real task"
    for value in ("", "   ", None, "x" * 201):
        with pytest.raises(ValueError, match="1–200"):
            window.task_name(value)


def test_project_tasks_filters_workspace_project_and_archived():
    snapshot = {
        "state": {"workspace_id": 1},
        "tasks": [
            {"id": 1, "workspace_id": 1, "project_id": 7, "name": "Active", "active": True},
            {"id": 2, "workspace_id": 1, "project_id": 7, "name": "Archived", "active": False},
            {"id": 3, "workspace_id": 1, "project_id": 8, "name": "Other project", "active": True},
            {"id": 4, "workspace_id": 2, "project_id": 7, "name": "Other workspace", "active": True},
        ],
    }
    assert [item["id"] for item in window.project_tasks(snapshot, 7)] == [1]
    assert [item["id"] for item in window.project_tasks(snapshot, 7, True)] == [1, 2]
