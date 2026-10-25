"""Project writes use the shared socket and temporary, account-scoped metadata."""
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from test_toggl_tray import reset_state, tmp_state_dir
import toggl_tray as app
import toggl_control as control


@pytest.fixture
def project(tmp_state_dir):
    item = {"id": 7, "workspace_id": 123, "name": "Original", "color": "#123456", "active": True}
    with patch.object(app, "API_BASE", "https://api.track.toggl.com/api/v9", create=True), \
         patch.object(app, "get_api_base", side_effect=lambda: app.API_BASE, create=True), \
         patch.object(app, "_project_cache", None), \
         patch.object(app, "_last_tray_icon_path", "old-icon"), \
         patch.object(app, "_control_entries", [], create=True), \
         patch.object(app, "_control_entries_date", "", create=True):
        app.state.update(workspace_id=123, tracking=True, entry_id=101, project_id=7,
                         start_time="2026-10-25T10:00:00+00:00", description="Existing interval")
        app._project_cache = {"account": app._project_account(), "fetched_at": 10, "projects": [item]}
        yield item


def context():
    return control.snapshot(app)["project_context"]


def test_snapshot_has_opaque_context_and_correct_service_manage_link(project):
    with patch.object(app, "api_get", side_effect=AssertionError("Snapshot cannot call API")):
        result = control.snapshot(app)
    assert result["project_context"]["workspace_id"] == 123
    fingerprint = result["project_context"]["account"]
    assert isinstance(fingerprint, str) and len(fingerprint) == 64
    assert fingerprint != app.api_token
    assert result["projects_url"] == "https://track.toggl.com/projects"
    with patch.object(app, "get_api_base", return_value="https://time.example.test/api/v9", create=True):
        custom = control.snapshot(app)
    assert custom["project_context"]["account"] != fingerprint
    assert custom["projects_url"] == "https://time.example.test/projects/123/list"


def test_context_changes_for_credentials_and_workspace(project):
    original = context()
    with patch.object(app, "api_token", "other-test-token"):
        assert context()["account"] != original["account"]
    with patch.dict(app.state, {"workspace_id": 456}):
        assert context()["workspace_id"] != original["workspace_id"]
    with patch.object(app, "api_token", None):
        assert context()["account"] is None


@pytest.mark.parametrize("envelope", [False, True])
def test_create_uses_one_post_and_updates_cache_without_assigning_timer(project, envelope):
    before = dict(app.state)
    saved = {"id": 9, "workspace_id": 123, "name": "Created", "color": "#aabbcc", "active": True}
    response = {"data": saved} if envelope else saved
    with patch.object(app, "api_post", return_value=response) as post, \
         patch.object(app, "api_get", side_effect=AssertionError("No extra GET")), \
         patch.object(app, "queue_action") as queue:
        result = control.dispatch(app, "project-create", {"context": context(), "name": "  Created  ", "color": "#aabbcc"})
    post.assert_called_once_with("/workspaces/123/projects", {"name": "Created", "color": "#aabbcc", "active": True},
                                 token=app.api_token, base_url=app.API_BASE)
    queue.assert_not_called()
    assert app.state == before
    assert result["saved_project"] == saved
    assert result["projects"] == [project, saved]
    persisted = json.loads((app.STATE_DIR / "projects.json").read_text())
    assert persisted["account"] == app._project_account()
    assert persisted["projects"] == result["projects"]
    assert app.api_token not in (app.STATE_DIR / "projects.json").read_text()
    assert app._last_tray_icon_path is None


def test_update_preserves_id_timer_history_and_other_workspace_projects(project):
    other = dict(project, workspace_id=456, name="Other workspace")
    app._project_cache["projects"].append(other)
    before = dict(app.state)
    saved = dict(project, name="Renamed", color="#fedcba")
    with patch.object(app, "api_put", return_value=saved) as put, \
         patch.object(app, "api_get", side_effect=AssertionError("No extra GET")):
        result = control.dispatch(app, "project-update", {"context": context(), "project": dict(project),
                                  "name": "Renamed", "color": "#fedcba"})
    put.assert_called_once_with("/workspaces/123/projects/7", {"name": "Renamed", "color": "#fedcba"},
                                token=app.api_token, base_url=app.API_BASE)
    assert app.state == before
    assert result["saved_project"]["id"] == 7
    assert result["projects"] == [saved, other]
    app._project_cache = None
    assert app._cached_projects() == [saved, other]


