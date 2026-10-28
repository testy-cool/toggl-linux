"""Pure task-window helpers enforce the visible task management contract."""
import pytest

import toggl_task_window as window


def test_task_name_is_trimmed_and_bounded():
    assert window.task_name("  Real task  ") == "Real task"
    for value in ("", "   ", None, "x" * 201):
        with pytest.raises(ValueError, match="1–200"):
            window.task_name(value)


def test_project_tasks_filters_workspace_project_and_archived():
    snapshot = {
        "state": {"workspace_id": 1},
        "tasks": [
            {"id": 1, "workspace_id": 1, "project_id": 7, "name": "Active", "active": True},
            {"id": 2, "workspace_id": 1, "project_id": 7, "name": "Archived", "active": False},
            {"id": 3, "workspace_id": 1, "project_id": 8, "name": "Other project", "active": True},
            {"id": 4, "workspace_id": 2, "project_id": 7, "name": "Other workspace", "active": True},
        ],
    }
    assert [item["id"] for item in window.project_tasks(snapshot, 7)] == [1]
    assert [item["id"] for item in window.project_tasks(snapshot, 7, True)] == [1, 2]
