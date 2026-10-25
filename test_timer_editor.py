import pytest
import test_toggl_tray
import toggl_tray as app
import toggl_timer_window as editor


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
    for name in ['toggle','new_task','save','description','project','start','message']:
        setattr(view,name,MagicMock())
    with patch.object(editor.threading,'Thread'):
        view._act('edit-current',{})
    for name in ['description','project','start']:
        getattr(view,name).set_sensitive.assert_called_once_with(False)
