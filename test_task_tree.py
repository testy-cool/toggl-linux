"""History ranking and tray task selection must preserve interval/account boundaries."""
import importlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from test_toggl_tray import reset_state, tmp_state_dir
import toggl_tray as app
import toggl_projects

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
PROJECTS = [dict(id=i, workspace_id='ws_123', name=n, active=True) for i,n in [(7,'DPF'),(8,'Tools'),(9,'Unused')]]

def module():
    assert importlib.util.find_spec('toggl_task_tree') is not None, 'Tray project/task tree is not implemented'
    return importlib.import_module('toggl_task_tree')

def entry(i, project=7, description='Write', days=0, **kw):
    return dict(id=i, project_id=project, workspace_id='ws_123', description=description,
                start=(NOW-timedelta(days=days)).isoformat(), **kw)

def test_projects_rank_recent_first_with_frequent_projects_kept_readily_available():
    m=module()
    groups=m.group_projects(PROJECTS,[entry(1,8,'Build',days=1)]+[entry(i+2,7,days=5) for i in range(6)],'ws_123',now=NOW)
    assert [p['id'] for p in groups]==[8,7,None,9]
    assert groups[1]['count']==6
    assert groups[1]['tasks'][0]['count']==6

def test_same_task_is_deduplicated_but_same_description_in_different_projects_is_distinct():
    m=module()
    groups=m.group_projects(PROJECTS,[entry(1),entry(1),entry(2),entry(3,8)],'ws_123',now=NOW)
    assert [(p['id'],len(p['tasks']),p['count']) for p in groups[:2]]==[(7,1,2),(8,1,1)]

def test_search_matches_project_name_or_task_and_keeps_tree_parent():
    m=module()
    rows=[entry(1,7,'Write docs'),entry(2,7,'Review'),entry(3,8,'Build')]
    groups=m.group_projects(PROJECTS,rows,'ws_123',query='docs',now=NOW)
    assert [p['name'] for p in groups]==['DPF']
    assert [t['description'] for t in groups[0]['tasks']]==['Write docs']
    assert len(m.group_projects(PROJECTS,rows,'ws_123',query='dpf',now=NOW)[0]['tasks'])==2

def test_archived_other_workspace_unknown_and_old_history_do_not_offer_invalid_tasks():
    m=module()
    projects=PROJECTS+[dict(id=10,workspace_id='ws_123',name='Archived',active=False)]
    rows=[entry(1,10),entry(2,999),entry(3,days=31),dict(entry(4),workspace_id='other'),dict(entry(5),start='broken')]
    assert all(not p['tasks'] for p in m.group_projects(projects,rows,'ws_123',now=NOW))

def test_no_project_and_blank_description_remain_selectable():
    groups=module().group_projects(PROJECTS,[entry(1,None,'')],'ws_123',now=NOW)
    p=next(p for p in groups if p['id'] is None)
    assert p['tasks'][0]['description']==''

def test_cache_is_scoped_to_server_account_and_workspace(tmp_state_dir):
    m=module()
    scope=toggl_projects.context(app)
    app._atomic_write_text(tmp_state_dir/'task-history.json',json.dumps(dict(context=scope,entries=[entry(1)],fetched_at=0)))
    assert len(m.cached_entries(app))==1
    with patch.object(app,'API_BASE','https://other.example/api/v9'):
        assert m.cached_entries(app)==[]
    app.api_token='other'
    assert m.cached_entries(app)==[]

def test_refresh_pins_token_and_base_and_discards_reply_after_account_switch(tmp_state_dir):
    m=module()
    def switch(*a,**kw):
        app.api_token='replacement'
        return [entry(1)]
    with patch.object(app,'_api',side_effect=switch) as api:
        assert m.refresh(app,force=True) is False
    assert api.call_args.kwargs['token']=='tok'
    assert api.call_args.kwargs['base_url']==app.API_BASE
    assert not (tmp_state_dir/'task-history.json').exists()

def test_refresh_is_throttled_and_respects_background_request_reserve(tmp_state_dir):
    m=module()
    with patch.object(app,'_api',return_value=[]) as api:
        assert m.refresh(app,force=True) is True
        assert m.refresh(app) is False
        assert api.call_count==1
    with patch.object(app,'_request_budget_remaining',return_value=app.REQUEST_BUDGET_BACKGROUND_RESERVE),patch.object(app,'_api') as api:
        assert m.refresh(app,force=True) is False
        api.assert_not_called()

def test_offline_pending_tasks_and_current_timer_are_included_without_network(tmp_state_dir):
    m=module()
    app.queue_action('start',start_time=NOW.isoformat(),description='Offline task',project_id=7,workspace_id='ws_123')
    with patch.object(app,'_cached_projects',return_value=PROJECTS),patch.object(app,'_api') as api:
        groups=m.tree(app,now=NOW)
    assert any(t['description']=='Offline task' for p in groups for t in p['tasks'])
    api.assert_not_called()

