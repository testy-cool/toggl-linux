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
    with patch.object(app,'get_api_base',return_value='https://other.example/api/v9',create=True):
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
    assert api.call_args.kwargs['base_url']==toggl_projects._api_base(app)
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


def test_last_two_projects_and_tasks_are_clickable_in_first_tray_menu_without_submenus(tmp_state_dir):
    from types import SimpleNamespace
    m=module()
    class Menu(list):
        SEPARATOR=object()
        def __init__(self,*items): super().__init__(items)
    def item(text,action,**options):return SimpleNamespace(text=text,action=action,**options)
    rows=m.group_projects(PROJECTS,[entry(1,7,'Write FAQ'),entry(2,8,'Build selector',days=1)],'ws_123',now=NOW)
    with patch.object(m,'tree',return_value=rows),patch.object(app,'_cached_projects',return_value=PROJECTS), \
         patch.object(app.pystray,'Menu',Menu),patch.object(app.pystray,'MenuItem',side_effect=item):
        menu=app.build_menu()
    direct=[i for i in menu if isinstance(i,SimpleNamespace)]
    labels=[i.text for i in direct]
    assert 'DPF' in labels and 'Tools' in labels
    tasks=[i for i in direct if '─ ' in i.text]
    assert any('Write FAQ' in i.text for i in tasks)
    assert any('Build selector' in i.text for i in tasks)
    assert all(callable(i.action) and not isinstance(i.action,Menu) for i in tasks)
    assert callable(next(i for i in direct if i.text=='DPF').action)
    assert 'Unused' not in labels


def test_tray_tree_marks_only_the_running_project_and_task_with_a_dot(tmp_state_dir):
    from types import SimpleNamespace
    m=module()
    class Menu(list):
        SEPARATOR=object()
        def __init__(self,*items): super().__init__(items)
    def item(text,action,**options):return SimpleNamespace(text=text,action=action,**options)
    rows=m.group_projects(PROJECTS,[entry(1,7,'Write FAQ'),entry(2,7,'Review',days=1),entry(3,8,'Build selector',days=2)],'ws_123',now=NOW)
    app.state.update(tracking=True,start_time=NOW.isoformat(),project_id=7,description='Write FAQ',workspace_id='ws_123')
    with patch.object(m,'tree',return_value=rows),patch.object(app,'_cached_projects',return_value=PROJECTS), \
         patch.object(app.pystray,'Menu',Menu),patch.object(app.pystray,'MenuItem',side_effect=item):
        labels=[i.text for i in app.build_menu() if isinstance(i,SimpleNamespace)]
    assert labels[:5]==['● DPF','  ├─ ● Write FAQ','  └─ Review','Tools','  └─ Build selector']
    assert sum('●' in l for l in labels)==2


def test_tray_icon_colour_follows_project_or_task(tmp_state_dir):
    projects=[dict(id=7,workspace_id='ws_123',name='DPF',color='#e28c1c',active=True)]
    with patch.object(app,'_cached_projects',return_value=projects):
        app.state.update(tracking=True,project_id=7,workspace_id='ws_123',description='x')
        assert app._tracking_color()=='#e28c1c'
        app.state.update(project_id=None,description='Stay Safe')
        first=app._tracking_color()
        assert first in app.TASK_COLORS and first==app._tracking_color()
        app.state.update(description='')
        assert app._tracking_color() is None


def test_choosing_the_running_task_keeps_its_timer(tmp_state_dir):
    m=module()
    running=dict(app.state,tracking=True,start_time=NOW.isoformat(),project_id=None,description='Stay Safe')
    with patch('toggl_control.dispatch') as dispatch:
        assert m.select_task(app,None,'Stay Safe',toggl_projects.context(app),running) is None
        dispatch.assert_not_called()
        m.select_task(app,None,'Other task',toggl_projects.context(app),running)
        dispatch.assert_called_once()


def test_pending_correction_replaces_remembered_description(tmp_state_dir):
    m=module()
    start=NOW.isoformat()
    app.queue_action('start',start_time=start,description='Old',project_id=7,workspace_id='ws_123')
    app.queue_action('stop',start_time=start,stop_time=(NOW+timedelta(minutes=1)).isoformat(),description='Old',project_id=7,workspace_id='ws_123')
    m.remember(app,dict(app.state,tracking=True,start_time=start,description='Old',project_id=7))
    selected=app._pending_as_entries()[0]
    import toggl_control
    toggl_control.dispatch(app,'edit-entry',dict(entry=selected,changes={'description':'Corrected'}))
    with patch.object(app,'_cached_projects',return_value=PROJECTS):
        group=next(p for p in m.tree(app,now=NOW) if p['id']==7)
    assert [t['description'] for t in group['tasks']]==['Corrected']


