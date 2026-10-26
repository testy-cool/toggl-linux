"""Rows of the left-click popup, built without a desktop."""
from test_toggl_tray import reset_state
import toggl_tray as app
import toggl_tray_popup as popup

PROJECTS = [dict(id=4, workspace_id=1, name='Stay Safe', color='#0b83d9', active=True),
            dict(id=1, workspace_id=1, name='DPF', color='#e28c1c', active=True)]


def group(pid, name, *tasks):
    return dict(id=pid, name=name, count=len(tasks), last_used=1,
                tasks=[dict(project_id=pid, description=t, count=1, last_used=1) for t in tasks])


def test_rows_form_a_coloured_tree_with_the_running_task_marked():
    groups = [group(4, 'Stay Safe', 'Onlysider', 'Planning'), group(None, 'No project', 'Misc'), group(1, 'DPF')]
    current = dict(tracking=True, project_id=4, description='Onlysider')
    rows = popup.rows(groups, PROJECTS, current)
    assert [(r['kind'], r['label'], r['active']) for r in rows] == [
        ('project', 'Stay Safe', True), ('task', 'Onlysider', True), ('task', 'Planning', False),
        ('project', 'No project', False), ('task', 'Misc', False), ('project', 'DPF', False)]
    assert rows[0]['color'] == '#0b83d9' and rows[3]['color'] == popup.NO_PROJECT_COLOR
    assert [r['last'] for r in rows if r['kind'] == 'task'] == [False, True, True]


def test_rows_cap_tasks_unless_searching_and_hide_an_empty_no_project_group():
    many = group(4, 'Stay Safe', *[f't{i}' for i in range(9)])
    stopped = dict(tracking=False)
    assert sum(r['kind'] == 'task' for r in popup.rows([many], PROJECTS, stopped)) == popup.TASKS_PER_PROJECT
    assert sum(r['kind'] == 'task' for r in popup.rows([many], PROJECTS, stopped, query='t')) == 9
    assert popup.rows([group(None, 'No project')], PROJECTS, stopped) == []
    assert not any(r['active'] for r in popup.rows([many], PROJECTS, stopped))


def test_menu_item_opens_the_popup_even_without_a_pointer():
    from unittest.mock import MagicMock, patch
    display = MagicMock()
    display.get_default_seat.return_value.get_pointer.return_value = None
    area = display.get_primary_monitor.return_value.get_workarea.return_value
    area.x, area.y, area.width = 0, 0, 1000
    with patch.object(popup.Gdk.Display, 'get_default', return_value=display), \
         patch.object(popup, 'toggle') as toggle:
        popup.toggle_at_pointer(app)
    toggle.assert_called_once_with(app, 500, 0)
