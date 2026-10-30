"""A frozen draft follows only the exact pending operation confirmed by sync."""
from unittest.mock import patch

import pytest
import toggl_control as control
import toggl_projects
import toggl_tray as app


def offline_timer():
    with patch.object(app,'start_entry',side_effect=app.requests.ConnectionError('offline')):
        control.dispatch(app,'start',{'changes':{'description':'Original pending work'}})
    snapshot=control.dispatch(app,'work-snapshot',{})
    return snapshot, app._pending_as_entries()[0]


def confirm_open():
    cloud=dict(id=801,workspace_id=app.state['workspace_id'],project_id=None,task_id=None,
               description=app.state['description'],start=app.state['start_time'],stop=None,duration=-1)
    with patch.object(app,'fetch_current_entry',return_value=None), \
         patch.object(app,'start_entry',return_value=cloud):
        assert app.sync_pending() == 0
    return cloud


def confirm_completed():
    pending=app._pending_as_entries()[0]
    cloud={k:pending.get(k) for k in ['workspace_id','project_id','task_id','description','start','stop','duration']}
    cloud['id']=802
    with patch.object(app,'api_post',return_value=cloud):
        assert app.sync_pending() == 0
    return cloud


def test_poll_resolves_the_original_offline_timer_after_it_receives_a_cloud_id():
    original,selected=offline_timer()
    cloud=confirm_open()
    result=control.dispatch(app,'work-snapshot',{'draft_entry':selected,'draft_context':original['project_context']})
    assert result['draft_resolution']['source'] == selected
    assert result['draft_resolution']['entry']['id'] == cloud['id']
    assert result['current_pending'] is None


def test_stopped_pending_draft_can_save_after_the_queue_has_already_synced():
    original,_=offline_timer()
    control.dispatch(app,'stop',{})
    selected=app._pending_as_entries()[0]
    cloud=confirm_completed()
    assert not app._load_pending()
    with patch.object(app,'update_entry') as update:
        control.dispatch(app,'edit-entry',{'entry':selected,'context':original['project_context'],
                                           'changes':{'description':'Draft survived sync'}})
    assert update.call_args.args[:2] == (cloud['workspace_id'],cloud['id'])
    assert update.call_args.args[2] == {'description':'Draft survived sync'}


def test_a_sync_handoff_never_approves_a_draft_changed_elsewhere_before_sync():
    original,_=offline_timer()
    control.dispatch(app,'stop',{})
    selected=app._pending_as_entries()[0]
    assert app._update_pending_entry(selected,{'description':'Changed in another editor'})
    confirm_completed()
    with patch.object(app,'update_entry') as update:
        with pytest.raises(control.ControlError,match='changed'):
            control.dispatch(app,'edit-entry',{'entry':selected,'context':original['project_context'],
                                               'changes':{'description':'Stale draft'}})
    update.assert_not_called()


def test_a_sync_handoff_is_scoped_to_its_original_account():
    original,selected=offline_timer()
    confirm_open()
    app.api_token='different-account'
    result=control.dispatch(app,'work-snapshot',{'draft_entry':selected,'draft_context':original['project_context']})
    assert result.get('draft_resolution') is None
    assert result['project_context'] != original['project_context']


def test_sync_bookkeeping_failure_cannot_retry_an_already_created_cloud_entry():
    offline_timer()
    control.dispatch(app,'stop',{})
    with patch.object(control,'record_pending_resolution',side_effect=RuntimeError('bookkeeping')):
        confirm_completed()
    assert app._load_pending() == []


def test_running_draft_can_save_immediately_after_sync_before_a_status_poll():
    original,selected=offline_timer()
    cloud=confirm_open()
    with patch.object(app,'update_entry') as update:
        control.dispatch(app,'edit-current',{'expected':original['state'],'context':original['project_context'],
                                             'draft_entry':selected,'changes':{'description':'Saved immediately'}})
    assert update.call_args.args[1] == cloud['id']
    assert app.state['description'] == 'Saved immediately'


def test_running_sync_handoff_cannot_edit_a_replacement_timer():
    original,selected=offline_timer()
    confirm_open()
    app.state['entry_id']=999
    with patch.object(app,'update_entry') as update:
        with pytest.raises(control.ControlError,match='changed'):
            control.dispatch(app,'edit-current',{'expected':original['state'],'context':original['project_context'],
                                                 'draft_entry':selected,'changes':{'description':'Do not redirect'}})
    update.assert_not_called()


