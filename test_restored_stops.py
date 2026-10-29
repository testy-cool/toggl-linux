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


@pytest.mark.parametrize('current', [None, {'id': 999, 'start': '2026-10-29T00:00:00+00:00'}])
def test_startup_stop_does_not_overwrite_an_entry_already_ended_elsewhere(current):
    restored_stop()
    app.api_token = 'test-token'
    with patch.object(app, 'fetch_current_entry', return_value=current), \
         patch.object(app, 'update_entry') as update, patch.object(app, 'api_post') as create:
        assert app._init_workspace() is True  # Pending stop deliberately defers normal adoption.
        assert app.sync_pending() == 0
    update.assert_not_called()
    create.assert_not_called()
    assert any(event['event'] == 'restored_stop_already_ended' for event in app._read_ledger_events())


def test_still_running_restored_entry_keeps_the_users_original_stop_time():
    queued = restored_stop()
    app.api_token = 'test-token'
    with patch.object(app, 'fetch_current_entry', return_value={'id':123, 'start':queued['start_time']}), \
         patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[2]['stop'] == queued['stop_time']


def test_restored_stop_stays_pending_if_cloud_validation_is_unavailable():
    restored_stop()
    before = app._load_pending()
    app.api_token = 'test-token'
    with patch.object(app, 'fetch_current_entry', side_effect=OSError('Offline')), \
         patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 1
    assert app._load_pending() == before
    update.assert_not_called()


def test_guard_survives_restart_and_pending_metadata_edits():
    queued = restored_stop()
    selected = app._pending_as_entries()[0]
    assert app._update_pending_entry(selected, {'description':'Edited offline'})
    app.load_state()
    app.api_token = 'test-token'
    with patch.object(app, 'fetch_current_entry', return_value={'id':123, 'start':queued['start_time']}), \
         patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[2]['description'] == 'Edited offline'


def test_deleted_restored_entry_is_not_recreated_after_the_cloud_check():
    queued = restored_stop()
    app.api_token = 'test-token'
    response = app.requests.Response()
    response.status_code = 404
    error = app.requests.exceptions.HTTPError(response=response)
    with patch.object(app, 'fetch_current_entry', return_value={'id':123, 'start':queued['start_time']}), \
         patch.object(app, 'update_entry', side_effect=error), patch.object(app, 'api_post') as create:
        assert app.sync_pending() == 0
    create.assert_not_called()