def test_equivalent_cloud_timestamp_does_not_double_count_uploaded_local_interval(tmp_state_dir):
    m=module()
    m.remember(app,dict(app.state,tracking=True,start_time=NOW.isoformat(),description='Task',project_id=7))
    cloud=[dict(entry(45,description='Task'),start=NOW.isoformat().replace('+00:00','Z'))]
    with patch.object(app,'_api',return_value=cloud):
        assert m.refresh(app,force=True)
    with patch.object(app,'_cached_projects',return_value=PROJECTS):
        group=next(p for p in m.tree(app,now=NOW) if p['id']==7)
    assert group['count']==1


def test_pending_suggestions_do_not_cross_accounts_with_identical_project_ids(tmp_state_dir):
    m=module()
    app.queue_action('start',start_time=NOW.isoformat(),description='Private old account task',project_id=7,workspace_id='ws_123')
    app.api_token='replacement-account'
    with patch.object(app,'_cached_projects',return_value=PROJECTS):
        assert all(not p['tasks'] for p in m.tree(app,now=NOW))


def test_stale_cloud_reply_does_not_undo_a_switch_made_during_the_request(tmp_state_dir):
    app.state.update(tracking=True,entry_id=101,start_time=NOW.isoformat(),description='Old',project_id=None)
    def slow_reply():
        app.state.update(entry_id=202,description='Next')  # the user switched while the request was in flight
        return dict(id=101,start=NOW.isoformat(),description='Old',workspace_id='ws_123')
    with patch.object(app,'fetch_current_entry',side_effect=slow_reply),patch.object(app,'_load_pending',return_value=[]):
        app._sync_cloud_state()
    assert (app.state['entry_id'],app.state['description'])==(202,'Next')


def test_running_task_from_previous_account_is_not_suggested_or_remembered(tmp_state_dir):
    m=module()
    app.queue_action('start',start_time=NOW.isoformat(),description='Account A private task',project_id=7,workspace_id='ws_123')
    app.state.update(tracking=True,start_time=NOW.isoformat(),description='Account A private task',project_id=7)
    app.api_token='account-b'
    with patch.object(app,'_cached_projects',return_value=PROJECTS):
        assert all(not p['tasks'] for p in m.tree(app,now=NOW))
    m.remember(app,dict(app.state))
    assert m.cached_entries(app)==[]


def test_corrected_interval_stays_corrected_after_its_upload(tmp_state_dir):
    m=module()
    start=NOW.isoformat()
    app.queue_action('start',start_time=start,description='Old',project_id=7,workspace_id='ws_123')
    app.queue_action('stop',start_time=start,stop_time=(NOW+timedelta(minutes=1)).isoformat(),description='Old',project_id=7,workspace_id='ws_123')
    m.remember(app,dict(app.state,tracking=True,start_time=start,description='Old',project_id=7))
    import toggl_control
    toggl_control.dispatch(app,'edit-entry',dict(entry=app._pending_as_entries()[0],changes={'description':'Corrected'}))
    app._save_pending([])  # the corrected interval uploaded and left the queue
    with patch.object(app,'_cached_projects',return_value=PROJECTS):
        group=next(p for p in m.tree(app,now=NOW) if p['id']==7)
    assert [t['description'] for t in group['tasks']]==['Corrected']


def test_running_task_with_no_description_gets_the_dot(tmp_state_dir):
    from types import SimpleNamespace
    m=module()
    class Menu(list):
        SEPARATOR=object()
        def __init__(self,*items): super().__init__(items)
    def item(text,action,**options):return SimpleNamespace(text=text,action=action,**options)
    rows=m.group_projects(PROJECTS,[entry(1,7,''),entry(2,7,'No description',days=1)],'ws_123',now=NOW)
    app.state.update(tracking=True,start_time=NOW.isoformat(),project_id=7,description='',workspace_id='ws_123')
    with patch.object(m,'tree',return_value=rows),patch.object(app,'_cached_projects',return_value=PROJECTS), \
         patch.object(app.pystray,'Menu',Menu),patch.object(app.pystray,'MenuItem',side_effect=item):
        labels=[i.text for i in app.build_menu() if isinstance(i,SimpleNamespace)]
    assert labels[:3]==['● DPF','  ├─ ● No description','  └─ No description']


def test_cloud_timer_from_previous_account_is_not_suggested_after_token_change(tmp_state_dir):
    m=module()
    app.state.update(tracking=True,entry_id=101,start_time=NOW.isoformat(),description='Private A',project_id=None)
    app.save_state()
    app.api_token='account-b'
    with patch.object(app,'_cached_projects',return_value=PROJECTS):
        assert all(not p['tasks'] for p in m.tree(app,now=NOW))
    m.remember(app,dict(app.state))
    assert m.cached_entries(app)==[]