def test_stop_reply_can_resolve_an_open_draft_synced_before_the_reply_was_built():
    original,selected=offline_timer()
    with patch.object(control,'snapshot',wraps=control.snapshot) as snapshot:
        # Sync at the boundary between sealing the interval and returning its snapshot.
        def after_stop(app_obj):
            if not app.state['tracking'] and app._load_pending(): confirm_completed()
            return original_snapshot(app_obj)
        original_snapshot=snapshot._mock_wraps
        snapshot.side_effect=after_stop
        result=control.dispatch(app,'stop',{'draft_entry':selected,'draft_context':original['project_context']})
    assert result['draft_resolution']['source'] == selected
    assert result['draft_resolution']['entry']['stop']
    assert result['draft_resolution']['entry']['id'] == 802


def test_confirmed_yesterday_entry_stays_editable_after_it_leaves_the_queue():
    from datetime import datetime, timedelta, timezone
    original,_=offline_timer()
    yesterday=(datetime.now(timezone.utc).astimezone().replace(hour=12,minute=0,second=0,microsecond=0)-timedelta(days=1)).isoformat()
    control.dispatch(app,'edit-current',{'changes':{'start':yesterday}})
    control.dispatch(app,'stop',{})
    selected=app._pending_as_entries()[0]
    confirm_completed()
    assert any(entry['id']==802 for entry in control.snapshot(app)['entries'])
    with patch.object(app,'update_entry') as update:
        control.dispatch(app,'edit-entry',{'entry':selected,'context':original['project_context'],
                                           'changes':{'description':'Yesterday draft preserved'}})
    assert update.call_args.args[1] == 802


def test_confirmed_running_unicode_draft_fits_the_real_socket_frame():
    app.api_token='tok'
    description='🧪'*10000
    with patch.object(app,'start_entry',side_effect=app.requests.ConnectionError('offline')):
        control.dispatch(app,'start',{'changes':{'description':description}})
    original=control.snapshot(app)
    selected=original['current_pending']
    confirm_open()
    server=control.start_server(app)
    try:
        with patch.object(app,'update_entry') as update:
            result=control.request(app.STATE_DIR,'edit-current',{'expected':original['state'],
                'context':original['project_context'],'draft_entry':selected,'changes':{'description':'🕒'*10000}})
        assert result['state']['description'] == '🕒'*10000
        assert update.call_args.args[1] == 801
    finally:
        server.shutdown(); server.server_close()


def test_an_already_ended_restored_stop_retains_the_confirmed_cloud_interval():
    from test_restored_stops import restored_stop, remote_entry
    queued=restored_stop()
    app.api_token='tok'
    queue=app._load_pending()
    queue[0]['_task_context']=toggl_projects.context(app)
    app._save_pending(queue)
    selected=app._pending_as_entries()[0]
    cloud=remote_entry(queued,stop=queued['stop_time'])
    with patch.object(app,'api_get',return_value=cloud), patch.object(app,'update_entry'):
        assert app.sync_pending() == 0
    result=control.dispatch(app,'work-snapshot',{'draft_entry':selected,'draft_context':toggl_projects.context(app)})
    assert result['draft_resolution']['entry']['stop'] == cloud['stop']


@pytest.mark.parametrize('variant',['empty','duplicate','foreign'])
def test_invalid_pending_identity_cannot_mutate_a_confirmed_timer(variant):
    original,selected=offline_timer()
    confirm_open()
    selected=dict(selected)
    selected['_pending_ids']={'empty':[], 'duplicate':selected['_pending_ids']*2, 'foreign':['other']}[variant]
    with patch.object(app,'update_entry') as update:
        with pytest.raises(control.ControlError,match='changed'):
            control.dispatch(app,'edit-current',{'expected':original['state'], 'context':original['project_context'],
                'draft_entry':selected,'changes':{'description':'Do not apply'}})
    update.assert_not_called()


