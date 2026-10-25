import pytest
import test_toggl_tray
import toggl_tray as app
import toggl_timer_window as editor
from unittest.mock import MagicMock, patch


def test_clock_only_start_correction_uses_today_after_midnight():
    yesterday = app.datetime.now().astimezone() - app.timedelta(days=1)
    baseline = dict(tracking=True, description='Work', project_id=7,
                    start_time=yesterday.replace(hour=18, minute=0).isoformat())
    changes = editor.editor_changes(app, baseline, 'Work', '7', '08:55')
    assert app._parse_iso(changes['start']).date() == app.datetime.now().astimezone().date()


def test_start_correction_can_explicitly_keep_the_previous_date():
    previous = app.datetime.now().astimezone() - app.timedelta(days=1)
    baseline = dict(tracking=True, description='Work', project_id=7,
                    start_time=previous.replace(hour=18, minute=0).isoformat())
    requested = previous.strftime('%Y-%m-%d') + ' 17:55'
    changes = editor.editor_changes(app, baseline, 'Work', '7', requested)
    parsed = app._parse_iso(changes['start'])
    assert parsed.date() == previous.date()
    assert (parsed.hour, parsed.minute) == (17, 55)


def test_clock_only_start_uses_today_even_when_the_clock_matches_yesterday():
    yesterday = (app.datetime.now() - app.timedelta(days=1)).astimezone()
    baseline = dict(tracking=True, description='Work', project_id=7,
                    start_time=yesterday.isoformat())
    changes = editor.editor_changes(app, baseline, 'Work', '7', yesterday.strftime('%H:%M'))
    assert app._parse_iso(changes['start']).date() == app.datetime.now().astimezone().date()


def test_unchanged_dated_start_preserves_seconds():
    previous = app.datetime.now().astimezone().replace(second=37)
    baseline = dict(tracking=True, description='Work', project_id=7, start_time=previous.isoformat())
    assert editor.editor_changes(app, baseline, 'Work', '7', previous.strftime('%Y-%m-%d %H:%M')) == {}


@pytest.mark.parametrize('action', ['stop', 'sync', 'refresh'])
def test_non_edit_actions_preserve_a_dirty_description_and_project(action):
    view = editor.TimerEditor.__new__(editor.TimerEditor)
    view.app = app
    view.busy = True
    view.dirty = True
    view.pending_action = action
    view.window = MagicMock()
    view.baseline = dict(tracking=True, description='Saved', project_id=7,
                         start_time='2026-10-25T10:00:00+00:00')
    for name in ['toggle', 'new_task', 'save', 'resume', 'description', 'project', 'start', 'message', 'reload']:
        setattr(view, name, MagicMock())
    view.description.get_text.return_value = 'Unsaved next task'
    view.project.get_active_id.return_value = '8'
    view._headers = MagicMock()
    view._load = MagicMock()
    state = dict(view.baseline, tracking=action != 'stop')
    view._finish({'state': state}, None)
    view._load.assert_not_called()
    view.description.set_text.assert_not_called()
    view.project.set_active_id.assert_not_called()
    assert view.dirty is True
    if action == 'stop':
        assert view.baseline['tracking'] is False


def test_more_settings_follow_current_tray_configuration():
    view = editor.TimerEditor.__new__(editor.TimerEditor)
    view.app = app
    view.updating = False
    view.panel_timer = MagicMock()
    view.panel_description = MagicMock()
    with patch.object(app, 'get_panel_timer_enabled', return_value=False), \
         patch.object(app, 'get_panel_description_enabled', return_value=True):
        view._settings_opened()
    view.panel_timer.set_active.assert_called_once_with(False)
    view.panel_description.set_active.assert_called_once_with(True)


def test_new_task_has_a_separate_keyboard_shortcut():
    view = editor.TimerEditor.__new__(editor.TimerEditor)
    view._new_task = MagicMock()
    view._save = MagicMock()
    view.rendered_state = {'tracking': True}
    with patch.object(editor.Gdk, 'KEY_Return', 65293), \
         patch.object(editor.Gdk.ModifierType, 'CONTROL_MASK', 4), \
         patch.object(editor.Gdk.ModifierType, 'SHIFT_MASK', 1):
        assert view._key(None, MagicMock(keyval=65293, state=5)) is True
    view._new_task.assert_called_once_with()
    view._save.assert_not_called()


