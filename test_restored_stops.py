"""Restored remote timers need reconciliation before an offline Stop is replayed."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
import toggl_tray as app


def restored_stop():
    start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    app.state.update(tracking=True, entry_id=123, start_time=start, description='Yesterday')
    app.save_state()
    app.load_state()
    app.api_token = None
    with patch.object(app, '_notify'), patch.object(app, '_play_sound'):
        app.toggle_tracking(prompt_for_description=False)
    return app._load_pending()[0]


def remote_entry(queued, **changes):
    return dict(dict(id=123, workspace_id='ws_123', start=queued['start_time'], stop=None, duration=-1), **changes)


@pytest.mark.parametrize('hours_ago', [1, 15])
def test_startup_stop_does_not_overwrite_an_entry_already_ended_elsewhere(hours_ago):
    queued = restored_stop()
    ended = (datetime.now(timezone.utc)-timedelta(hours=hours_ago)).isoformat()
    app.api_token = 'tok'
    with patch.object(app, 'api_get', return_value=remote_entry(queued, stop=ended)) as get, \
         patch.object(app, 'update_entry') as update, patch.object(app, 'api_post') as create:
        assert app._init_workspace() is True  # Pending stop deliberately defers normal adoption.
        assert app.sync_pending() == 0
    get.assert_called_once_with('/me/time_entries/123', token='tok', base_url=app.get_api_base())
    update.assert_not_called()
    create.assert_not_called()
    assert any(event['event'] == 'restored_stop_already_ended' for event in app._read_ledger_events())


def test_still_running_restored_entry_keeps_the_users_original_stop_time():
    queued = restored_stop()
    app.api_token = 'tok'
    with patch.object(app, 'api_get', return_value=remote_entry(queued)), \
         patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[2]['stop'] == queued['stop_time']


def test_restored_stop_stays_pending_if_cloud_validation_is_unavailable():
    restored_stop()
    before = app._load_pending()
    app.api_token = 'tok'
    with patch.object(app, 'api_get', side_effect=OSError('Offline')), \
         patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 1
    assert app._load_pending() == before
    update.assert_not_called()


def test_guard_survives_restart_and_pending_metadata_edits():
    queued = restored_stop()
    selected = app._pending_as_entries()[0]
    assert app._update_pending_entry(selected, {'description':'Edited offline'})
    app.load_state()
    app.api_token = 'tok'
    with patch.object(app, 'api_get', return_value=remote_entry(queued)), \
         patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[2]['description'] == 'Edited offline'


def test_deleted_restored_entry_is_not_recreated_after_the_cloud_check():
    queued = restored_stop()
    app.api_token = 'tok'
    response = app.requests.Response()
    response.status_code = 404
    error = app.requests.exceptions.HTTPError(response=response)
    with patch.object(app, 'api_get', return_value=remote_entry(queued)), \
         patch.object(app, 'update_entry', side_effect=error), patch.object(app, 'api_post') as create:
        assert app.sync_pending() == 1
    create.assert_not_called()


def test_starting_another_timer_does_not_lose_the_original_local_stop():
    queued = restored_stop()
    app.api_token = 'tok'
    later = (app._parse_iso(queued['stop_time'])+timedelta(minutes=5)).isoformat()
    with patch.object(app, 'start_entry', return_value={'id':456, 'start':later}), \
         patch.object(app, '_play_sound'):
        app.toggle_tracking(prompt_for_description=False)
    with patch.object(app, 'fetch_current_entry', return_value={'id':456}), \
         patch.object(app, 'api_get', return_value=remote_entry(queued, stop=later)), \
         patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[:2] == ('ws_123', 123)
    assert update.call_args.args[2]['stop'] == queued['stop_time']
    assert app.state['entry_id'] == 456


def test_explicit_metadata_edit_is_applied_even_when_the_remote_entry_already_ended():
    queued = restored_stop()
    assert app._update_pending_entry(app._pending_as_entries()[0], {'description':'Explicit correction'})
    earlier = (app._parse_iso(queued['stop_time'])-timedelta(hours=1)).isoformat()
    app.api_token = 'tok'
    with patch.object(app, 'api_get', return_value=remote_entry(queued, stop=earlier)), \
         patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[2] == {'description':'Explicit correction'}


def test_remote_start_and_metadata_corrections_are_preserved_during_stop_replay():
    queued = restored_stop()
    corrected = (app._parse_iso(queued['stop_time'])-timedelta(hours=1)).isoformat()
    app.api_token = 'tok'
    with patch.object(app, 'api_get', return_value=remote_entry(queued, start=corrected, description='Remote edit')), \
         patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[2] == {'stop':queued['stop_time'], 'duration':3600}


def test_account_change_keeps_a_guarded_stop_pending():
    restored_stop()
    before = app._load_pending()
    app.api_token = 'another-account-token'
    with patch.object(app, 'api_get') as get, patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 1
    assert app._load_pending() == before
    get.assert_not_called()
    update.assert_not_called()


def test_account_change_during_target_lookup_cannot_drop_or_write_the_stop():
    queued = restored_stop()
    app.api_token = 'tok'
    def reply(*_args, **_kwargs):
        app.api_token = 'another-account-token'
        return remote_entry(queued, stop=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())
    with patch.object(app, 'api_get', side_effect=reply), patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 1
    update.assert_not_called()


def test_explicit_time_corrections_on_a_pending_stop_survive_reconciliation():
    queued = restored_stop()
    revised_start = (app._parse_iso(queued['start_time'])+timedelta(hours=1)).isoformat()
    revised_stop = (app._parse_iso(queued['stop_time'])-timedelta(minutes=5)).isoformat()
    assert app._update_pending_entry(app._pending_as_entries()[0], {'start':revised_start, 'stop':revised_stop})
    app.api_token = 'tok'
    with patch.object(app, 'api_get', return_value=remote_entry(queued)), patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[2] == {'start':revised_start, 'stop':revised_stop, 'duration':82500}


@pytest.mark.parametrize('status', [404, 405, 501])
def test_missing_or_unsupported_target_lookup_keeps_the_pending_entry_editable(status):
    restored_stop()
    app.api_token = 'tok'
    response = app.requests.Response()
    response.status_code = status
    error = app.requests.exceptions.HTTPError(response=response)
    with patch.object(app, 'api_get', side_effect=error), patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 1
    update.assert_not_called()
    selected = app._pending_as_entries()[0]
    assert app._update_pending_entry(selected, {'description':'Still editable'})
    assert app._pending_as_entries()[0]['description'] == 'Still editable'
