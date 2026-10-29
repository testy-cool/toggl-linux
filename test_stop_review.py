"""Unverifiable local corrections remain recoverable without blocking cloud tracking."""
from unittest.mock import patch
from types import SimpleNamespace
import pytest
import toggl_tray as app
import toggl_control as control
import toggl_cli as cli
from test_restored_stops import restored_stop, remote_entry


def park_stop():
    queued = restored_stop()
    app.api_token = 'tok'
    response = app.requests.Response()
    response.status_code = 501
    with patch.object(app, 'api_get', side_effect=app.requests.exceptions.HTTPError(response=response)):
        assert app.sync_pending() == 1
    return queued


def test_unsupported_lookup_is_visible_and_background_sync_does_not_retry_it():
    park_stop()
    assert app._pending_as_entries()[0]['_review_error']
    assert 'needs review' in control.snapshot(app)['warning']
    cloud = dict(id=456, workspace_id='ws_123', start=app.datetime.now(app.timezone.utc).isoformat())
    with patch.object(app, 'api_get') as get, patch.object(app, 'fetch_current_entry', return_value=cloud):
        app._run_sync_cycle(now=999999)
        assert app.state['entry_id'] == 456
        assert app.sync_pending() == 1
    get.assert_not_called()


@pytest.mark.parametrize('path', ['init', 'health', 'poll'])
def test_review_only_items_do_not_block_other_cloud_state_recovery(path):
    park_stop()
    app.state.update(tracking=True, entry_id=None)
    cloud = dict(id=456, workspace_id='ws_123', start=app.datetime.now(app.timezone.utc).isoformat())
    with patch.object(app, 'fetch_current_entry', return_value=cloud) as fetch:
        {'init':app._init_workspace, 'health':app._health_check, 'poll':app._sync_cloud_state}[path]()
    fetch.assert_called_once()
    assert app.state['entry_id'] == 456


def test_local_discard_does_not_queue_a_delete_of_the_remote_entry():
    park_stop()
    entry = app._pending_as_entries()[0]
    assert app._delete_pending_entry(entry)
    assert app._load_pending() == []


def test_manual_retry_can_resolve_an_item_after_endpoint_recovery():
    queued = park_stop()
    remote = remote_entry(queued, stop=(app._parse_iso(queued['stop_time'])-app.timedelta(hours=1)).isoformat())
    with patch.object(app, 'api_get', return_value=remote), patch.object(app, 'update_entry') as update:
        assert app.sync_pending(retry_review=True) == 0
    update.assert_not_called()


def test_editing_a_snapshotless_older_stop_preserves_the_explicit_corrections():
    queued = restored_stop()
    queue = app._load_pending()
    queue[0].pop('_verify_snapshot')
    app._save_pending(queue)
    app.api_token = 'tok'
    with patch.object(app, 'api_get') as get:
        assert app.sync_pending() == 1
    get.assert_not_called()
    revised_start = (app._parse_iso(queued['start_time'])+app.timedelta(hours=1)).isoformat()
    revised_stop = (app._parse_iso(queued['stop_time'])-app.timedelta(minutes=5)).isoformat()
    assert app._update_pending_entry(app._pending_as_entries()[0],
                                     {'description':'Explicit correction', 'start':revised_start, 'stop':revised_stop})
    assert not app._pending_as_entries()[0].get('_review_error')
    with patch.object(app, 'api_get', return_value=remote_entry(queued)), patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[2] == dict(description='Explicit correction', start=revised_start,
                                          stop=revised_stop, duration=82500)


def test_control_sync_retries_items_that_background_sync_pauses():
    with patch.object(app, '_run_sync_cycle') as sync:
        control.dispatch(app, 'sync', {})
    sync.assert_called_once_with(retry_review=True)


def test_cli_sync_retries_items_that_background_sync_pauses():
    with patch.object(cli, '_load_cli_runtime'), patch.object(app, 'sync_pending', return_value=0) as sync, \
         patch.object(app, '_sync_cloud_state'):
        assert cli._cli_sync(SimpleNamespace()) == 0
    sync.assert_called_once_with(retry_review=True)
