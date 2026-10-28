"""Rows of the left-click popup, built without a desktop."""
import toggl_tray as app
import toggl_tray_popup as popup

PROJECTS = [dict(id=4, workspace_id=1, name='Stay Safe', color='#0b83d9', active=True),
            dict(id=1, workspace_id=1, name='DPF', color='#e28c1c', active=True)]


def group(pid, name, *activities):
    return dict(id=pid, name=name, count=len(activities), last_used=1,
                activities=[dict(project_id=pid, description=t, count=1, last_used=1) for t in activities])


def test_rows_separate_real_tasks_from_collapsed_recent_activities():
    groups = [group(4, 'Stay Safe', 'Onlysider', 'Planning'), group(None, 'No project', 'Misc'), group(1, 'DPF')]
    tasks = [dict(id=31, project_id=4, workspace_id=1, name='Stay Safe task', active=True)]
    current = dict(tracking=True, project_id=4, task_id=31, description='Specific details')
    rows = popup.rows(groups, PROJECTS, current, tasks=tasks)
    assert [(r['kind'], r['label'], r['active']) for r in rows] == [
        ('project', 'Stay Safe', True), ('task', 'Stay Safe task', True),
        ('activity-toggle', 'Show 2 recent activities', False),
        ('project', 'No project', False), ('activity-toggle', 'Show 1 recent activity', False),
        ('project', 'DPF', False)]
    assert rows[0]['color'] == '#0b83d9' and rows[3]['color'] == popup.NO_PROJECT_COLOR


def test_rows_cap_expanded_activities_search_all_and_hide_empty_no_project():
    many = group(4, 'Stay Safe', *[f't{i}' for i in range(9)])
    stopped = dict(tracking=False)
    assert sum(r['kind'] == 'activity' for r in popup.rows([many], PROJECTS, stopped, expanded={4})) == popup.ACTIVITIES_PER_PROJECT
    assert sum(r['kind'] == 'activity' for r in popup.rows([many], PROJECTS, stopped, query='t')) == 9
    assert popup.rows([group(None, 'No project')], PROJECTS, stopped) == []
    assert not any(r['active'] for r in popup.rows([many], PROJECTS, stopped))


def test_search_filters_real_tasks_and_activities_without_losing_their_project():
    groups = [group(4, 'Stay Safe', 'Review notes', 'Plan week')]
    tasks = [dict(id=31, project_id=4, workspace_id=1, name='Build UI', active=True)]
    task_rows = popup.rows(groups, PROJECTS, dict(tracking=False), query='build', tasks=tasks)
    assert [(row['kind'], row['label']) for row in task_rows] == [
        ('project', 'Stay Safe'), ('task', 'Build UI')]
    activity_rows = popup.rows(groups, PROJECTS, dict(tracking=False), query='review', tasks=tasks)
    assert [(row['kind'], row['label']) for row in activity_rows] == [
        ('project', 'Stay Safe'), ('activity-heading', 'Recent activities'), ('activity', 'Review notes')]


def test_project_row_explains_that_it_starts_when_timer_is_stopped():
    stopped = popup.rows([group(4, 'Stay Safe')], PROJECTS, dict(tracking=False))[0]
    running = popup.rows([group(4, 'Stay Safe')], PROJECTS, dict(tracking=True))[0]

    assert stopped['hint'] == 'Start a timer with this project'
    assert running['hint'] == 'Use this project for the current timer'


def test_stopped_project_choice_starts_blank_instead_of_reusing_old_task():
    stopped = dict(tracking=False, project_id=4, description='Stay Safe')

    assert popup.project_action(stopped, 6) == ('start', {'project_id': 6, 'task_id': None, 'description': ''})
    assert popup.project_action(dict(stopped, tracking=True), 6) == ('edit-current', {'project_id': 6, 'task_id': None})


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
