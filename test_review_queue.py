"""Regression checks for queue review findings; all runtime state is temporary."""
import threading
from unittest.mock import patch

import pytest

import toggl_tray as app
import toggl_cli
import toggl_control as control


@pytest.mark.parametrize("failure", ["offline", "rate_limit", "network"])
def test_queued_cloud_stop_retains_project_for_pending_description_edit(tmp_state_dir, failure):
    app.state.update(tracking=True, entry_id=101, workspace_id=99, project_id=7,
                     description="Work", start_time="2026-10-25T10:00:00+00:00")
    app.api_token = None if failure == "offline" else "test-token"
    error = app.RateLimitedError(60) if failure == "rate_limit" else RuntimeError("offline")
    with patch.object(app, "stop_entry", side_effect=error), patch.object(app, "_notify"), patch.object(app, "_play_sound"):
        app.toggle_tracking()
    queued = app._load_pending()[0]
    assert queued["project_id"] == 7
    assert queued["workspace_id"] == 99
    selected = app._pending_as_entries()[0]
    assert selected["project_id"] == 7
    control.dispatch(app, "edit-entry", {"entry": selected, "changes": {"description": "Renamed"}})
    app.api_token = "test-token"
    with patch.object(app, "update_entry", return_value={}) as update:
        assert app.sync_pending() == 0
    assert update.call_args.args == (99, 101, {
        "stop": queued["stop_time"], "start": queued["start_time"],
        "duration": app._duration_seconds(queued["start_time"], queued["stop_time"]),
        "description": "Renamed", "project_id": 7,
    })


def test_legacy_stop_description_edit_keeps_unknown_project_unchanged(tmp_state_dir):
    app.queue_action("stop", entry_id=101, start_time="2026-10-25T10:00:00+00:00",
                     stop_time="2026-10-25T11:00:00+00:00", description="Work")
    selected = app._pending_as_entries()[0]
    control.dispatch(app, "edit-entry", {"entry": selected, "changes": {"description": "Renamed"}})
    with patch.object(app, "update_entry", return_value={}) as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[2]["description"] == "Renamed"
    assert "project_id" not in update.call_args.args[2]


def test_cli_offline_stop_retains_project(tmp_state_dir):
    app.api_token = None
    app.state.update(tracking=True, entry_id=101, project_id=7,
                     description="Work", start_time="2026-10-25T10:00:00+00:00")
    with patch.object(toggl_cli, "_load_cli_runtime"):
        assert toggl_cli._cli_stop(None) == 0
    assert app._load_pending()[0]["project_id"] == 7


def test_completed_sync_does_not_resurrect_backup_and_repost(tmp_state_dir):
    app.queue_action("start", start_time="2026-10-25T10:00:00+00:00", description="Work")
    app.queue_action("stop", stop_time="2026-10-25T11:00:00+00:00")
    assert app._backup_path(app.PENDING_FILE).exists()
    with patch.object(app, "api_post", return_value={"id": 101}) as post, \
         patch.object(app, "_sync_cloud_state"), patch.object(app, "_health_check"):
        app._run_sync_cycle(now=1000)
        assert app._load_pending() == []
        assert not app._backup_path(app.PENDING_FILE).exists()
        app._run_sync_cycle(now=1300)
    assert post.call_count == 1


def test_action_queued_after_sync_is_not_deleted_by_cycle_cleanup(tmp_state_dir):
    app.queue_action("delete", entry_id=101)
    original_sync = app.sync_pending
    def finish_then_queue():
        remaining = original_sync()
        assert remaining == 0
        app.queue_action("delete", entry_id=202)
        return remaining
    with patch.object(app, "sync_pending", side_effect=finish_then_queue), \
         patch.object(app, "delete_entry", return_value={}), patch.object(app, "_health_check"):
        app._run_sync_cycle(now=1000)
    assert [item["entry_id"] for item in app._load_pending()] == [202]


def test_failed_sync_keeps_pending_and_recovery_backup(tmp_state_dir):
    app.queue_action("delete", entry_id=101)
    app.queue_action("delete", entry_id=202)
    with patch.object(app, "delete_entry", side_effect=RuntimeError("offline")):
        assert app.sync_pending() == 2
    assert len(app._load_pending()) == 2
    assert app._backup_path(app.PENDING_FILE).exists()


def test_hotkey_during_socket_sync_reports_busy_without_changing_timer(tmp_state_dir):
    started = threading.Event()
    release = threading.Event()
    def slow_sync(*, retry_review=False):
        assert retry_review
        started.set()
        assert release.wait(3)
    with patch.object(app, "_run_sync_cycle", side_effect=slow_sync), patch.object(app, "_notify") as notify:
        worker = threading.Thread(target=lambda: control.dispatch(app, "sync", {}))
        worker.start()
        assert started.wait(2)
        try:
            app.toggle_tracking()
            assert app.state["tracking"] is False
            assert "busy" in notify.call_args.args[0].lower()
        finally:
            release.set()
            worker.join(3)
            assert not worker.is_alive()
    assert app.state["tracking"] is False
    assert app._load_pending() == []