def test_editing_one_interval_leaves_others_with_the_same_start_alone(tmp_state_dir):
    m=module()
    first=entry(101,7,'First'); other=dict(entry(102,8,'Unrelated'),start=first['start'])
    app._atomic_write_text(tmp_state_dir/'task-history.json',json.dumps(dict(context=toggl_projects.context(app),fetched_at=0,entries=[first,other])))
    m.correct(app,first,{'description':'Corrected'})
    assert sorted(e['description'] for e in m.cached_entries(app))==['Corrected','Unrelated']


def test_history_reply_fetched_before_an_edit_does_not_undo_it(tmp_state_dir):
    m=module()
    old=entry(101,7,'Old')
    app._atomic_write_text(tmp_state_dir/'task-history.json',json.dumps(dict(context=toggl_projects.context(app),fetched_at=0,entries=[old])))
    def slow_history(*_a,**_k):
        m.correct(app,old,{'description':'Corrected'})  # the edit lands while the history request is in flight
        return [old]
    with patch.object(app,'_api',side_effect=slow_history):
        assert m.refresh(app,force=True) is False
    assert [e['description'] for e in m.cached_entries(app)]==['Corrected']


def test_two_remote_intervals_with_identical_text_are_corrected_separately(tmp_state_dir):
    m=module()
    first=entry(101,7,'Same task'); second=dict(entry(102,7,'Same task'),start=first['start'])
    app._atomic_write_text(tmp_state_dir/'task-history.json',json.dumps(dict(context=toggl_projects.context(app),fetched_at=0,entries=[first,second])))
    m.correct(app,first,{'description':'Corrected'})
    assert sorted(e['description'] for e in m.cached_entries(app))==['Corrected','Same task']


def test_edit_during_the_first_history_fetch_is_not_undone(tmp_state_dir):
    m=module()
    old=entry(101,7,'Old')
    def slow_history(*_a,**_k):
        m.correct(app,old,{'description':'Corrected'})
        return [old]
    with patch.object(app,'_api',side_effect=slow_history):
        assert m.refresh(app,force=True) is False


def test_offline_edit_of_a_synced_interval_updates_its_cached_copy(tmp_state_dir):
    m=module()
    synced=entry(101,7,'Old')
    app._atomic_write_text(tmp_state_dir/'task-history.json',json.dumps(dict(context=toggl_projects.context(app),fetched_at=0,entries=[synced])))
    m.correct(app,dict(synced,id='pending:0',_remote_entry_id=101),{'description':'Corrected'})
    assert [e['description'] for e in m.cached_entries(app)]==['Corrected']


def _edit_from_entries_window(selected, result):
    """Run the Today's entries window edit handler inline, with the dialog answered."""
    class Inline:
        def __init__(self, target, daemon=None): self.target = target
        def start(self): self.target()
    with patch.object(app, '_gtk_edit_entry_dialog', return_value=result), \
         patch.object(app, '_gtk_confirm', return_value=True), patch.object(app, '_refresh_entries'), \
         patch.object(app, 'update_entry'), patch.object(app, 'delete_entry'), \
         patch.object(app.threading, 'Thread', Inline):
        app._on_edit_entry(selected, None, [], None, {'date': NOW.date()})


def _cached_tasks(m):
    with patch.object(app, '_cached_projects', return_value=PROJECTS):
        return {(p['id'], t['description']) for p in m.tree(app, now=NOW) for t in p['tasks']}


def test_rename_in_entries_window_updates_tray_tasks(tmp_state_dir):
    m = module()
    old = entry(101, 7, 'Old name')
    app._atomic_write_text(tmp_state_dir/'task-history.json', json.dumps(dict(context=toggl_projects.context(app), fetched_at=0, entries=[old])))
    _edit_from_entries_window(dict(old), {'description': 'New name'})
    assert _cached_tasks(m) == {(7, 'New name')}


def test_offline_rename_in_entries_window_stays_after_upload(tmp_state_dir):
    m = module()
    start = NOW.isoformat()
    app.queue_action('start', start_time=start, description='Old', project_id=7, workspace_id='ws_123')
    app.queue_action('stop', start_time=start, stop_time=(NOW+timedelta(minutes=1)).isoformat(), description='Old', project_id=7, workspace_id='ws_123')
    m.remember(app, dict(app.state, tracking=True, start_time=start, description='Old', project_id=7))
    _edit_from_entries_window(app._pending_as_entries()[0], {'description': 'Corrected'})
    app._save_pending([])  # the corrected interval uploaded and left the queue
    assert _cached_tasks(m) == {(7, 'Corrected')}


def test_delete_in_entries_window_removes_the_task_from_the_tray(tmp_state_dir):
    m = module()
    gone, kept = entry(101, 7, 'Deleted task'), entry(102, 7, 'Kept task', days=1)
    app._atomic_write_text(tmp_state_dir/'task-history.json', json.dumps(dict(context=toggl_projects.context(app), fetched_at=0, entries=[gone, kept])))
    _edit_from_entries_window(dict(gone), 'delete')
    assert _cached_tasks(m) == {(7, 'Kept task')}
