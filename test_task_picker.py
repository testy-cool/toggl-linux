"""Picker actions preserve drafts on failure and ignore callbacks after closing."""
from unittest.mock import MagicMock, patch
import test_toggl_tray
from test_toggl_tray import reset_state, tmp_state_dir
import toggl_tray as app
import toggl_task_picker as picker
import toggl_projects


def view():
    v=picker.TaskPicker.__new__(picker.TaskPicker)
    v.app=app
    v.context=toggl_projects.context(app)
    v.window=MagicMock()
    v.busy=False
    for name in ['search','tree','refresh','use_project','start_task','message']:
        setattr(v,name,MagicMock())
    v._selection=MagicMock(return_value=dict(kind='task',project_id=7,description='Next task'))
    return v


def test_failed_action_keeps_selection_and_search():
    v=view()
    assert v._finish('Timer changed; reopen') is False
    v.search.set_text.assert_not_called()
    v.tree.set_cursor.assert_not_called()
    v.window.destroy.assert_not_called()
    v.message.set_text.assert_called_with('Timer changed; reopen')


def test_closed_picker_does_not_touch_destroyed_widgets_on_completion():
    v=view()
    v.window=None
    v._finish(None)
    v.search.set_sensitive.assert_not_called()


def test_parent_row_does_not_start_a_timer():
    v=view()
    v._selection.return_value=dict(kind='project',project_id=7)
    with patch.object(picker.threading,'Thread') as worker:
        v._apply(True)
    worker.assert_not_called()


def test_task_action_sends_both_fields_and_a_click_time_timer_snapshot():
    v=view()
    app.state.update(description='Current when clicked',entry_id=55)
    def immediate(target,daemon):
        result=MagicMock()
        result.start.side_effect=target
        return result
    with patch.object(picker.threading,'Thread',side_effect=immediate),patch.object(picker.toggl_task_tree,'select_task') as select:
        v._apply(True)
    assert select.call_args.args[:4]==(app,7,'Next task',v.context)
    assert select.call_args.args[4]['entry_id']==55


def test_project_action_only_assigns_project_and_keeps_interval(tmp_state_dir):
    v=view()
    def immediate(target,daemon):
        result=MagicMock()
        result.start.side_effect=target
        return result
    with patch.object(picker.threading,'Thread',side_effect=immediate),patch.object(picker.toggl_control,'dispatch') as dispatch:
        v._apply(False)
    assert dispatch.call_args.args[1]=='edit-current'
    assert dispatch.call_args.args[2]['changes']=={'project_id':7}
    assert dispatch.call_args.args[2]['context']==v.context
