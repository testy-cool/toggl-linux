"""Daily-cache boundaries, account isolation and concurrent timer writes."""
from datetime import timedelta
import threading
from unittest.mock import patch

import toggl_control as control
import toggl_today as today
import toggl_tray as app


def interval(identity=1, start=None, stop=None, **changes):
    _, midnight, _ = today.window(app)
    result = dict(id=identity, workspace_id=app.state['workspace_id'], project_id=None,
                  task_id=None, description='Work', duration=60,
                  start=(start or midnight + timedelta(seconds=1)).isoformat(),
                  stop=(stop or midnight + timedelta(seconds=61)).isoformat())
    result.update(changes)
    return result


def test_read_only_poll_does_not_fetch_or_claim_a_partial_cache_is_complete():
    today.remember(app, interval())
    with patch.object(app, 'api_get') as api:
        value = control.dispatch(app, 'work-snapshot', {})
    api.assert_not_called()
    assert len(value['entries']) == 1
    assert value['today_fetched_at'] is None


def test_daily_read_includes_overnight_entries_and_freezes_credentials():
    _, midnight, _ = today.window(app)
    item = interval(start=midnight-timedelta(days=7), stop=midnight+timedelta(minutes=20))
    with patch.object(app, 'api_get', return_value=[item]) as api:
        assert today.refresh(app)
    api.assert_called_once_with('/me/time_entries', params={'since': int(midnight.timestamp())},
                                token=app.api_token, base_url=app.get_api_base())
    value = control.snapshot(app)
    assert value['entries'][0]['start'] == item['start']
    assert value['today_fetched_at'] is not None


def test_pending_overnight_work_is_available_before_cloud_loading():
    app.api_token = None
    _, midnight, _ = today.window(app)
    app.queue_action('start', start_time=(midnight-timedelta(hours=1)).isoformat(), description='Overnight')
    app.queue_action('stop', stop_time=(midnight+timedelta(minutes=15)).isoformat())
    value = control.snapshot(app)
    assert len(value['entries']) == 1 and value['entries'][0]['_offline']
    assert value['today_fetched_at'] is None


def test_reopen_and_repeated_refresh_share_the_daily_read_throttle():
    with patch.object(app, 'api_get', return_value=[]) as api:
        assert today.refresh(app)
        assert today.refresh(app, force=True)
        assert today.refresh(app)
    assert api.call_count == 1
    assert today.status(app)['today_fetched_at'] is not None


def test_a_slow_daily_read_does_not_hold_start_stop_lock_and_cannot_overwrite_a_stop():
    item = interval(stop=None)
    item.update(stop=None, duration=-1)
    today.remember(app, item)
    waiting, release = threading.Event(), threading.Event()
    def fetch(*args, **kwargs):
        waiting.set()
        assert release.wait(3)
        return [item]
    with patch.object(app, 'api_get', side_effect=fetch):
        thread = threading.Thread(target=today.refresh, args=(app,))
        thread.start()
        try:
            assert waiting.wait(1)
            assert app.toggle_lock.acquire(blocking=False)
            try:
                stopped = interval()
                control.record_stop(app, 1, stopped['start'], 'Stopped while loading',
                                    stopped['workspace_id'], None, None, stopped['stop'])
            finally:
                app.toggle_lock.release()
        finally:
            release.set()
            thread.join(4)
    assert not thread.is_alive()
    saved = today.cached(app)[0]
    assert saved['stop'] is not None and saved['description'] == 'Stopped while loading'


def test_a_late_read_cannot_restore_an_interval_corrected_out_of_today():
    old = interval()
    _, midnight, _ = today.window(app)
    def fetch(*args, **kwargs):
        today.remember(app, interval(start=midnight-timedelta(hours=2), stop=midnight-timedelta(hours=1)))
        return [old]
    with patch.object(app, 'api_get', side_effect=fetch):
        assert today.refresh(app)
    assert today.cached(app) == []


def test_an_inflight_account_change_rejects_the_previous_accounts_response():
    old = interval()
    def fetch(*args, **kwargs):
        app.api_token = 'another-fixture-account'
        return [old]
    with patch.object(app, 'api_get', side_effect=fetch):
        assert not today.refresh(app)
    assert today.cached(app) is None
    assert today.status(app)['today_fetched_at'] is None


def test_daily_cache_does_not_leak_through_provider_changes():
    with patch.object(app, 'api_get', return_value=[interval()]):
        assert today.refresh(app)
    app.api_token = 'another-fixture-account'
    assert not control.snapshot(app)['entries']
    assert control.snapshot(app)['today_fetched_at'] is None


def test_failure_retains_saved_totals_without_claiming_a_fresh_fetch():
    today.remember(app, interval())
    with patch.object(app, 'api_get', side_effect=RuntimeError('offline')):
        assert not today.refresh(app)
    value = control.snapshot(app)
    assert len(value['entries']) == 1
    assert value['today_fetched_at'] is None and value['today_error']


def test_budget_reserve_blocks_automatic_totals_fetches():
    with patch.object(app, '_request_budget_remaining', return_value=app.REQUEST_BUDGET_BACKGROUND_RESERVE), \
         patch.object(app, 'api_get') as api:
        assert not today.refresh(app)
    api.assert_not_called()


def test_capped_or_stale_open_entries_are_not_presented_as_complete_totals():
    with patch.object(app, 'api_get', return_value=[interval()] * today.MAX_ENTRIES):
        assert today.refresh(app)
    assert control.snapshot(app)['today_incomplete']


def test_deleted_entries_and_foreign_workspaces_do_not_enter_the_daily_cache():
    with patch.object(app, 'api_get', return_value=[interval(), interval(2,server_deleted_at='deleted'),
                                                  interval(3,workspace_id='other')]):
        assert today.refresh(app)
    assert [entry['id'] for entry in today.cached(app)] == [1]