def test_refresh_cannot_turn_a_completed_overnight_handoff_back_into_a_running_entry():
    from datetime import datetime,timedelta,timezone
    offline_timer()
    yesterday=(datetime.now(timezone.utc).astimezone().replace(hour=12,minute=0,second=0,microsecond=0)-timedelta(days=1)).isoformat()
    control.dispatch(app,'edit-current',{'changes':{'start':yesterday}})
    confirm_open()
    with patch.object(app,'stop_entry',return_value={}):
        stopped=control.dispatch(app,'stop',{})['stopped_entry']
    with patch.object(app,'_refresh_projects'), patch.object(app,'_refresh_tasks'), \
         patch.object(app,'_request_budget_remaining',return_value=100), patch.object(app,'fetch_today_entries',return_value=[]):
        control.dispatch(app,'refresh',{})
    changed=(datetime.fromisoformat(yesterday)+timedelta(minutes=10)).isoformat()
    with patch.object(app,'update_entry') as update:
        control.dispatch(app,'edit-entry',{'entry':stopped,'changes':{'start':changed}})
    assert update.call_args.args[2]['duration'] >= 0, 'A completed edit must never send a running duration'
    assert not app.state['tracking']
    new_stop=(datetime.fromisoformat(stopped['stop'])-timedelta(minutes=1)).isoformat()
    with patch.object(app,'update_entry') as update:
        control.dispatch(app,'edit-entry',{'entry':stopped,'changes':{'stop':new_stop}})
    assert update.call_args.args[2]['duration'] >= 0


def test_a_new_interval_draft_can_switch_after_its_current_timer_syncs():
    original,selected=offline_timer()
    confirm_open()
    from datetime import datetime,timezone
    new=dict(id=803,start=datetime.now(timezone.utc).isoformat(),description='Next work',workspace_id='ws_123')
    with patch.object(app,'stop_entry',return_value={}) as stop,patch.object(app,'start_entry',return_value=new) as start:
        control.dispatch(app,'switch',{'expected':original['state'],'context':original['project_context'],
            'draft_entry':selected,'changes':{'description':'Next work','project_id':None,'task_id':None}})
    assert stop.call_args.args[1] == 801
    assert start.call_count == 1 and app.state['entry_id'] == 803


def test_a_completed_form_is_never_applied_to_a_stale_running_handoff():
    offline_timer()
    cloud=confirm_open()
    app._control_entries=[]
    from datetime import datetime,timezone
    selected=dict(cloud,stop=datetime.now(timezone.utc).isoformat(),duration=0)
    with patch.object(app,'update_entry') as update:
        with pytest.raises(control.ControlError,match='changed'):
            control.dispatch(app,'edit-entry',{'entry':selected,'changes':{'start':cloud['start']}})
    update.assert_not_called()


def test_stop_racing_with_confirmation_updates_the_handoff_to_its_completed_state():
    import threading,time
    offline_timer()
    remote=dict(id=801,workspace_id='ws_123',project_id=None,task_id=None,description=app.state['description'],
                start=app.state['start_time'],stop=None,duration=-1)
    entering,release=threading.Event(),threading.Event()
    original=control.record_pending_resolution
    errors=[]
    def delayed(*args):
        entering.set()
        assert release.wait(3)
        return original(*args)
    def sync():
        try: app.sync_pending()
        except BaseException as error: errors.append(error)
    def stop():
        try: control.dispatch(app,'stop',{})
        except BaseException as error: errors.append(error)
    with patch.object(control,'record_pending_resolution',side_effect=delayed), \
         patch.object(app,'fetch_current_entry',return_value=None),patch.object(app,'start_entry',return_value=remote), \
         patch.object(app,'stop_entry',return_value={}):
        syncing=threading.Thread(target=sync,daemon=True); syncing.start()
        assert entering.wait(1)
        stopping=threading.Thread(target=stop,daemon=True); stopping.start()
        deadline=time.monotonic()+2
        while app.state['tracking'] and time.monotonic()<deadline: time.sleep(0.01)
        release.set(); syncing.join(3); stopping.join(3)
    assert not syncing.is_alive() and not stopping.is_alive() and not errors
    assert app._pending_resolutions[-1]['entry']['stop']
    assert app._pending_resolutions[-1]['entry']['duration'] >= 0


def test_all_pending_aliases_share_the_latest_confirmed_interval_fields():
    original,opened=offline_timer()
    confirm_open()
    with patch.object(app,'stop_entry',side_effect=app.requests.ConnectionError('offline')):
        control.dispatch(app,'stop',{})
    closed=app._pending_as_entries()[0]
    remote={k:closed.get(k) for k in ['id','workspace_id','description','project_id','task_id','start','stop','duration']}
    with patch.object(app,'update_entry',return_value=remote): assert app.sync_pending() == 0
    with patch.object(app,'update_entry'):
        control.dispatch(app,'edit-entry',{'entry':closed,'changes':{'description':'Latest confirmed text'}})
    result=control.dispatch(app,'work-snapshot',{'draft_entry':opened,'draft_context':original['project_context']})
    assert result['draft_resolution']['entry']['description'] == 'Latest confirmed text'
    assert result['draft_resolution']['entry']['stop']