def test_task_selection_starts_a_new_interval_and_passes_exact_timer_and_context(tmp_state_dir):
    m=module()
    before=dict(app.state,tracking=True,entry_id=101,start_time=NOW.isoformat(),description='Previous')
    app.state.update(before)
    context=toggl_projects.context(app)
    with patch('toggl_control.dispatch',return_value={}) as dispatch:
        m.select_task(app,7,'Next',context,before)
    dispatch.assert_called_once_with(app,'switch',{'changes':{'project_id':7,'description':'Next'},'expected':before,'context':context})

def test_stale_task_tree_cannot_switch_a_new_account(tmp_state_dir):
    m=module()
    context=toggl_projects.context(app)
    app.api_token='replacement'
    with patch('toggl_control.dispatch') as dispatch:
        with pytest.raises(ValueError,match='changed'):
            m.select_task(app,7,'Next',context,dict(app.state))
    dispatch.assert_not_called()


def test_frequent_alternatives_are_offered_even_when_many_projects_were_used_more_recently():
    m=module()
    rows=[dict(id=i,count=1,last_used=20-i) for i in range(6)]+[dict(id=99,count=50,last_used=1)]
    recent,frequent=m.quick_choices(rows)
    assert [r['id'] for r in recent]==[0,1,2,3]
    assert frequent[0]['id']==99
    assert len({r['id'] for r in recent+frequent})==len(recent+frequent)


def test_task_context_is_checked_again_inside_mutation_lock(tmp_state_dir):
    m=module()
    import toggl_control
    context=toggl_projects.context(app)
    before=dict(app.state)
    app.api_token='changed'
    with patch.object(app,'toggle_tracking') as toggle:
        with pytest.raises(toggl_control.ControlError,match='changed'):
            toggl_control.dispatch(app,'switch',dict(changes={'description':'Next'},expected=before,context=context))
    toggle.assert_not_called()


def test_local_task_survives_stop_and_next_history_refresh(tmp_state_dir):
    m=module()
    current=dict(app.state,tracking=True,start_time=NOW.isoformat(),entry_id=8,description='Just completed',project_id=7)
    m.remember(app,current)
    with patch.object(app,'_api',return_value=[]):
        m.refresh(app,force=True)
    assert any(e['description']=='Just completed' for e in m.cached_entries(app))


def test_current_pending_and_remembered_offline_interval_count_once(tmp_state_dir):
    m=module()
    app.state.update(tracking=True,start_time=NOW.isoformat(),entry_id=None,description='Offline',project_id=7)
    app.queue_action('start',start_time=NOW.isoformat(),description='Offline',project_id=7,workspace_id='ws_123')
    m.remember(app,dict(app.state))
    with patch.object(app,'_cached_projects',return_value=PROJECTS):
        group=next(p for p in m.tree(app,now=NOW) if p['id']==7)
    assert group['count']==1


def test_remembered_correction_overrides_older_cloud_description(tmp_state_dir):
    m=module()
    scope=toggl_projects.context(app)
    app._atomic_write_text(tmp_state_dir/'task-history.json',json.dumps(dict(context=scope,entries=[entry(12,description='Old')],fetched_at=0)))
    m.remember(app,dict(app.state,tracking=True,entry_id=12,start_time=NOW.isoformat(),description='Corrected',project_id=7))
    with patch.object(app,'_cached_projects',return_value=PROJECTS):
        group=next(p for p in m.tree(app,now=NOW) if p['id']==7)
    assert [t['description'] for t in group['tasks']]==['Corrected']


def test_task_switch_closes_offline_interval_and_creates_distinct_pending_start(tmp_state_dir):
    m=module()
    app.api_token=None
    app.state.update(tracking=True,start_time=(app.datetime.now(app.timezone.utc)-app.timedelta(minutes=5)).isoformat(),entry_id=None,description='Old task',project_id=7)
    app.queue_action('start',start_time=app.state['start_time'],description='Old task',project_id=7,workspace_id='ws_123')
    original=app.state['start_time']
    with patch.object(app,'_cached_projects',return_value=PROJECTS),patch.object(app,'update_tray_icon'),patch.object(app,'_play_sound'),patch.object(app,'_notify'):
        m.select_task(app,7,'Next task',toggl_projects.context(app),dict(app.state))
    operations=app._pending_operations(app._load_pending())
    assert app.state['tracking'] is True
    assert app.state['description']=='Next task'
    assert len(operations)==2
    assert operations[0]['description']=='Old task'
    assert operations[0]['start_time']==original
    assert operations[0]['stop_time'] is not None
    assert operations[1]['description']=='Next task'
    assert operations[1].get('stop_time') is None
