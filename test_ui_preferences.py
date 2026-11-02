"""Work-panel expansion persists privately without entering timer state."""
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

import toggl_control as control
import toggl_projects
import toggl_tasks
import toggl_tray as app
import toggl_ui_preferences
from toggl_webview import Bridge


def _seed_work():
    project = {"id": 7, "workspace_id": 123, "name": "Studio", "active": True}
    task = {"id": 31, "workspace_id": 123, "project_id": 7, "name": "Saved", "active": True}
    app.state.update(workspace_id=123, project_id=7, task_id=31, description="Running")
    app._project_cache = {
        "account": app._project_account(),
        "fetched_at": 1,
        "projects": [project],
    }
    app._task_cache = {
        "context": toggl_tasks.context(app),
        "fetched_at": 1,
        "tasks": [task],
    }
    return project, task


def test_expansion_survives_module_restart_and_stays_out_of_timer_state(tmp_state_dir):
    project, task = _seed_work()
    original = dict(app.state)
    payload = {
        "context": toggl_projects.context(app),
        "known_projects": [project["id"], None, project["id"], "unknown"],
        "expanded_projects": [project["id"], None, project["id"], "unknown"],
        "expanded_tasks": [task["id"], task["id"], "unknown"],
    }

    result = control.dispatch(app, "save-work-preferences", payload)
    assert result["work_preferences"] == {
        "configured": True,
        "known_projects": ["7", None],
        "expanded_projects": ["7", None],
        "expanded_tasks": ["31"],
    }
    assert app.state == original

    path = tmp_state_dir / toggl_ui_preferences.FILE_NAME
    assert path.stat().st_mode & 0o777 == 0o600
    saved = path.read_text()
    assert app.api_token not in saved
    assert json.loads(saved)["version"] == 1

    restarted = importlib.reload(toggl_ui_preferences)
    assert restarted.read(app) == result["work_preferences"]
    assert control.snapshot(app)["work_preferences"] == result["work_preferences"]


def test_expansion_is_scoped_to_the_account_and_workspace(tmp_state_dir):
    _seed_work()
    first_context = toggl_projects.context(app)
    control.dispatch(app, "save-work-preferences", {
        "context": first_context,
        "known_projects": [7],
        "expanded_projects": [7],
        "expanded_tasks": [31],
    })

    app.api_token = "another-account"
    app._project_cache = None
    app._task_cache = None
    assert control.snapshot(app)["work_preferences"] == {
        "configured": False,
        "known_projects": [],
        "expanded_projects": [],
        "expanded_tasks": [],
    }

    app.api_token = "tok"
    _seed_work()
    assert toggl_projects.context(app) == first_context
    assert control.snapshot(app)["work_preferences"]["expanded_tasks"] == ["31"]


def test_snapshot_skips_malformed_scope_records_and_keeps_a_valid_match(tmp_state_dir):
    _seed_work()
    context = toggl_projects.context(app)
    path = tmp_state_dir / toggl_ui_preferences.FILE_NAME
    path.write_text(json.dumps({"version": 1, "scopes": [0]}))
    assert control.snapshot(app)["work_preferences"] == {
        "configured": False,
        "known_projects": [],
        "expanded_projects": [],
        "expanded_tasks": [],
    }

    valid = {
        "context": context,
        "work_tree": {
            "known_projects": ["7"],
            "expanded_projects": ["7"],
            "expanded_tasks": ["31"],
        },
    }
    malformed_match = {
        "context": context,
        "work_tree": {"known_projects": ["7"], "expanded_projects": "7"},
    }
    path.write_text(json.dumps({
        "version": 1,
        "scopes": [valid, {"unrelated": True}, 0, malformed_match],
    }))
    assert control.snapshot(app)["work_preferences"] == {
        "configured": True,
        "known_projects": ["7"],
        "expanded_projects": ["7"],
        "expanded_tasks": ["31"],
    }


def test_incomplete_metadata_caches_preserve_saved_ids_but_reject_new_ids(tmp_state_dir):
    _seed_work()
    context = toggl_projects.context(app)
    original = {
        "context": context,
        "known_projects": [7],
        "expanded_projects": [7],
        "expanded_tasks": [31],
    }
    control.dispatch(app, "save-work-preferences", original)

    app._project_cache = {
        "account": app._project_account(),
        "fetched_at": 2,
        "projects": [],
    }
    app._task_cache = {
        "context": toggl_tasks.context(app),
        "fetched_at": 2,
        "tasks": [],
    }
    result = control.dispatch(app, "save-work-preferences", {
        "context": context,
        "known_projects": [7, 999],
        "expanded_projects": [7, 999],
        "expanded_tasks": [31, 999],
    })
    assert result["work_preferences"] == {
        "configured": True,
        "known_projects": ["7"],
        "expanded_projects": ["7"],
        "expanded_tasks": ["31"],
    }

    # Omitting a previously saved ID is still an explicit collapse/removal.
    result = control.dispatch(app, "save-work-preferences", {
        "context": context,
        "known_projects": [7],
        "expanded_projects": [],
        "expanded_tasks": [],
    })
    assert result["work_preferences"]["known_projects"] == ["7"]
    assert result["work_preferences"]["expanded_projects"] == []
    assert result["work_preferences"]["expanded_tasks"] == []


