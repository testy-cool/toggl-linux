"""Private, account-scoped preferences for the work panel's browsing state."""
import json
import threading

import toggl_projects


FILE_NAME = "ui-preferences.json"
MAX_SCOPES = 20
MAX_IDS = 1000
_lock = threading.Lock()


def _path(app):
    return app.STATE_DIR / FILE_NAME


def _empty(configured=False):
    return {
        "configured": configured,
        "expanded_projects": [],
        "expanded_tasks": [],
    }


def _scope_key(context):
    if not isinstance(context, dict):
        return None
    account = context.get("account")
    workspace = context.get("workspace_id")
    if account is not None and not isinstance(account, str):
        return None
    if workspace is not None and not isinstance(workspace, (str, int)):
        return None
    return account, str(workspace) if workspace is not None else None


def _read_file(app):
    data = app._load_json_with_backup(_path(app), {})
    if not isinstance(data, dict) or data.get("version") != 1:
        return []
    scopes = data.get("scopes")
    return scopes if isinstance(scopes, list) else []


def _stored_preferences(record):
    if not isinstance(record, dict):
        return None
    work_tree = record.get("work_tree")
    if not isinstance(work_tree, dict):
        return None
    projects = work_tree.get("expanded_projects")
    tasks = work_tree.get("expanded_tasks")
    if not isinstance(projects, list) or not isinstance(tasks, list):
        return None
    if len(projects) > MAX_IDS or len(tasks) > MAX_IDS:
        return None
    if any(value is not None and (isinstance(value, bool) or not isinstance(value, (str, int)))
           for value in projects):
        return None
    if any(isinstance(value, bool) or not isinstance(value, (str, int)) for value in tasks):
        return None
    return {
        "configured": True,
        "expanded_projects": projects,
        "expanded_tasks": tasks,
    }


def read(app):
    """Return only the current account/workspace's expansion choices."""
    wanted = _scope_key(toggl_projects.context(app))
    if wanted is None:
        return _empty()
    with _lock:
        for record in reversed(_read_file(app)):
            if _scope_key(record.get("context")) == wanted:
                return _stored_preferences(record) or _empty()
    return _empty()


def _validated_ids(app, payload):
    projects = payload.get("expanded_projects")
    tasks = payload.get("expanded_tasks")
    if not isinstance(projects, list) or not isinstance(tasks, list):
        raise ValueError("Expanded projects and tasks must be lists")
    if len(projects) > MAX_IDS or len(tasks) > MAX_IDS:
        raise ValueError("Too many work-panel expansion choices")
    if any(value is not None and (isinstance(value, bool) or not isinstance(value, (str, int)))
           for value in projects):
        raise ValueError("Expanded project IDs are invalid")
    if any(isinstance(value, bool) or not isinstance(value, (str, int)) for value in tasks):
        raise ValueError("Expanded task IDs are invalid")

    context = toggl_projects.context(app)
    workspace = context.get("workspace_id")
    allowed_projects = {
        str(project.get("id"))
        for project in app._cached_projects()
        if isinstance(project, dict)
        and str(project.get("workspace_id")) == str(workspace)
        and project.get("id") is not None
    }
    allowed_tasks = {
        str(task.get("id"))
        for task in app._cached_tasks()
        if isinstance(task, dict)
        and str(task.get("workspace_id")) == str(workspace)
        and task.get("id") is not None
    }

    normalized_projects = []
    seen_projects = set()
    for value in projects:
        key = "none" if value is None else str(value)
        if key != "none" and key not in allowed_projects:
            continue
        if key in seen_projects:
            continue
        seen_projects.add(key)
        normalized_projects.append(None if key == "none" else key)

    normalized_tasks = []
    seen_tasks = set()
    for value in tasks:
        key = str(value)
        if key not in allowed_tasks or key in seen_tasks:
            continue
        seen_tasks.add(key)
        normalized_tasks.append(key)
    return normalized_projects, normalized_tasks


def write(app, payload):
    """Replace one scope's allowlisted expansion state without touching timer data."""
    context = toggl_projects.context(app)
    if not isinstance(payload, dict) or payload.get("context") != context:
        raise ValueError("Account or workspace changed; reopen the work panel")
    if set(payload) - {"context", "expanded_projects", "expanded_tasks"}:
        raise ValueError("Unsupported work-panel preference")
    projects, tasks = _validated_ids(app, payload)
    record = {
        "context": context,
        "work_tree": {
            "expanded_projects": projects,
            "expanded_tasks": tasks,
        },
    }
    wanted = _scope_key(context)
    with _lock:
        scopes = [
            existing
            for existing in _read_file(app)
            if _scope_key(existing.get("context") if isinstance(existing, dict) else None) != wanted
        ]
        scopes.append(record)
        app._atomic_write_text(
            _path(app),
            json.dumps({"version": 1, "scopes": scopes[-MAX_SCOPES:]}, indent=2),
        )
    return {
        "configured": True,
        "expanded_projects": projects,
        "expanded_tasks": tasks,
    }