@pytest.mark.parametrize("name,color", [("", "#123456"), ("  ", "#123456"), (None, "#123456"),
    ("a" * 201, "#123456"), ("Valid", "123456"), ("Valid", "#fff"), ("Valid", "#xx1234"),
    ("Valid", None), ("Valid", "#123456\n")])
def test_invalid_input_never_writes_remote(project, name, color):
    with patch.object(app, "api_post") as post:
        with pytest.raises(control.ControlError):
            control.dispatch(app, "project-create", {"context": context(), "name": name, "color": color})
    post.assert_not_called()


@pytest.mark.parametrize("change", ["workspace", "credential", "base", "missing-context"])
def test_stale_context_rejected_before_project_create(project, change):
    original = context()
    if change == "workspace":
        app.state["workspace_id"] = 456
    elif change == "credential":
        app.api_token = "other-test-token"
    elif change == "base":
        app.API_BASE = "https://other.example.test/api/v9"
    else:
        original = None
    with patch.object(app, "api_post") as post:
        with pytest.raises(control.ControlError, match="changed|refresh"):
            control.dispatch(app, "project-create", {"context": original, "name": "Created", "color": "#123456"})
    post.assert_not_called()


@pytest.mark.parametrize("field,value", [("id", 999), ("workspace_id", 456), ("name", "Other"), ("color", "#654321")])
def test_update_rejects_stale_or_other_workspace_selection(project, field, value):
    selected = dict(project, **{field: value})
    with patch.object(app, "api_put") as put:
        with pytest.raises(control.ControlError, match="changed|refresh"):
            control.dispatch(app, "project-update", {"context": context(), "project": selected, "name": "Changed", "color": "#123456"})
    put.assert_not_called()


def test_network_failure_does_not_queue_or_retry_creation(project):
    before = list(app._cached_projects())
    with patch.object(app, "api_post", side_effect=requests.ConnectionError("Disconnected")) as post, \
         patch.object(app, "queue_action") as queue:
        with pytest.raises(control.ControlError, match="refresh|Refresh"):
            control.dispatch(app, "project-create", {"context": context(), "name": "Created", "color": "#123456"})
    assert post.call_count == 1
    queue.assert_not_called()
    assert app._cached_projects() == before


@pytest.mark.parametrize("kind", ["missing-token", "rate-limit", "budget"])
def test_unavailable_connection_fails_before_write(project, kind):
    original = context()
    if kind == "missing-token":
        app.api_token = None
        original = context()
    with patch.object(app, "_rate_limit_active", return_value=kind == "rate-limit"), \
         patch.object(app, "_request_budget_exhausted", return_value=kind == "budget"), \
         patch.object(app, "api_post") as post:
        with pytest.raises(control.ControlError):
            control.dispatch(app, "project-create", {"context": original, "name": "Created", "color": "#123456"})
    post.assert_not_called()


def test_account_change_during_response_does_not_cache_into_new_account(project):
    original = context()
    def changed_account(*args, **kwargs):
        app.api_token = "other-test-token"
        return {"id": 9, "workspace_id": 123, "name": "Created", "color": "#123456"}
    with patch.object(app, "api_post", side_effect=changed_account):
        with pytest.raises(control.ControlError, match="saved"):
            control.dispatch(app, "project-create", {"context": original, "name": "Created", "color": "#123456"})
    assert app._cached_projects() == []
    assert not (app.STATE_DIR / "projects.json").exists()


def test_real_socket_creates_and_updates_project_from_mocked_service(project):
    saved = dict(project, id=9, name="Created", color="#aabbcc")
    updated = dict(saved, name="Renamed", color="#fedcba")
    server = control.start_server(app)
    try:
        with patch.object(app, "api_post", return_value=saved), patch.object(app, "api_put", return_value=updated):
            created = control.request(app.STATE_DIR, "project-create", {"context": context(), "name": "Created", "color": "#aabbcc"})
            result = control.request(app.STATE_DIR, "project-update", {"context": created["project_context"],
                    "project": created["saved_project"], "name": "Renamed", "color": "#fedcba"})
        assert result["saved_project"] == updated
        assert result["state"]["project_id"] == 7
    finally:
        server.shutdown()
        server.server_close()


def test_cli_accepts_project_actions_and_forwards_json_payload(project, capsys):
    for action in ("project-create", "project-update"):
        payload = {"context": context(), "project": dict(project), "name": "Changed", "color": "#123456"}
        with patch.object(control, "request", return_value={"saved_project": dict(project)}) as request:
            assert app._run_cli(["control", action, json.dumps(payload)]) == 0
        request.assert_called_once_with(app.STATE_DIR, action, payload)
        assert json.loads(capsys.readouterr().out)["ok"] is True


