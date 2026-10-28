"""Project changes from the tray keep the existing timer interval intact."""
from types import SimpleNamespace
from unittest.mock import patch

import toggl_tray as app
import toggl_control as control


PROJECTS = [dict(id=7, workspace_id='ws_123', name='DPF', active=True),
            dict(id=8, workspace_id='elsewhere', name='Other workspace', active=True)]


def test_tray_project_choices_include_no_project_and_current_workspace_only(tmp_state_dir):
    def item(text, action, **options):
        return SimpleNamespace(text=text, action=action, **options)
    with patch.object(app, '_cached_projects', return_value=PROJECTS), \
         patch.object(app.pystray, 'MenuItem', side_effect=item):
        items = app._tray_project_items()
    assert [i.text for i in items if isinstance(i, SimpleNamespace) and getattr(i, 'radio', False)][:2] == ['No project', 'DPF']
    assert 'Other workspace' not in [i.text for i in items if isinstance(i, SimpleNamespace)]
    no_project = next(i for i in items if isinstance(i, SimpleNamespace) and i.text == 'No project')
    assert no_project.checked(None) is True


def test_tray_project_choice_updates_pending_timer_without_splitting_it(tmp_state_dir):
    app.api_token = None
    app.state.update(tracking=True, entry_id=None, start_time='2026-10-25T10:00:00+00:00', description='Work')
    app.queue_action('start', start_time=app.state['start_time'], description='Work')
    before = dict(app.state)
    with patch.object(app, '_cached_projects', return_value=PROJECTS):
        assert app._apply_tray_project(7, before) is True
    assert app.state['project_id'] == 7
    assert app.state['start_time'] == before['start_time']
    assert app.state['description'] == before['description']
    assert [q['action'] for q in app._load_pending()] == ['start']
    assert app._load_pending()[0]['project_id'] == 7


def test_tray_project_choice_updates_existing_cloud_interval(tmp_state_dir):
    app.state.update(tracking=True, entry_id=101, start_time='2026-10-25T10:00:00+00:00', description='Work')
    before = dict(app.state)
    with patch.object(app, '_cached_projects', return_value=PROJECTS), patch.object(app, 'update_entry') as update:
        assert app._apply_tray_project(7, before) is True
    update.assert_called_once_with('ws_123', 101, {'project_id': 7, 'task_id': None})
    assert app.state['entry_id'] == 101
    assert app.state['start_time'] == before['start_time']


def test_stale_tray_project_choice_does_not_edit_a_replacement_timer(tmp_state_dir):
    before = dict(app.state)
    app.state['description'] = 'Replacement'
    with patch.object(app, 'update_entry') as update, patch.object(app, '_notify') as notify:
        assert app._apply_tray_project(7, before) is False
    update.assert_not_called()
    assert app.state['project_id'] is None
    assert 'changed' in notify.call_args.args[0].lower()


def test_selecting_the_checked_project_spends_no_cloud_request(tmp_state_dir):
    app.state.update(tracking=True, entry_id=101, project_id=7, start_time='2026-10-25T10:00:00+00:00')
    with patch.object(app, 'update_entry') as update:
        assert app._apply_tray_project(7, dict(app.state)) is True
    update.assert_not_called()


def test_cloud_switch_during_project_validation_cannot_edit_the_replacement(tmp_state_dir):
    app.state.update(tracking=True, entry_id=101, start_time='2026-10-25T10:00:00+00:00')
    before = dict(app.state)
    original = control._changes
    def change_timer(*args, **kwargs):
        changes = original(*args, **kwargs)
        app.state.update(entry_id=202, description='Replacement')
        return changes
    with patch.object(app, '_cached_projects', return_value=PROJECTS), \
         patch.object(control, '_changes', side_effect=change_timer), \
         patch.object(app, 'update_entry') as update, patch.object(app, '_notify'):
        assert app._apply_tray_project(7, before) is False
    update.assert_not_called()
    assert app.state['project_id'] is None


def test_project_menu_uses_the_timer_current_at_click_time(tmp_state_dir):
    app.state.update(tracking=True, entry_id=101, start_time='2026-10-25T10:00:00+00:00')
    menu_state = dict(app.state)
    callback = app._project_choice_action(7, menu_state)
    app.state.update(entry_id=202, description='Current when clicked')
    def immediate_thread(target, args, daemon):
        return SimpleNamespace(start=lambda: target(*args))
    with patch.object(app.threading, 'Thread', side_effect=immediate_thread), \
         patch.object(app, '_cached_projects', return_value=PROJECTS), \
         patch.object(app, 'update_entry') as update, patch.object(app, '_notify') as notify:
        callback(None, None)
    update.assert_called_once_with('ws_123', 202, {'project_id': 7, 'task_id': None})
    notify.assert_not_called()


def test_old_workspace_menu_cannot_assign_a_project_in_the_new_workspace(tmp_state_dir):
    callback = app._project_choice_action(7, dict(app.state))
    app.state['workspace_id'] = 'elsewhere'
    with patch.object(app, '_apply_tray_project') as apply, patch.object(app, '_notify') as notify:
        callback(None, None)
    apply.assert_not_called()
    assert 'changed' in notify.call_args.args[0].lower()