def test_previous_tasks_show_recent_distinct_completed_tasks():
    def entry(id, start, description='Work', project=7, stop='2026-10-25T11:00:00Z'):
        return dict(id=id, start=start, stop=stop, duration=3600 if stop else -1,
                    description=description, project_id=project, workspace_id=99)
    newest = entry(3, '2026-10-25T10:00:00Z')
    other = entry(4, '2026-10-25T09:30:00Z', 'Other task')
    result = editor.previous_tasks(app, {'entries': [
        entry(1, '2026-10-25T08:00:00Z'), newest, other,
        entry(5, '2026-10-25T11:00:00Z', 'Active task', stop=None),
    ]})
    assert result == [newest, other]


def test_resume_request_fetches_history_when_no_previous_tasks_are_cached():
    view = editor.TimerEditor.__new__(editor.TimerEditor)
    view.app = app
    view.busy = False
    view._act = MagicMock()
    with patch.object(editor.toggl_control, 'snapshot', return_value={'entries': []}):
        view._resume_previous()
    view._act.assert_called_once_with('refresh', {})
    assert view.pending_resume_picker is True


def test_resume_selection_keeps_the_details_displayed_when_the_chooser_opened():
    entry = dict(id=1, start='2026-10-25T08:00:00Z', stop='2026-10-25T09:00:00Z',
                 duration=3600, description='Displayed task', project_id=7, workspace_id=99,
                 _pending_indexes=[0, 1])
    selected = editor.previous_tasks(app, {'entries': [entry]})[0]
    entry['description'] = 'Changed in Vicinae'
    entry['_pending_indexes'].append(2)
    assert selected['description'] == 'Displayed task'
    assert selected['_pending_indexes'] == [0, 1]


def test_unchanged_fields_make_no_request():
    state=dict(description="Work",project_id=7,tracking=False)
    assert editor.editor_changes(app,state,"Work","7","")=={}


def test_description_and_project_can_be_corrected_together():
    state=dict(description="Before",project_id=7,tracking=False)
    assert editor.editor_changes(app,state,"After","none","")=={"description":"After","project_id":None}


def test_invalid_start_text_is_rejected_before_saving_any_field():
    state=dict(description="Before",project_id=7,tracking=True,start_time="2026-10-25T10:00:00+00:00")
    with pytest.raises(ValueError,match="HH:MM"):
        editor.editor_changes(app,state,"After","7","bad")


def test_elapsed_header_contains_only_duration():
    start=app.datetime.now(app.timezone.utc)-app.timedelta(hours=1,minutes=2,seconds=3)
    state=dict(tracking=True,start_time=start.isoformat(),description="A very long description that belongs in its own field")
    assert editor.elapsed_text(app,state).startswith('1:02:0')
    assert editor.elapsed_text(app,dict(tracking=False))=='0:00:00'


def test_start_button_uses_the_displayed_state_and_supplies_guard():
    from unittest.mock import MagicMock
    view=editor.TimerEditor.__new__(editor.TimerEditor)
    view.app=app
    app.state.update(tracking=True,entry_id=99)
    view.rendered_state=dict(tracking=False,entry_id=None,description="",project_id=None,start_time=None)
    view.baseline=dict(view.rendered_state)
    view.description=MagicMock();view.description.get_text.return_value="New work"
    view.project=MagicMock();view.project.get_active_id.return_value="none"
    view._act=MagicMock()
    view._toggle()
    assert view._act.call_args.args[0]=='start'
    assert view._act.call_args.args[1]['expected']['tracking'] is False


def test_editor_fields_are_frozen_while_request_is_pending():
    from unittest.mock import MagicMock,patch
    view=editor.TimerEditor.__new__(editor.TimerEditor)
    view.app=app;view.busy=False
    for name in ['toggle','new_task','save','resume','description','project','start','message']:
        setattr(view,name,MagicMock())
    with patch.object(editor.threading,'Thread'):
        view._act('edit-current',{})
    for name in ['description','project','start']:
        getattr(view,name).set_sensitive.assert_called_once_with(False)


def test_start_with_dirty_fields_guards_the_state_those_fields_came_from():
    from unittest.mock import MagicMock
    view=editor.TimerEditor.__new__(editor.TimerEditor)
    view.app=app
    view.baseline=dict(tracking=False,entry_id=None,start_time=None,description="Old task",project_id=None)
    view.rendered_state=dict(view.baseline,description="Replacement task")
    app.state.update(view.rendered_state)
    view.description=MagicMock();view.description.get_text.return_value="Old task edited before replacement"
    view.project=MagicMock();view.project.get_active_id.return_value="none"
    view._act=MagicMock()
    view._toggle()
    assert view._act.call_args.args[0]=='start'
    assert view._act.call_args.args[1]['expected']==view.baseline
