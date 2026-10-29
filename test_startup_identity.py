"""Only intervals created during this bootstrap may acquire its resolved identity."""
import threading
from unittest.mock import patch
import pytest
import toggl_projects as projects
import toggl_task_tree as tree
import toggl_tray as app


def resolve_keyring():
    with patch.object(app, 'get_api_token', return_value='resolved-test-token'), \
         patch.object(app, 'fetch_me', return_value={'default_workspace_id':'ws_123'}), \
         patch.object(app, '_refresh_projects'), patch.object(app, '_refresh_tasks'), \
         patch.object(tree, 'refresh'), patch.object(app, 'sync_loop'):
        app._bootstrap_and_sync(threading.Event())


@pytest.mark.parametrize('stopped, workspace', [(False, 'ws_123'), (True, 'ws_123'), (False, None)])
def test_work_created_before_keyring_remains_in_history_after_identity_load(stopped, workspace):
    app.api_token = None
    app.state.update(workspace_id=workspace, description='Started during bootstrap')
    app._begin_startup_scope()
    with patch.object(app, '_notify'), patch.object(app, '_play_sound'):
        app.toggle_tracking(prompt_for_description=False)
        if stopped:
            app.toggle_tracking(prompt_for_description=False)
    resolve_keyring()
    scope = projects.context(app)
    assert scope['account'] and scope['workspace_id'] == 'ws_123'
    assert all(entry['_task_context'] == scope for entry in app._pending_as_entries())
    assert tree.cached_entries(app)[0]['description'] == 'Started during bootstrap'
    if not stopped:
        assert app.state['_account'] == scope['account']
    app._save_pending([])  # Activity remains after successful upload removes its queue copy.
    assert tree.tree(app)[0]['activities'][0]['description'] == 'Started during bootstrap'


def test_preexisting_unscoped_queue_entries_are_not_adopted():
    app.api_token = None
    app.queue_action('start', start_time='2026-10-28T00:00:00+00:00', stop_time='2026-10-28T00:01:00+00:00')
    old_id = app._load_pending()[0]['_queue_id']
    app._begin_startup_scope()
    with patch.object(app, '_notify'), patch.object(app, '_play_sound'):
        app.toggle_tracking(prompt_for_description=False)
    resolve_keyring()
    queue = app._load_pending()
    assert next(item for item in queue if item['_queue_id'] == old_id)['_task_context']['account'] is None
    assert queue[-1]['_task_context'] == projects.context(app)


def test_work_is_not_adopted_after_the_user_replaces_the_account():
    app.api_token = None
    app._begin_startup_scope()
    with patch.object(app, '_notify'), patch.object(app, '_play_sound'):
        app.toggle_tracking(prompt_for_description=False)
    before = app._load_pending()
    def lookup():
        app.api_token = 'manually-replaced-test-token'
        return 'old-keyring-test-token'
    with patch.object(app, 'get_api_token', side_effect=lookup), \
         patch.object(app, '_refresh_projects'), patch.object(app, '_refresh_tasks'), \
         patch.object(tree, 'refresh'), patch.object(app, 'sync_loop'):
        app._bootstrap_and_sync(threading.Event())
    assert app.api_token == 'manually-replaced-test-token'
    assert app._load_pending() == before


def test_saved_account_mismatch_does_not_adopt_bootstrap_work():
    app.state['_account'] = 'another-account-fingerprint'
    app.api_token = None
    app._begin_startup_scope()
    with patch.object(app, '_notify'), patch.object(app, '_play_sound'):
        app.toggle_tracking(prompt_for_description=False)
    before = app._load_pending()
    resolve_keyring()
    assert app._load_pending() == before


def test_later_keyring_retry_binds_work_created_while_the_keyring_was_locked():
    app.api_token = None
    app._begin_startup_scope()
    with patch.object(app, '_notify'), patch.object(app, '_play_sound'):
        app.toggle_tracking(prompt_for_description=False)
    with patch.object(app, 'get_api_token', return_value='resolved-test-token'), \
         patch.object(app, 'sync_pending'), patch.object(app, '_health_check'):
        app._run_sync_cycle()
    assert app._pending_as_entries()[0]['_task_context'] == projects.context(app)


@pytest.mark.parametrize('changed', ['server', 'workspace'])
def test_bootstrap_work_is_not_adopted_after_its_server_or_workspace_changes(changed):
    app.api_token = None
    app._begin_startup_scope()
    with patch.object(app, '_notify'), patch.object(app, '_play_sound'):
        app.toggle_tracking(prompt_for_description=False)
    before = app._load_pending()
    if changed == 'workspace':
        app.state['workspace_id'] = 'different-workspace'
        resolve_keyring()
    else:
        with patch.object(app, 'get_api_base', return_value='https://changed.example.test/api/v9'):
            resolve_keyring()
    assert app._load_pending() == before


def test_a_legacy_restored_stop_is_pinned_after_its_bootstrap_account_resolves():
    app.api_token = None
    app.state.update(tracking=True, entry_id=123,
                     start_time='2026-10-28T00:00:00+00:00', _account=None)
    app.save_state()
    app.load_state()
    app._begin_startup_scope()
    with patch.object(app, '_notify'), patch.object(app, '_play_sound'):
        app.toggle_tracking(prompt_for_description=False)
    resolve_keyring()
    queued = app._load_pending()[0]
    assert queued['_verify_snapshot']['account'] == projects.account(app)
    app.api_token = 'another-account-token'
    with patch.object(app, 'api_get') as get:
        assert app.sync_pending() == 1
    get.assert_not_called()
