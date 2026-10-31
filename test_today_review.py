"""Transport, handoff and legacy-window regressions from Claude review."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
import threading

import pytest
import toggl_control as control
import toggl_entries_window as entries_window
import toggl_today as today
import toggl_tray as app
from test_toggl_today import interval


def test_real_socket_stop_finishes_while_daily_http_is_still_waiting():
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    errors=[]
    def fetch(*args,**kwargs):
        entered.set();assert release.wait(3);return []
    def read():
        try:control.request(app.STATE_DIR,'refresh-today',{})
        except Exception as e:errors.append(e)
    def stop():
        try:control.request(app.STATE_DIR,'stop',{})
        except Exception as e:errors.append(e)
        finally:stopped.set()
    app.state.update(tracking=True,entry_id=1,start_time=interval()['start'])
    server=control.start_server(app)
    try:
        with patch.object(app,'api_get',side_effect=fetch),patch.object(app,'stop_entry',return_value={}):
            reader=threading.Thread(target=read);reader.start();assert entered.wait(1)
            stopper=threading.Thread(target=stop);stopper.start()
            finished_before_release=stopped.wait(.5)
            release.set();reader.join(4);stopper.join(4)
        assert finished_before_release,'The real control socket serialized Stop behind the HTTP read'
        assert not errors
        assert not app.state['tracking']
    finally:
        release.set();server.shutdown();server.server_close()


def test_overnight_pending_time_survives_its_successful_cloud_handoff():
    with patch.object(app,'api_get',return_value=[]):assert today.refresh(app)
    _,midnight,_=today.window(app)
    app.queue_action('start',start_time=(midnight-timedelta(hours=1)).isoformat(),description='Overnight')
    app.queue_action('stop',stop_time=(midnight+timedelta(minutes=15)).isoformat())
    queue=app._load_pending();op=app._pending_operations(queue)[0]
    source=app._pending_as_entries()[0]
    remote={key:source.get(key) for key in ('workspace_id','project_id','task_id','description','start','stop','duration')}
    remote['id']=901
    control.record_pending_resolution(app,queue,op,remote);app._save_pending([])
    assert any(e['id']==901 for e in control.snapshot(app)['entries'])
    assert control.snapshot(app)['today_fetched_at']


def test_editing_a_stopped_interval_out_of_today_removes_both_cached_copies():
    with patch.object(app,'api_get',return_value=[]):assert today.refresh(app)
    row=interval()
    control.record_stop(app,row['id'],row['start'],row['description'],row['workspace_id'],None,None,row['stop'])
    _,midnight,_=today.window(app)
    changes={'start':(midnight-timedelta(hours=2)).isoformat(),'stop':(midnight-timedelta(hours=1)).isoformat()}
    with patch.object(app,'update_entry',return_value={}):
        control.dispatch(app,'edit-entry',{'entry':dict(row),'changes':changes})
    assert not any(e['id']==row['id'] for e in control.snapshot(app)['entries'])


@pytest.mark.parametrize('operation',['edit','delete'])
def test_gtk_interval_changes_reach_the_work_panel_cache(operation):
    row=interval()
    with patch.object(app,'api_get',return_value=[row]):assert today.refresh(app)
    changes={'description':'Corrected from GTK'} if operation=='edit' else 'delete'
    with patch.object(entries_window,'_gtk_edit_entry_dialog',return_value=changes), \
         patch.object(app,'_gtk_confirm',return_value=True), \
         patch.object(app,'update_entry',return_value={}),patch.object(app,'delete_entry',return_value={}), \
         patch.object(entries_window,'_refresh_entries'), \
         patch.object(app.threading,'Thread',side_effect=lambda **kw:SimpleNamespace(start=kw['target'])):
        entries_window._on_edit_entry(dict(row),None,[],None,{'date':app.datetime.now().date()})
    rows=control.snapshot(app)['entries']
    if operation=='delete':assert not rows
    else:assert rows[0]['description']=='Corrected from GTK'


def test_stopping_before_any_read_does_not_leak_entries_to_a_new_provider():
    row=interval()
    control.record_stop(app,row['id'],row['start'],row['description'],row['workspace_id'],None,None,row['stop'])
    app.switch_api_base('https://another-fixture.example/api/v9')
    assert not control.snapshot(app)['entries']


def test_each_local_stop_tags_its_actual_account_instead_of_the_previous_read():
    row=interval()
    with patch.object(app,'api_get',return_value=[row]):assert today.refresh(app)
    first=app.api_token;app.api_token='second-fixture-account'
    control.record_stop(app,2,row['start'],'Second account',row['workspace_id'],None,None,row['stop'])
    app.api_token=first
    assert not any(e['id']==2 for e in control.snapshot(app)['entries'])


def test_a_normal_offline_stop_with_no_task_has_known_assignment_fields():
    row=interval()
    with patch.object(app,'api_get',return_value=[]):assert today.refresh(app)
    app.state.update(tracking=True,entry_id=1,start_time=row['start'],task_id=None,project_id=None)
    with patch.object(app,'stop_entry',side_effect=app.requests.ConnectionError('offline')):
        app.toggle_tracking()
    assert not app._pending_as_entries()[0]['_pending_missing']
    assert control.snapshot(app)['today_fetched_at']
    assert not control.snapshot(app)['today_incomplete']


def test_untagged_legacy_records_are_not_editable_or_visible():
    row=interval()
    app._control_entries=[row];app._control_entries_date=today.window(app)[0]
    app._control_entries_context=None
    assert not control.snapshot(app)['entries']
    with patch.object(app,'update_entry') as update:
        with pytest.raises(control.ControlError):
            control.dispatch(app,'edit-entry',{'entry':row,'changes':{'description':'Wrong scope'}})
    update.assert_not_called()


def test_gtk_current_interval_corrections_update_the_live_timer_without_restarting():
    row=interval(stop=None);row.update(stop=None,duration=-1)
    app.state.update(tracking=True,entry_id=row['id'],start_time=row['start'])
    today.remember(app,row)
    control.record_edit(app,row,{'description':'Corrected current'})
    assert app.state['tracking'] and app.state['entry_id']==row['id']
    assert app.state['description']=='Corrected current'
    control.record_delete(app,row)
    assert not app.state['tracking'] and not control.snapshot(app)['entries']


def test_concurrent_refresh_does_not_report_a_failed_cloud_read():
    entered, release = threading.Event(), threading.Event()
    def fetch(*args, **kwargs):
        entered.set(); assert release.wait(3); return []
    with patch.object(app, 'api_get', side_effect=fetch), \
         patch.object(app, '_refresh_projects'), patch.object(app, '_refresh_tasks'):
        worker = threading.Thread(target=today.refresh, args=(app,))
        worker.start()
        try:
            assert entered.wait(1)
            result = control.dispatch(app, 'refresh', {})
            assert not result['warning']
        finally:
            release.set(); worker.join(4)
    assert not control.snapshot(app)['warning']


def test_a_successful_daily_read_clears_its_old_cloud_warning():
    app._control_warning = 'Cloud refresh unavailable; showing cached and pending entries'
    with patch.object(app, 'api_get', return_value=[]):
        control.dispatch(app, 'refresh-today', {})
    assert not control.snapshot(app)['warning']


def test_a_duration_only_closed_interval_can_change_its_start_without_becoming_running():
    class Noon(app.datetime):
        @classmethod
        def now(cls, tz=None):
            local = cls(2026, 10, 31, 12).astimezone()
            return local.astimezone(tz) if tz else local.replace(tzinfo=None)
    with patch.object(app, 'datetime', Noon):
        row = interval(); row.update(stop=None, duration=3600)
        today.remember(app, row)
        original_start = app._parse_iso(row['start'])
        with patch.object(app, 'update_entry', return_value={}) as update:
            control.dispatch(app, 'edit-entry', {'entry':row, 'changes':{'start':(original_start+timedelta(minutes=5)).isoformat()}})
    assert update.call_args.args[2]['duration'] == 3300
    assert not app.state['tracking']


def test_offline_stop_corrected_out_of_today_hides_its_original_cloud_interval():
    row = interval()
    with patch.object(app, 'api_get', return_value=[row]): assert today.refresh(app)
    app.state.update(tracking=True, entry_id=row['id'], start_time=row['start'])
    with patch.object(app, 'stop_entry', side_effect=app.requests.ConnectionError('offline')):
        app.toggle_tracking()
    selected = app._pending_as_entries()[0]
    _, midnight, _ = today.window(app)
    control.dispatch(app, 'edit-entry', {'entry':selected, 'changes':{
        'start':(midnight-timedelta(hours=2)).isoformat(), 'stop':(midnight-timedelta(hours=1)).isoformat()}})
    assert not control.snapshot(app)['entries']
    assert app._pending_as_entries()[0]['stop'] < midnight.isoformat()
