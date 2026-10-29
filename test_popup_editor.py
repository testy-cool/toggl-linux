"""Exercise form commands against the actual guarded timer path."""
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

import toggl_control as control
import toggl_tray as app
import toggl_tray_popup as popup
from toggl_popup_editor import InlineEditor


def form(mode='current'):
    view = InlineEditor.__new__(InlineEditor)
    view.mode = mode
    view.snapshot = control.snapshot(app)
    view.original = deepcopy(app.state)
    view.description = MagicMock()
    view.description.get_text.return_value = 'Corrected description'
    view.project = MagicMock()
    view.project.get_active_id.return_value = 'none'
    view.task = MagicMock()
    view.task.get_active_id.return_value = 'none'
    view.projects = []
    view.task_choices = []
    view.submit = MagicMock()
    return view


def pending_timer():
    app.api_token = None
    app.state.update(tracking=True, entry_id=None, start_time='2026-10-29T12:00:00+00:00',
                     project_id=4, task_id=31, description='Original')
    app._save_pending([dict(action='start', start_time=app.state['start_time'],
                            workspace_id=app.state['workspace_id'], project_id=4,
                            task_id=31, description='Original')])


def test_inline_move_edits_pending_interval_without_restarting():
    pending_timer()
    view = form()
    view._save()
    action, payload, _ = view.submit.call_args.args
    started = app.state['start_time']
    control.dispatch(app, action, payload)
    assert app.state['description'] == 'Corrected description'
    assert app.state['project_id'] is None and app.state['task_id'] is None
    assert app.state['start_time'] == started and app.state['tracking']
    queue = app._load_pending()
    assert len(queue) == 1 and queue[0]['description'] == 'Corrected description'
    assert queue[0]['project_id'] is None and queue[0]['task_id'] is None


def test_inline_edit_rejects_external_timer_change_and_keeps_the_draft():
    pending_timer()
    view = form()
    view._save()
    action, payload, _ = view.submit.call_args.args
    app.state['description'] = 'Edited elsewhere'
    with pytest.raises(control.ControlError, match='changed'):
        control.dispatch(app, action, payload)
    assert app.state['description'] == 'Edited elsewhere'
    assert view.description.get_text() == 'Corrected description'
    assert app._load_pending()[0]['description'] == 'Original'


def test_description_only_save_does_not_try_to_reassign_an_archived_task():
    app.state.update(tracking=True, project_id=4, task_id=31)
    view = form()
    view.project.get_active_id.return_value = '4'
    view.task.get_active_id.return_value = '31'
    view._save()
    action, payload, _ = view.submit.call_args.args
    assert action == 'edit-current'
    assert payload['changes'] == dict(description='Corrected description')


def test_recent_work_editor_starts_a_new_interval_instead_of_rewriting_history():
    view = form('draft')
    view._save()
    action, payload, _ = view.submit.call_args.args
    assert action == 'switch'
    assert payload['expected'] == app.state
    assert payload['changes'] == dict(project_id=None, task_id=None, description='Corrected description')


def test_saving_a_recent_description_as_a_task_never_starts_a_timer():
    view = form('draft')
    view._save(as_task=True)
    action, payload, _ = view.submit.call_args.args
    assert action == 'task-create' and payload['name'] == 'Corrected description'
    assert 'changes' not in payload and 'expected' not in payload


def test_task_rename_sends_the_original_metadata_for_stale_target_checks():
    view = form('task')
    view.original = dict(id=31, project_id=4, workspace_id='ws_123', name='Old name', active=True)
    original = deepcopy(view.original)
    view._save()
    action, payload, _ = view.submit.call_args.args
    assert action == 'task-update'
    assert payload['task'] == original and payload['name'] == 'Corrected description'
    assert view.original == original


def panel():
    view = popup.TrayPopup.__new__(popup.TrayPopup)
    view.app = app
    view.context = popup.toggl_projects.context(app)
    view.window = MagicMock()
    view.editor = MagicMock()
    view.busy = False
    view._fill = MagicMock()
    view._say = MagicMock()
    return view


def test_refresh_and_focus_loss_keep_an_inline_draft():
    view = panel()
    assert view._tick() is True
    view._close_unless_busy()
    view._fill.assert_not_called()
    view.window.destroy.assert_not_called()


def test_failed_save_keeps_inputs_available_in_the_same_panel():
    view = panel()
    editor = view.editor
    view.busy = True
    view._cancel_edit = MagicMock()
    view._edit_finished('Timer changed; refresh before editing', 'Saved')
    assert view.editor is editor and not view.busy
    editor.widget.set_sensitive.assert_called_once_with(True)
    view._cancel_edit.assert_not_called()


def test_account_change_blocks_writes_but_leaves_draft_available_to_copy():
    view = panel()
    app.api_token = 'different-account'
    assert view._tick() is True
    view.editor.save.set_sensitive.assert_called_once_with(False)
    view.editor.widget.set_sensitive.assert_not_called()
    with patch.object(popup.threading, 'Thread') as worker:
        view._submit_edit('edit-current', {}, 'Saved')
    worker.assert_not_called()
    view.window.destroy.assert_not_called()


def test_unchanged_save_does_not_spend_an_api_request():
    view = panel()
    view._cancel_edit = MagicMock()
    with patch.object(popup.threading, 'Thread') as worker:
        view._submit_edit('edit-current', {'changes': {}}, 'Saved')
    worker.assert_not_called()
    view._cancel_edit.assert_called_once_with()


def test_account_change_cannot_hide_an_old_suggestion_in_the_new_account():
    view = panel()
    app.api_token = 'different-account'
    with patch.object(popup.toggl_task_tree, 'hide_activity') as hide:
        view._hide_edited_activity(dict(project_id=4, description='Old account work'))
    hide.assert_not_called()


def test_new_timer_form_uses_the_displayed_timer_instead_of_an_unseen_replacement():
    view = panel()
    view.editor = None
    view.rendered_state = dict(app.state, tracking=True, entry_id=11, description='Displayed timer')
    app.state.update(tracking=True, entry_id=22, description='Unseen replacement')
    view.scroll = MagicMock()
    view.pages = MagicMock()
    with patch.object(popup, 'InlineEditor', return_value=MagicMock()) as editor:
        view._open_editor('draft', row=dict(project_id=None, description='New work'))
    snapshot = editor.call_args.args[0]
    assert snapshot['state'] == view.rendered_state
    changes = dict(project_id=None, task_id=None, description='New work')
    with patch.object(app, 'toggle_tracking') as toggle:
        with pytest.raises(control.ControlError, match='changed'):
            control.dispatch(app, 'switch', dict(changes=changes, expected=snapshot['state'],
                                                context=snapshot['project_context']))
    toggle.assert_not_called()
    assert app.state['entry_id'] == 22


@pytest.mark.parametrize('kind,active,expected', [('task', True, 'current'),
                                              ('activity', True, 'current'),
                                              ('activity', False, 'draft'), ('task', False, 'task')])
def test_edit_controls_handle_running_and_saved_work_in_the_same_panel(kind, active, expected):
    view = panel()
    view.editor = None
    view._open_editor = MagicMock()
    row = dict(kind=kind, active=active, task=dict(id=31))
    view._edit_row(row)
    assert view._open_editor.call_args.args[0] == expected
