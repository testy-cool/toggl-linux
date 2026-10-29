"""Shared edit preparation and confirmed discard intent cover every entry surface."""
from unittest.mock import patch
import pytest
import toggl_tray as app
import toggl_control as control
from test_restored_stops import restored_stop, remote_entry
from test_stop_review import park_stop


def test_full_review_preserves_corrections_made_before_snapshots_existed():
    queued = restored_stop()
    queue = app._load_pending()
    corrected_start = (app._parse_iso(queued['start_time'])+app.timedelta(hours=1)).isoformat()
    queue[0].update(start_time=corrected_start, description='Correction from old version')
    queue[0].pop('_verify_snapshot')
    app._save_pending(queue)
    app.api_token = 'tok'
    assert app.sync_pending() == 1
    entry = app._pending_as_entries()[0]
    assert app._update_pending_entry(entry, {'description':'New correction'})
    remote = remote_entry(queued, stop=(app._parse_iso(queued['stop_time'])-app.timedelta(hours=1)).isoformat())
    with patch.object(app, 'api_get', return_value=remote), patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[2]['start'] == corrected_start
    assert update.call_args.args[2]['description'] == 'New correction'


@pytest.mark.parametrize('legacy', [False, True])
def test_control_edits_prepare_reviewed_entries_for_replay(legacy):
    queued = park_stop()
    if legacy:
        queue = app._load_pending(); queue[0].pop('_verify_snapshot'); app._save_pending(queue)
    selected = app._pending_as_entries()[0]
    control.dispatch(app, 'edit-entry', {'entry':selected, 'changes':{'description':'From control'}})
    assert not app._pending_as_entries()[0].get('_review_error')
    with patch.object(app, 'api_get', return_value=remote_entry(queued)), patch.object(app, 'update_entry') as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[2]['description'] == 'From control'


@pytest.mark.parametrize('unpark', [False, True])
def test_changed_review_state_rejects_the_previously_confirmed_delete_intent(unpark):
    if unpark:
        park_stop()
        selected = app._pending_as_entries()[0]
        assert app._update_pending_entry(selected, {})
    else:
        restored_stop()
        selected = app._pending_as_entries()[0]
        app.api_token = 'tok'
        response = app.requests.Response(); response.status_code = 501
        with patch.object(app, 'api_get', side_effect=app.requests.exceptions.HTTPError(response=response)):
            assert app.sync_pending() == 1
    before = app._load_pending()
    assert not app._delete_pending_entry(selected)
    assert app._load_pending() == before