def test_update_retains_cached_associations_and_archived_flag(project):
    project.update(active=False, client_id=44, billable=True)
    with patch.object(app, "api_put", return_value={"id": 7, "workspace_id": 123, "name": "Renamed", "color": "#abcdef"}):
        result = control.dispatch(app, "project-update", {"context": context(), "project": dict(project),
                                  "name": "Renamed", "color": "#abcdef"})
    assert result["saved_project"]["active"] is False
    assert result["saved_project"]["client_id"] == 44
    assert result["saved_project"]["billable"] is True


def test_invalid_response_after_success_warns_project_may_be_saved(project):
    with patch.object(app, "api_post", return_value={"id": 9, "name": "", "color": "#aabbcc"}):
        with pytest.raises(control.ControlError, match="saved.*refresh"):
            control.dispatch(app, "project-create", {"context": context(), "name": "Created", "color": "#aabbcc"})


@pytest.mark.parametrize("status", [401, 403, 429])
def test_http_auth_and_rate_failures_are_clear_and_not_retried(project, status):
    response = requests.Response()
    response.status_code = status
    with patch.object(app, "api_post", side_effect=requests.HTTPError(response=response)) as post:
        with pytest.raises(control.ControlError, match="access denied|rate limited"):
            control.dispatch(app, "project-create", {"context": context(), "name": "Created", "color": "#123456"})
    assert post.call_count == 1
    assert len(app._cached_projects()) == 1


def test_missing_workspace_and_custom_manage_link_are_safe(project):
    app.state["workspace_id"] = None
    with patch.object(app, "get_api_base", return_value="https://time.example.test/api/v9", create=True):
        assert control.snapshot(app)["projects_url"] is None
        with patch.object(app, "api_post") as post:
            with pytest.raises(control.ControlError, match="workspace"):
                control.dispatch(app, "project-create", {"context": context(), "name": "Created", "color": "#123456"})
        post.assert_not_called()


def test_cache_write_failure_warns_saved_without_losing_updated_memory(project):
    saved = dict(project, name="Renamed", color="#fedcba")
    with patch.object(app, "api_put", return_value=saved) as put, \
         patch.object(app, "_atomic_write_text", side_effect=OSError("disk full")):
        with pytest.raises(control.ControlError, match="saved.*cache"):
            control.dispatch(app, "project-update", {"context": context(), "project": dict(project),
                                  "name": "Renamed", "color": "#fedcba"})
    assert put.call_count == 1
    assert app._cached_projects()[0]["color"] == "#fedcba"


@pytest.mark.parametrize("action", ["project-create", "project-update"])
def test_project_write_pins_validated_token_and_base_during_request_budget_hook(project, action):
    validated = context()
    original_token = app.api_token
    original_base = app.API_BASE
    response = MagicMock(status_code=200, content=b"json")
    response.json.return_value = dict(project, name="Changed", color="#abcdef")
    def replace_account():
        app.api_token = "replacement-test-token"
        app.API_BASE = "https://replacement.example.test/api/v9"
    with patch.object(app, "_record_api_request", side_effect=replace_account), \
         patch.object(app.requests, "request", return_value=response) as request:
        with pytest.raises(control.ControlError, match="saved"):
            control.dispatch(app, action, {"context": validated, "project": dict(project),
                             "name": "Changed", "color": "#abcdef"})
    assert request.call_count == 1
    method, url = request.call_args.args
    assert method == ("POST" if action == "project-create" else "PUT")
    expected_path = "/workspaces/123/projects" + ("/7" if action == "project-update" else "")
    assert url == original_base + expected_path
    assert request.call_args.kwargs["auth"].username == original_token
    assert request.call_args.kwargs["auth"].username != app.api_token


def test_stale_project_edit_requires_reopening_frozen_form(project):
    stale = dict(project, name="Stale name")
    with pytest.raises(control.ControlError, match="reopen"):
        control.dispatch(app, "project-update", {"context": context(), "project": stale,
                         "name": "Changed", "color": "#abcdef"})


def test_stale_context_requires_reopening_frozen_form(project):
    stale = context()
    app.state["workspace_id"] = 456
    with pytest.raises(control.ControlError, match="reopen"):
        control.dispatch(app, "project-create", {"context": stale, "name": "Changed", "color": "#abcdef"})