def test_preference_action_uses_the_timer_mutation_lock_and_one_scope():
    _seed_work()
    context = toggl_projects.context(app)
    payload = {
        "context": context,
        "known_projects": [7],
        "expanded_projects": [7],
        "expanded_tasks": [31],
    }

    class Guard:
        entered = False

        def __enter__(self):
            self.entered = True

        def __exit__(self, *_args):
            self.entered = False

    guard = Guard()

    def save(_app, received, *, context):
        assert guard.entered
        assert received == payload
        assert context == payload["context"]
        return {"configured": True}

    with patch.object(app, "toggle_lock", guard), patch.object(
        toggl_projects, "context", return_value=context
    ) as context_read, patch.object(
        toggl_ui_preferences, "write", side_effect=save
    ) as write:
        assert control.dispatch(app, "save-work-preferences", payload) == {
            "work_preferences": {"configured": True}
        }
    assert context_read.call_count == 1
    assert write.call_count == 1
    assert not guard.entered


def test_preference_action_rejects_stale_context_and_arbitrary_fields():
    _seed_work()
    context = toggl_projects.context(app)
    before = dict(app.state)
    with pytest.raises(control.ControlError, match="changed"):
        control.dispatch(app, "save-work-preferences", {
            "context": {**context, "workspace_id": 999},
            "known_projects": [7],
            "expanded_projects": [7],
            "expanded_tasks": [31],
        })
    with pytest.raises(control.ControlError, match="Unsupported"):
        control.dispatch(app, "save-work-preferences", {
            "context": context,
            "known_projects": [7],
            "expanded_projects": [7],
            "expanded_tasks": [31],
            "description": "must never become timer data",
        })
    with pytest.raises(control.ControlError, match="known projects"):
        control.dispatch(app, "save-work-preferences", {
            "context": context,
            "known_projects": [],
            "expanded_projects": [7],
            "expanded_tasks": [],
        })
    with pytest.raises(control.ControlError, match="Too many"):
        control.dispatch(app, "save-work-preferences", {
            "context": context,
            "known_projects": [7] * (toggl_ui_preferences.MAX_IDS + 1),
            "expanded_projects": [],
            "expanded_tasks": [],
        })
    assert app.state == before


def test_webview_can_only_write_preferences_through_the_fixed_socket():
    payload = {
        "context": {"account": "fixture", "workspace_id": 1},
        "known_projects": [7],
        "expanded_projects": [7],
        "expanded_tasks": [31],
    }
    expected = {"work_preferences": {"configured": True}}
    with patch("toggl_control.request", return_value=expected) as request:
        result = Bridge(Path("/private-test")).command("save-work-preferences", payload)
    assert result == expected
    request.assert_called_once_with(Path("/private-test"), "save-work-preferences", payload)


def test_theme_is_private_persistent_and_independent_of_timer_and_tree():
    before = dict(app.state)
    tree = toggl_ui_preferences.read(app)
    result = control.dispatch(app, "set-ui-theme", {"theme": "grok-night"})
    assert result == {"ui_theme": "grok-night"}
    assert toggl_ui_preferences.read_theme(app) == "grok-night"
    assert control.snapshot(app)["ui_theme"] == "grok-night"
    assert dict(app.state) == before
    assert toggl_ui_preferences.read(app) == tree
    assert (app.STATE_DIR / "appearance.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(control.ControlError):
        control.dispatch(app, "set-ui-theme", {"theme": "other"})
    assert toggl_ui_preferences.read_theme(app) == "grok-night"
    control.dispatch(app, "set-ui-theme", {"theme": "gruvbox"})
    assert toggl_ui_preferences.read_theme(app) == "gruvbox"


def test_theme_writes_do_not_take_the_timer_lock():
    with ThreadPoolExecutor(max_workers=1) as pool:
        # A blocked network start may own this lock; appearance must still finish.
        with app.toggle_lock:
            result = pool.submit(control.dispatch, app, "set-ui-theme", {"theme": "grok-night"}).result(timeout=2)
    assert result == {"ui_theme": "grok-night"}


def test_theme_recovers_backup_and_defaults_invalid_values():
    path = app.STATE_DIR / "appearance.json"
    assert toggl_ui_preferences.read_theme_from_dir(app.STATE_DIR) == "gruvbox"
    app._atomic_write_text(path, '{"theme":"grok-night"}')
    app._atomic_write_text(path, '{invalid')
    assert toggl_ui_preferences.read_theme_from_dir(app.STATE_DIR) == "grok-night"
    path.write_text('{"theme":"unknown"}')
    assert toggl_ui_preferences.read_theme_from_dir(app.STATE_DIR) == "gruvbox"
