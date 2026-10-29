"""Rows of the left-click popup, built without a desktop."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import toggl_tray as app
import toggl_tray_popup as popup

PROJECTS = [dict(id=4, workspace_id=1, name='Stay Safe', color='#0b83d9', active=True),
            dict(id=1, workspace_id=1, name='DPF', color='#e28c1c', active=True)]


def group(pid, name, *activities):
    return dict(id=pid, name=name, count=len(activities), last_used=1,
                activities=[dict(project_id=pid, description=t, count=1, last_used=1) for t in activities])


def test_rows_show_recent_work_without_an_expand_step():
    groups = [group(4, 'Stay Safe', 'Onlysider', 'Planning'), group(None, 'No project', 'Misc'), group(1, 'DPF')]
    tasks = [dict(id=31, project_id=4, workspace_id=1, name='Stay Safe task', active=True)]
    current = dict(tracking=True, project_id=4, task_id=31, description='Specific details')
    rows = popup.rows(groups, PROJECTS, current, tasks=tasks)
    assert [(r['kind'], r['label'], r['active']) for r in rows] == [
        ('project', 'Stay Safe', True), ('task', 'Stay Safe task', True),
        ('activity-heading', 'Recent work', False), ('activity', 'Onlysider', False),
        ('activity', 'Planning', False),
        ('project', 'No project', False), ('activity-heading', 'Recent work', False),
        ('activity', 'Misc', False),
        ('project', 'DPF', False)]
    assert rows[0]['color'] == '#0b83d9' and rows[5]['color'] == popup.NO_PROJECT_COLOR


def test_rows_cap_expanded_activities_search_all_and_hide_empty_no_project():
    many = group(4, 'Stay Safe', *[f't{i}' for i in range(9)])
    stopped = dict(tracking=False)
    initial = popup.rows([many], PROJECTS, stopped)
    assert sum(r['kind'] == 'activity' for r in initial) == popup.ACTIVITIES_PER_PROJECT
    assert initial[-1]['label'] == 'Show 4 more'
    assert sum(r['kind'] == 'activity' for r in popup.rows([many], PROJECTS, stopped, expanded={4})) == 9
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
        ('project', 'Stay Safe'), ('activity-heading', 'Recent work'), ('activity', 'Review notes')]


def test_project_row_explains_that_it_starts_when_timer_is_stopped():
    stopped = popup.rows([group(4, 'Stay Safe')], PROJECTS, dict(tracking=False))[0]
    running = popup.rows([group(4, 'Stay Safe')], PROJECTS, dict(tracking=True))[0]

    assert stopped['hint'] == 'Start a new timer in this project'
    assert running['hint'] == stopped['hint']


def test_stopped_project_choice_starts_blank_instead_of_reusing_old_task():
    stopped = dict(tracking=False, project_id=4, description='Stay Safe')

    changes = {'project_id': 6, 'task_id': None, 'description': ''}
    assert popup.project_action(stopped, 6) == ('switch', changes)
    assert popup.project_action(dict(stopped, tracking=True), 6) == ('switch', changes)


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


NOW = datetime(2026, 10, 28, 15, tzinfo=timezone.utc)


def test_pinned_timer_identifies_the_project_when_activity_matches_another_project_name():
    projects = PROJECTS + [dict(id=6, workspace_id=1, name='Unified Studio')]
    current = dict(tracking=True, workspace_id=1, project_id=6, task_id=None,
                   description='Stay Safe', start_time=(NOW - timedelta(seconds=570)).isoformat())
    assert popup.running_timer(current, projects, [], now=NOW) == dict(
        title='Stay Safe', project='Unified Studio', task='', elapsed='0:09:30')


@pytest.mark.parametrize('kind', ['hidden', 'capped', 'search'])
def test_current_activity_remains_available_independently_of_browsable_history(kind):
    activities = [f'Recent {i}' for i in range(7)] + ['Running activity']
    groups = [group(4, 'Stay Safe', *([] if kind == 'hidden' else activities))]
    current = dict(tracking=True, workspace_id=1, project_id=4, task_id=None,
                   description='Running activity', start_time=NOW.isoformat())
    display_rows = popup.rows(groups, PROJECTS, current, query='unmatched' if kind == 'search' else '',
                              expanded=())
    assert not any(row['kind'] == 'activity' and row['active'] for row in display_rows)
    assert popup.running_timer(current, PROJECTS, [], now=NOW)['title'] == 'Running activity'


def test_current_archived_task_has_a_name_and_uncached_task_has_a_fallback():
    current = dict(tracking=True, workspace_id=1, project_id=4, task_id=31,
                   description='', start_time=NOW.isoformat())
    task = dict(id=31, workspace_id=1, project_id=4, name='Archived task', active=False)
    assert popup.running_timer(current, PROJECTS, [task], now=NOW)['title'] == 'Archived task'
    assert popup.running_timer(current, PROJECTS, [], now=NOW)['title'] == 'Current task'
    # Equal IDs belonging to another workspace must not supply labels.
    assert popup.running_timer(current, [], [dict(task, workspace_id=2)], now=NOW)['title'] == 'Current task'


def test_current_task_and_entry_description_both_remain_identifiable():
    current = dict(tracking=True, workspace_id=1, project_id=4, task_id=31,
                   description='Specific details', start_time=NOW.isoformat())
    task = dict(id=31, workspace_id=1, project_id=4, name='Reusable task')
    info = popup.running_timer(current, PROJECTS, [task], now=NOW)
    assert info['title'] == 'Specific details'
    assert info['task'] == 'Reusable task'
    assert popup.running_timer(dict(current, tracking=False), PROJECTS, [task], now=NOW) is None


def test_popup_tick_reloads_timer_changes_but_elapsed_ticks_keep_the_list_intact():
    view = popup.TrayPopup.__new__(popup.TrayPopup)
    view.app = app
    view.window = MagicMock()
    view.busy = False
    view.expanded = {4}
    view.search = MagicMock()
    view.search.get_text.return_value = 'kept search'
    view.context = popup.toggl_projects.context(app)
    view._render_key = None
    view._fill = MagicMock()
    view._update_current = MagicMock()
    with patch.object(popup.toggl_task_tree, 'tree', return_value=[]), \
         patch.object(app, '_cached_projects', return_value=[]), \
         patch.object(app, '_cached_tasks', return_value=[]):
        assert view._tick() is True
        view._fill.assert_called_once_with()
        view._fill.reset_mock()
        view._render_key = (dict(app.state), [], [], [])
        assert view._tick() is True
        view._fill.assert_not_called()
        view._update_current.assert_called_once_with(dict(app.state), [], [])
        app.state.update(tracking=True, entry_id=99)
        assert view._tick() is True
        view._fill.assert_called_once_with()
    assert view.expanded == {4}
    view.search.set_text.assert_not_called()


def test_closed_popup_ignores_saved_task_and_scroll_callbacks():
    view = popup.TrayPopup.__new__(popup.TrayPopup)
    view.window = None
    view.list = MagicMock()
    view.scroll = MagicMock()
    view._fill()
    assert view._restore_scroll(50) is False
    assert view._tick() is False
    view.list.get_children.assert_not_called()
    view.scroll.get_vadjustment.assert_not_called()


def test_stop_uses_the_timer_displayed_in_the_pinned_row():
    view = popup.TrayPopup.__new__(popup.TrayPopup)
    view.app = app
    view.window = MagicMock()
    view.busy = False
    view.rendered_state = dict(app.state, tracking=True, entry_id=11)
    view.context = popup.toggl_projects.context(app)
    view.list = MagicMock()
    view.stop = MagicMock()
    view.edit = MagicMock()
    view._say = MagicMock()
    app.state.update(tracking=True, entry_id=22)
    def inline(target, daemon):
        return MagicMock(start=target)
    with patch.object(popup.threading, 'Thread', side_effect=inline), \
         patch.object(popup.toggl_control, 'dispatch') as dispatch:
        view._stop_current()
    dispatch.assert_called_once_with(app, 'stop', {'expected': view.rendered_state, 'context': view.context})


def test_popup_tick_closes_when_account_changes():
    view = popup.TrayPopup.__new__(popup.TrayPopup)
    view.app = app
    view.window = MagicMock()
    view.context = popup.toggl_projects.context(app)
    app.api_token = 'changed-test-token'
    assert view._tick() is False
    view.window.destroy.assert_called_once_with()
