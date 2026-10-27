"""Local socket integration tests; no desktop session or live API required."""
import json
import socket
import stat
import threading
from unittest.mock import MagicMock, patch

import pytest
import toggl_tray as app
import toggl_control as control
import toggl_cli


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


def test_state_changing_cli_commands_use_the_running_tray(tmp_state_dir, capsys):
    app.api_token = None
    server = control.start_server(app)
    try:
        with patch.object(toggl_cli, "_load_cli_runtime", side_effect=AssertionError("must use socket")), \
             patch.object(app, "_play_sound"), patch.object(app, "_notify"):
            assert toggl_cli._run_cli(["start", "Socket task"]) == 0
            started = dict(app.state)
            assert started["tracking"] is True
            assert started["description"] == "Socket task"

            assert toggl_cli._run_cli(["set-start", "09:15"]) == 0
            assert app._coerce_datetime(app.state["start_time"]).astimezone().strftime("%H:%M") == "09:15"

            assert toggl_cli._run_cli(["stop"]) == 0
            assert app.state["tracking"] is False
            assert [item["action"] for item in app._load_pending()] == ["start", "stop"]
    finally:
        server.shutdown()
        server.server_close()
    assert "Started locally" in capsys.readouterr().out


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


def test_switch_task_closes_previous_interval_and_starts_new_offline(tmp_state_dir):
    app.api_token=None
    with patch.object(app,"_play_sound"),patch.object(app,"_notify"):
        control.dispatch(app,"start",{"changes":{"description":"First"}})
        original=dict(app.state)
        result=control.dispatch(app,"switch",{"expected":original,"changes":{"description":"Next"}})
    assert result["state"]["tracking"] is True
    assert result["state"]["description"]=="Next"
    assert result["state"]["start_time"]!=original["start_time"]
    assert [item['action'] for item in app._load_pending()]==['start','stop','start']
    assert app._pending_as_entries()[0]['description']=='First'


def test_edit_start_time_updates_pending_start_and_running_timer(tmp_state_dir):
    app.api_token=None
    app.state.update(tracking=True,start_time="2026-10-25T10:00:00+00:00",description="Work")
    app.queue_action("start",start_time=app.state["start_time"],description="Work")
    control.dispatch(app,"edit-current",{"changes":{"start":"2026-10-25T09:00:00+00:00"}})
    assert app.state["start_time"]=="2026-10-25T09:00:00+00:00"
    assert app._load_pending()[0]["start_time"]==app.state["start_time"]


def test_invalid_start_time_does_not_partially_edit_description(tmp_state_dir):
    app.state.update(tracking=True,description="Original",entry_id=5,start_time="2026-10-25T10:00:00+00:00")
    with patch.object(app,"update_entry") as update:
        with pytest.raises(control.ControlError):
            control.dispatch(app,"edit-current",{"changes":{"start":"bad","description":"Changed"}})
        update.assert_not_called()
    assert app.state['description']=='Original'


def test_tray_stop_closes_cached_running_entry(tmp_state_dir):
    app.state.update(tracking=True,entry_id=101,start_time="2026-10-25T10:00:00+00:00",description="Work")
    app._control_entries=[{"id":101,"start":app.state["start_time"],"stop":None,"duration":-1,"description":"Work"}]
    app._control_entries_date=app.datetime.now().date().isoformat()
    with patch.object(app,"stop_entry",return_value={}),patch.object(app,"_play_sound"):
        app.toggle_tracking()
    result=control.snapshot(app)
    assert result["entries"][0]["stop"] is not None
    assert result["entries"][0]["duration"]>=0


def test_external_timer_switch_does_not_display_previous_timer_as_running(tmp_state_dir):
    app.state.update(tracking=True,entry_id=102,start_time="2026-10-25T11:00:00+00:00",description="New")
    app._control_entries=[{"id":101,"start":"2026-10-25T10:00:00+00:00","stop":None,"duration":-1,"description":"Old"}]
    app._control_entries_date=app.datetime.now().date().isoformat()
    result=control.snapshot(app)
    assert not any(e['id']==101 for e in result['entries'])
    assert 'refresh' in result['warning'].lower()


def test_stale_start_button_does_not_stop_or_edit_replacement_timer(tmp_state_dir):
    before=dict(app.state)
    app.state.update(tracking=True,entry_id=99,description='Replacement')
    with patch.object(app,'toggle_tracking') as toggle:
        with pytest.raises(control.ControlError,match='changed'):
            control.dispatch(app,'start',{'expected':before,'changes':{'description':'New work'}})
        toggle.assert_not_called()
    assert app.state['description']=='Replacement'


def test_sync_button_runs_existing_offline_recovery(tmp_state_dir):
    with patch.object(app,'_run_sync_cycle') as sync:
        result=control.dispatch(app,'sync',{})
    sync.assert_called_once_with()
    assert 'pending_count' in result
