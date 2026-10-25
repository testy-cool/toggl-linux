"""Local socket integration tests; no desktop session or live API required."""
import json
import socket
import stat
import threading
from unittest.mock import MagicMock, patch

import pytest
import test_toggl_tray
from test_toggl_tray import reset_state, tmp_state_dir
import toggl_tray as app
import toggl_control as control


def test_socket_reports_same_timer_and_rejects_unknown_action(tmp_state_dir):
    app.state.update(description="Existing timer", project_id=7)
    server = control.start_server(app)
    try:
        result = control.request(app.STATE_DIR, "snapshot", {})
        assert result["state"]["description"] == "Existing timer"
        assert result["state"]["project_id"] == 7
        assert stat.S_IMODE((app.STATE_DIR / "control.sock").stat().st_mode) == 0o600
        with pytest.raises(control.ControlError, match="Unknown action"):
            control.request(app.STATE_DIR, "erase-everything", {})
    finally:
        server.shutdown()
        server.server_close()


def test_edit_pending_entry_changes_queue_and_running_timer(tmp_state_dir):
    app.api_token = None
    app.state.update(tracking=True, entry_id=None, start_time="2026-10-25T10:00:00+00:00", description="Before")
    app.queue_action("start", start_time=app.state["start_time"], description="Before")
    entry = app._pending_as_entries()[0]
    result = control.dispatch(app, "edit-entry", {"entry": entry, "changes": {"description": "After"}})
    assert app._load_pending()[0]["description"] == "After"
    assert result["state"]["description"] == "After"
    assert result["state"]["tracking"] is True


def test_stale_pending_selection_cannot_edit_another_entry(tmp_state_dir):
    app.queue_action("start", start_time="2026-10-25T10:00:00+00:00", description="Original")
    old = app._pending_as_entries()[0]
    app._save_pending([{"action": "start", "start_time": "2026-10-25T11:00:00+00:00", "description": "Other"}])
    with pytest.raises(control.ControlError, match="changed"):
        control.dispatch(app, "edit-entry", {"entry": old, "changes": {"description": "Wrong"}})
    assert app._load_pending()[0]["description"] == "Other"


def test_cloud_edit_failure_preserves_current_description(tmp_state_dir):
    app.state.update(tracking=True, entry_id=17, description="Before")
    with patch.object(app, "update_entry", side_effect=RuntimeError("offline")):
        with pytest.raises(RuntimeError, match="offline"):
            control.dispatch(app, "edit-current", {"changes": {"description": "After"}})
    assert app.state["description"] == "Before"


def test_start_stop_are_idempotent_and_use_existing_toggle(tmp_state_dir):
    app.api_token = None
    with patch.object(app, "_play_sound"), patch.object(app, "_notify"):
        control.dispatch(app, "start", {"changes": {"description": "Offline work", "project_id": None}})
        assert app.state["tracking"] is True
        control.dispatch(app, "start", {"changes": {"description": "Do not overwrite"}})
        assert app.state["description"] == "Offline work"
        control.dispatch(app, "stop", {})
        control.dispatch(app, "stop", {})
    assert app.state["tracking"] is False
    assert len(app._load_pending()) == 2


def test_snapshot_does_not_make_network_calls(tmp_state_dir):
    with patch.object(app, "api_get") as api:
        control.dispatch(app, "snapshot", {})
        api.assert_not_called()


def test_description_edit_preserves_existing_uncached_project(tmp_state_dir):
    app.state.update(project_id=17)
    result = control.dispatch(app, "edit-current", {"changes": {"description": "Changed offline", "project_id": "17"}})
    assert result["state"]["project_id"] == 17
    assert result["state"]["description"] == "Changed offline"


def test_stale_current_form_does_not_overwrite_a_new_timer(tmp_state_dir):
    original = dict(app.state, tracking=True, entry_id=101, start_time="2026-10-25T10:00:00+00:00")
    app.state.update(original, entry_id=102, description="New timer")
    with patch.object(app, "update_entry") as update:
        with pytest.raises(control.ControlError, match="changed"):
            control.dispatch(app, "edit-current", {"expected": original, "changes": {"description": "Old form"}})
        update.assert_not_called()
    assert app.state["description"] == "New timer"


def test_cloud_timer_change_during_save_does_not_overwrite_new_local_timer(tmp_state_dir):
    app.state.update(tracking=True, entry_id=101, start_time="2026-10-25T10:00:00+00:00", description="A")
    def change_timer(*args):
        app.state.update(entry_id=102, start_time="2026-10-25T11:00:00+00:00", description="B")
    with patch.object(app, "update_entry", side_effect=change_timer):
        with pytest.raises(control.ControlError, match="changed"):
            control.dispatch(app, "edit-current", {"changes": {"description": "A edited"}})
    assert app.state["entry_id"] == 102
    assert app.state["description"] == "B"


def test_pending_stop_project_edit_survives_sync(tmp_state_dir):
    app._save_pending([{"action": "stop", "entry_id": 101, "workspace_id": "ws_123", "start_time": "2026-10-25T10:00:00+00:00", "stop_time": "2026-10-25T11:00:00+00:00", "description": "Work"}])
    with patch.object(app, "_cached_projects", return_value=[{"id": 8, "workspace_id": "ws_123"}]):
        entry = app._pending_as_entries()[0]
        control.dispatch(app, "edit-entry", {"entry": entry, "changes": {"project_id": 8}})
    assert app._pending_as_entries()[0]["project_id"] == 8
    with patch.object(app, "update_entry", return_value={}) as update:
        assert app.sync_pending() == 0
    assert update.call_args.args[2]["project_id"] == 8


def test_entry_in_other_workspace_can_keep_its_project(tmp_state_dir):
    app._save_pending([{"action":"start", "workspace_id":2, "project_id":9, "description":"Before", "start_time":"2026-10-25T10:00:00+00:00"}])
    entry=app._pending_as_entries()[0]
    control.dispatch(app,"edit-entry",{"entry":entry,"changes":{"description":"After", "project_id":"9"}})
    assert app._load_pending()[0]["description"]=="After"
    assert app._load_pending()[0]["project_id"]==9


def test_live_icon_edit_does_not_require_unpublished_menu_helper(tmp_state_dir):
    app.icon_ref = MagicMock()
    result=control.dispatch(app,"edit-current",{"changes":{"description":"Saved"}})
    assert result["state"]["description"]=="Saved"


def test_stopped_entry_snapshot_has_a_stop_time(tmp_state_dir):
    app.state.update(tracking=True, entry_id=101, start_time="2026-10-25T10:00:00+00:00", description="Work")
    app._control_entries=[{"id":101,"start":app.state["start_time"],"stop":None,"duration":-1,"description":"Work"}]
    app._control_entries_date=app.datetime.now().date().isoformat()
    with patch.object(app,"stop_entry",return_value={}), patch.object(app,"_play_sound"):
        result=control.dispatch(app,"stop",{})
    assert result["entries"][0]["stop"] is not None
    assert result["entries"][0]["duration"]>=0
