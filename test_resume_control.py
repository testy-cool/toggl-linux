"""Resume creates a new interval from an authoritative, completed entry."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import toggl_tray as app
import toggl_cli
import toggl_control as control


@pytest.fixture
def history(tmp_state_dir):
    now = datetime.now(timezone.utc)
    entry = {"id": 101, "workspace_id": "history-ws", "project_id": 7,
             "description": "Previous task", "start": (now - timedelta(minutes=20)).isoformat(),
             "stop": (now - timedelta(minutes=10)).isoformat(), "duration": 600}
    with patch.object(app, "_control_entries", [entry], create=True), \
         patch.object(app, "_control_entries_date", app.datetime.now().date().isoformat(), create=True), \
         patch.object(app, "_cached_projects", return_value=[]), \
         patch.object(app, "_notify"), patch.object(app, "_play_sound"):
        yield entry


def test_resume_online_creates_new_interval_without_editing_history(history):
    previous = dict(history)
    start = datetime.now(timezone.utc).isoformat()
    with patch.object(app, "start_entry", return_value={"id": 999, "start": start}) as create, \
         patch.object(app, "update_entry") as edit:
        result = control.dispatch(app, "resume", {"entry": dict(history), "expected": dict(app.state)})
    create.assert_called_once_with("history-ws", description="Previous task", project_id=7)
    edit.assert_not_called()
    assert history == previous
    assert result["state"]["tracking"] is True
    assert result["state"]["entry_id"] == 999
    assert result["state"]["start_time"] == start
    assert result["state"]["start_time"] != previous["start"]
    assert result["state"]["workspace_id"] == "history-ws"


def test_resume_stops_current_task_before_online_start_in_another_workspace(history):
    app.state.update(tracking=True, entry_id=202, workspace_id="current-ws", project_id=8,
                     description="Current", start_time="2026-10-25T08:00:00+00:00")
    calls = []
    def stop(workspace, entry):
        calls.append(("stop", workspace, entry))
    def start(workspace, **kwargs):
        assert app.state["tracking"] is False
        calls.append(("start", workspace, kwargs))
        return {"id": 999, "start": datetime.now(timezone.utc).isoformat()}
    with patch.object(app, "stop_entry", side_effect=stop), patch.object(app, "start_entry", side_effect=start):
        result = control.dispatch(app, "resume", {"entry": dict(history), "expected": dict(app.state)})
    assert calls == [("stop", "current-ws", 202), ("start", "history-ws", {"description": "Previous task", "project_id": 7})]
    stopped = next(e for e in result["entries"] if e["id"] == 202)
    assert stopped["description"] == "Current"
    assert stopped["workspace_id"] == "current-ws"
    assert stopped["project_id"] == 8
    assert stopped["stop"] is not None


def test_resume_offline_keeps_old_project_and_workspace_on_stop(history):
    app.api_token = None
    app.state.update(tracking=True, entry_id=202, workspace_id="current-ws", project_id=8,
                     description="Current", start_time="2026-10-25T08:00:00+00:00")
    result = control.dispatch(app, "resume", {"entry": dict(history), "expected": dict(app.state)})
    queue = app._load_pending()
    assert [q["action"] for q in queue] == ["stop", "start"]
    assert (queue[0]["workspace_id"], queue[0]["project_id"], queue[0]["description"]) == ("current-ws", 8, "Current")
    assert (queue[1]["workspace_id"], queue[1]["project_id"], queue[1]["description"]) == ("history-ws", 7, "Previous task")
    assert result["state"]["tracking"] is True
    assert result["state"]["entry_id"] is None
    assert result["state"]["start_time"] == queue[1]["start_time"]


def test_resume_completed_pending_entry_preserves_original_interval(history):
    app.api_token = None
    app._control_entries = []
    # The snapshot shows today's pending entries, including just after midnight.
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    app.queue_action("start", start_time=start.isoformat(), description="Pending task", project_id=17,
                     workspace_id="pending-ws")
    app.queue_action("stop", stop_time=datetime.now(timezone.utc).isoformat(), workspace_id="pending-ws")
    selected = control.snapshot(app)["entries"][0]
    original_queue = app._load_pending()
    result = control.dispatch(app, "resume", {"entry": selected, "expected": dict(app.state)})
    assert app._load_pending()[:2] == original_queue
    assert app._load_pending()[2]["action"] == "start"
    assert result["state"]["description"] == "Pending task"
    assert result["state"]["project_id"] == 17
    assert result["state"]["workspace_id"] == "pending-ws"


@pytest.mark.parametrize("field,value", [
    ("id", 999), ("description", "Injected"), ("project_id", 999), ("workspace_id", "other"),
    ("start", "2026-10-25T00:00:00+00:00"), ("stop", None), ("duration", 10),
    ("_offline", True),
])
def test_resume_rejects_changed_selection_before_stopping_current(history, field, value):
    app.state.update(tracking=True, entry_id=202, start_time="2026-10-25T08:00:00+00:00", description="Current")
    before = dict(app.state)
    selected = dict(history, **{field: value})
    with patch.object(app, "toggle_tracking") as toggle:
        with pytest.raises(control.ControlError, match="changed"):
            control.dispatch(app, "resume", {"entry": selected, "expected": before})
        toggle.assert_not_called()
    assert app.state == before


@pytest.mark.parametrize("field,value", [("entry_id", 999), ("workspace_id", "other"), ("description", "Changed")])
def test_resume_rejects_stale_current_timer_without_mutation(history, field, value):
    expected = dict(app.state)
    app.state[field] = value
    before = dict(app.state)
    with patch.object(app, "toggle_tracking") as toggle:
        with pytest.raises(control.ControlError, match="Timer changed"):
            control.dispatch(app, "resume", {"entry": dict(history), "expected": expected})
        toggle.assert_not_called()
    assert app.state == before


@pytest.mark.parametrize("selection", [None, {}, "bad", {"id": 999}])
def test_resume_rejects_missing_or_invalid_selection(history, selection):
    before = dict(app.state)
    with patch.object(app, "toggle_tracking") as toggle:
        with pytest.raises(control.ControlError):
            control.dispatch(app, "resume", {"entry": selection, "expected": before})
        toggle.assert_not_called()
    assert app.state == before


def test_resume_rejects_missing_expected_state(history):
    with patch.object(app, "toggle_tracking") as toggle:
        with pytest.raises(control.ControlError, match="Timer changed"):
            control.dispatch(app, "resume", {"entry": dict(history)})
        toggle.assert_not_called()


def test_resume_rejects_current_running_entry(history):
    history.update(stop=None, duration=-1)
    app.state.update(tracking=True, entry_id=history["id"], start_time=history["start"])
    with patch.object(app, "toggle_tracking") as toggle:
        with pytest.raises(control.ControlError, match="completed"):
            control.dispatch(app, "resume", {"entry": dict(history), "expected": dict(app.state)})
        toggle.assert_not_called()


def test_resume_rejects_entry_from_stale_date_cache(history):
    app._control_entries_date = "2000-01-01"
    with patch.object(app, "toggle_tracking") as toggle:
        with pytest.raises(control.ControlError, match="changed"):
            control.dispatch(app, "resume", {"entry": dict(history), "expected": dict(app.state)})
        toggle.assert_not_called()


def test_resume_rejects_unknown_entry_workspace(history):
    history["workspace_id"] = None
    with patch.object(app, "toggle_tracking") as toggle:
        with pytest.raises(control.ControlError, match="workspace"):
            control.dispatch(app, "resume", {"entry": dict(history), "expected": dict(app.state)})
        toggle.assert_not_called()


def test_resume_uses_real_cli_and_local_socket_without_cloud_calls(history, capsys):
    app.api_token = None
    server = control.start_server(app)
    try:
        with patch.object(app, "api_get", side_effect=AssertionError("No live API")), \
             patch.object(app, "api_post", side_effect=AssertionError("No live API")):
            payload = {"entry": dict(history), "expected": dict(app.state)}
            assert toggl_cli._run_cli(["control", "resume", json.dumps(payload)]) == 0
            response = json.loads(capsys.readouterr().out)
            assert response["ok"] is True
            result = response["result"]
        assert result["state"]["tracking"] is True
        assert result["state"]["description"] == "Previous task"
        assert result["state"]["workspace_id"] == "history-ws"
        assert app._load_pending()[0]["action"] == "start"
    finally:
        server.shutdown()
        server.server_close()


def test_resume_preserves_existing_open_pending_interval(history):
    app.api_token = None
    app.state.update(tracking=True, entry_id=None, workspace_id="current-ws", project_id=8,
                     description="Pending current", start_time="2026-10-25T08:00:00+00:00")
    app.queue_action("start", start_time=app.state["start_time"], description=app.state["description"],
                     workspace_id="current-ws", project_id=8)
    control.dispatch(app, "resume", {"entry": dict(history), "expected": dict(app.state)})
    operations = app._pending_operations(app._load_pending())
    assert [op["kind"] for op in operations] == ["create", "open"]
    assert (operations[0]["workspace_id"], operations[0]["project_id"], operations[0]["description"]) == ("current-ws", 8, "Pending current")
    assert (operations[1]["workspace_id"], operations[1]["project_id"], operations[1]["description"]) == ("history-ws", 7, "Previous task")


def test_resume_cli_forwards_action_and_json_payload_to_socket_client(history, capsys):
    payload = {"entry": dict(history), "expected": dict(app.state)}
    response = {"state": {"tracking": True, "description": "Previous task"}}
    with patch.object(control, "request", return_value=response) as request:
        assert toggl_cli._run_cli(["control", "resume", json.dumps(payload)]) == 0
    request.assert_called_once_with(app.STATE_DIR, "resume", payload)
    assert json.loads(capsys.readouterr().out) == {"ok": True, "result": response}
