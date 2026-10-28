"""Server task metadata stays scoped and reaches timers without becoming descriptions."""
import json
from unittest.mock import patch

import pytest
import requests

import toggl_cli
import toggl_control as control
import toggl_tasks
import toggl_tray as app


@pytest.fixture
def task_account(tmp_state_dir):
    project = {"id": 7, "workspace_id": 123, "name": "Project", "color": "#123456", "active": True}
    task = {"id": 31, "workspace_id": 123, "project_id": 7, "name": "Original task", "active": True}
    with patch.object(app, "API_BASE", "https://api.example.test/api/v9", create=True), \
         patch.object(app, "get_api_base", side_effect=lambda: app.API_BASE, create=True), \
         patch.object(app, "_project_cache", {"account": app._project_account(), "fetched_at": 1, "projects": [project]}), \
         patch.object(app, "_task_cache", {"context": toggl_tasks.context(app), "fetched_at": 1, "tasks": [task]}), \
         patch.object(app, "_task_capability", "supported"):
        app.state.update(workspace_id=123, tracking=False, entry_id=None, start_time=None,
                         description="", project_id=7, task_id=None)
        # The context contains workspace state, so rebuild it after setting the fixture workspace.
        app._task_cache = {"context": toggl_tasks.context(app), "fetched_at": 1, "tasks": [task]}
        yield project, task


def context():
    return control.snapshot(app)["project_context"]


def test_snapshot_exposes_server_tasks_and_capability(task_account):
    _, task = task_account
    result = control.snapshot(app)
    assert result["tasks"] == [task]
    assert result["task_capability"] == "supported"


def test_refresh_reads_envelope_and_persists_account_scoped_cache(task_account):
    saved = {"id": 32, "workspace_id": 123, "project_id": 7, "name": "Fetched", "active": True}
    app._task_cache = None
    def response(_method, _path, **kwargs):
        return {"data": [saved], "total_count": 1} if kwargs["params"]["active"] == "true" else {"data": [], "total_count": 0}
    with patch.object(app, "_api", side_effect=response) as request:
        assert toggl_tasks.refresh(app, force=True) is True
    assert request.call_count == 2
    assert [call.kwargs["params"]["active"] for call in request.call_args_list] == ["true", "false"]
    assert all(call.kwargs["token"] == app.api_token and call.kwargs["base_url"] == app.API_BASE
               for call in request.call_args_list)
    assert app._cached_tasks() == [saved]
    persisted = json.loads((app.STATE_DIR / "tasks.json").read_text())
    assert persisted["context"] == context()
    assert persisted["tasks"] == [saved]
    assert app.api_token not in (app.STATE_DIR / "tasks.json").read_text()


def test_refresh_feature_detects_unsupported_endpoint_without_losing_activities(task_account):
    response = requests.Response()
    response.status_code = 404
    app._task_cache = None
    with patch.object(app, "_api", side_effect=requests.HTTPError(response=response)):
        assert toggl_tasks.refresh(app, force=True) is False
    assert toggl_tasks.capability(app) == "unavailable"


def test_refresh_combines_active_and_archived_from_boolean_filters(task_account):
    active = {"id": 32, "workspace_id": 123, "project_id": 7, "name": "Active", "active": True}
    archived = {"id": 33, "workspace_id": 123, "project_id": 7, "name": "Archived", "active": False}
    app._task_cache = None
    def response(_method, _path, **kwargs):
        item = active if kwargs["params"]["active"] == "true" else archived
        return {"data": [item], "total_count": 1}
    with patch.object(app, "_api", side_effect=response):
        assert toggl_tasks.refresh(app, force=True) is True
    assert app._cached_tasks() == [active, archived]


def test_refresh_discards_response_after_account_switch(task_account):
    app._task_cache = None
    original = context()
    def switch_account(*_args, **_kwargs):
        app.api_token = "replacement-token"
        return {"data": [{"id": 32, "workspace_id": 123, "project_id": 7,
                           "name": "Private old account task", "active": True}]}
    with patch.object(app, "_api", side_effect=switch_account) as request:
        assert toggl_tasks.refresh(app, force=True) is False
    assert request.call_args.kwargs["token"] == "tok"
    assert app._cached_tasks() == []
    assert not (app.STATE_DIR / "tasks.json").exists()
    assert context() != original


def test_create_and_update_archive_restore_use_task_routes(task_account):
    _, original = task_account
    created = {"id": 32, "workspace_id": 123, "project_id": 7, "name": "Created", "active": True}
    archived = dict(original, name="Renamed", active=False)
    restored = dict(archived, active=True)
    with patch.object(app, "api_post", return_value={"data": created}) as post:
        result = control.dispatch(app, "task-create", {"context": context(), "project_id": 7, "name": "  Created  "})
    post.assert_called_once_with("/workspaces/123/projects/7/tasks", {"name": "Created", "active": True},
                                 token=app.api_token, base_url=app.API_BASE)
    assert result["saved_task"] == created
    assert app.state["task_id"] is None
    with patch.object(app, "api_put", return_value=archived) as put:
        result = control.dispatch(app, "task-update", {"context": context(), "task": dict(original),
                                  "name": "Renamed", "active": False})
    put.assert_called_once_with("/workspaces/123/projects/7/tasks/31", {"name": "Renamed", "active": False},
                                token=app.api_token, base_url=app.API_BASE)
    with patch.object(app, "api_put", return_value=restored):
        result = control.dispatch(app, "task-update", {"context": context(), "task": result["saved_task"],
                                  "name": "Renamed", "active": True})
    assert result["saved_task"]["active"] is True


@pytest.mark.parametrize("payload", [
    {"name": "", "project_id": 7},
    {"name": "x" * 201, "project_id": 7},
    {"name": "New", "project_id": 999},
    {"name": "Original task", "project_id": 7},
])
def test_invalid_create_never_writes(task_account, payload):
    with patch.object(app, "api_post") as post, pytest.raises(control.ControlError):
        control.dispatch(app, "task-create", {"context": context(), **payload})
    post.assert_not_called()


def test_stale_context_and_frozen_task_are_rejected(task_account):
    _, task = task_account
    stale_context = context()
    app.state["workspace_id"] = 999
    with patch.object(app, "api_post") as post, pytest.raises(control.ControlError, match="changed|refresh"):
        control.dispatch(app, "task-create", {"context": stale_context, "project_id": 7, "name": "New"})
    post.assert_not_called()
    app.state["workspace_id"] = 123
    with patch.object(app, "api_put") as put, pytest.raises(control.ControlError, match="changed|refresh"):
        control.dispatch(app, "task-update", {"context": context(), "task": dict(task, name="Stale"),
                         "name": "Changed", "active": True})
    put.assert_not_called()


def test_task_selection_forces_project_but_keeps_description_separate(task_account):
    _, task = task_account
    changes = control._changes(app, {"task_id": task["id"], "description": "Details"}, app.state)
    assert changes == {"task_id": 31, "description": "Details", "project_id": 7}


def test_cli_accepts_task_actions(task_account, capsys):
    _, task = task_account
    for action in ("task-create", "task-update"):
        payload = {"context": context(), "task": task, "project_id": 7, "name": "Changed", "active": True}
        with patch.object(control, "request", return_value={"saved_task": task}) as request:
            assert toggl_cli._run_cli(["control", action, json.dumps(payload)]) == 0
        request.assert_called_once_with(app.STATE_DIR, action, payload)
        assert json.loads(capsys.readouterr().out)["ok"] is True
