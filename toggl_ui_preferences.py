"""Private appearance and account-scoped browsing preferences for the work panel."""
import json
from pathlib import Path
import threading

import toggl_projects


FILE_NAME = "ui-preferences.json"
MAX_SCOPES = 20
MAX_IDS = 1000
_lock = threading.RLock()


def read_theme_from_dir(state_dir):
    path = Path(state_dir) / "appearance.json"
    for candidate in (path, path.with_suffix(path.suffix + ".bak")):
        try:
            data = json.loads(candidate.read_text())
        except (UnicodeDecodeError, ValueError, OSError):
            continue
        theme = data.get("theme") if isinstance(data, dict) else None
        return theme if theme in ("gruvbox", "grok-night") else "gruvbox"
    return "gruvbox"


def read_theme(app):
    return read_theme_from_dir(app.STATE_DIR)


def write_theme(app, payload):
    if not isinstance(payload, dict) or set(payload) != {"theme"} or payload["theme"] not in ("gruvbox", "grok-night"):
        raise ValueError("Choose Gruvbox or GrokNight")
    with _lock:
        app._atomic_write_text(app.STATE_DIR / "appearance.json", json.dumps(payload))
    return payload["theme"]


def _path(app):
    return app.STATE_DIR / FILE_NAME


def _empty(configured=False):
    return {
        "configured": configured,
        "known_projects": [],
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
    known_projects = work_tree.get("known_projects")
    projects = work_tree.get("expanded_projects")
    tasks = work_tree.get("expanded_tasks")
    if not all(isinstance(values, list) for values in (known_projects, projects, tasks)):
        return None
    if any(len(values) > MAX_IDS for values in (known_projects, projects, tasks)):
        return None
    if any(value is not None and (isinstance(value, bool) or not isinstance(value, (str, int)))
           for values in (known_projects, projects) for value in values):
        return None
    if any(isinstance(value, bool) or not isinstance(value, (str, int)) for value in tasks):
        return None
    known_keys = {"none" if value is None else str(value) for value in known_projects}
    if any(("none" if value is None else str(value)) not in known_keys for value in projects):
        return None
    return {
        "configured": True,
        "known_projects": known_projects,
        "expanded_projects": projects,
        "expanded_tasks": tasks,
    }


def _read_context(app, context):
    wanted = _scope_key(context)
    if wanted is None:
        return _empty()
    for record in reversed(_read_file(app)):
        if not isinstance(record, dict) or _scope_key(record.get("context")) != wanted:
            continue
        stored = _stored_preferences(record)
        if stored is not None:
            return stored
    return _empty()


def read(app):
    """Return only the current account/workspace's expansion choices."""
    context = toggl_projects.context(app)
    with _lock:
        return _read_context(app, context)


def _validated_ids(app, payload, context, previous):
    known_projects = payload.get("known_projects")
    projects = payload.get("expanded_projects")
    tasks = payload.get("expanded_tasks")
    if not all(isinstance(values, list) for values in (known_projects, projects, tasks)):
        raise ValueError("Known projects, expanded projects, and expanded tasks must be lists")
    if any(len(values) > MAX_IDS for values in (known_projects, projects, tasks)):
        raise ValueError("Too many work-panel expansion choices")
    if any(value is not None and (isinstance(value, bool) or not isinstance(value, (str, int)))
           for values in (known_projects, projects) for value in values):
        raise ValueError("Project expansion IDs are invalid")
    if any(isinstance(value, bool) or not isinstance(value, (str, int)) for value in tasks):
        raise ValueError("Expanded task IDs are invalid")

    workspace = context.get("workspace_id")
    allowed_projects = {
        str(project.get("id"))
        for project in app._cached_projects()
        if isinstance(project, dict)
        and str(project.get("workspace_id")) == str(workspace)
        and project.get("id") is not None
    }
    # A partial or stale metadata cache must not turn a previously validated
    # choice into a collapse. IDs not already saved still need current metadata.
    allowed_projects.update(
        str(value)
        for value in previous["known_projects"] + previous["expanded_projects"]
        if value is not None
    )
    allowed_tasks = {
        str(task.get("id"))
        for task in app._cached_tasks()
        if isinstance(task, dict)
        and str(task.get("workspace_id")) == str(workspace)
        and task.get("id") is not None
    }
    allowed_tasks.update(str(value) for value in previous["expanded_tasks"])

    def normalized_project_ids(values):
        normalized = []
        seen = set()
        for value in values:
            key = "none" if value is None else str(value)
            if key != "none" and key not in allowed_projects:
                continue
            if key in seen:
                continue
            seen.add(key)
            normalized.append(None if key == "none" else key)
        return normalized

    normalized_known = normalized_project_ids(known_projects)
    normalized_projects = normalized_project_ids(projects)
    known_keys = {"none" if value is None else str(value) for value in normalized_known}
    if any(("none" if value is None else str(value)) not in known_keys
           for value in normalized_projects):
        raise ValueError("Expanded projects must also be known projects")

    normalized_tasks = []
    seen_tasks = set()
    for value in tasks:
        key = str(value)
        if key not in allowed_tasks or key in seen_tasks:
            continue
        seen_tasks.add(key)
        normalized_tasks.append(key)
    return normalized_known, normalized_projects, normalized_tasks


def write(app, payload, *, context=None):
    """Replace one scope's allowlisted expansion state without touching timer data."""
    context = context if context is not None else toggl_projects.context(app)
    if not isinstance(payload, dict) or payload.get("context") != context:
        raise ValueError("Account or workspace changed; reopen the work panel")
    if set(payload) - {"context", "known_projects", "expanded_projects", "expanded_tasks"}:
        raise ValueError("Unsupported work-panel preference")
    wanted = _scope_key(context)
    with _lock:
        previous = _read_context(app, context)
        known_projects, projects, tasks = _validated_ids(app, payload, context, previous)
        record = {
            "context": context,
            "work_tree": {
                "known_projects": known_projects,
                "expanded_projects": projects,
                "expanded_tasks": tasks,
            },
        }
        scopes = [
            existing
            for existing in _read_file(app)
            if isinstance(existing, dict)
            and _scope_key(existing.get("context")) is not None
            and _stored_preferences(existing) is not None
            and _scope_key(existing.get("context")) != wanted
        ]
        scopes.append(record)
        app._atomic_write_text(
            _path(app),
            json.dumps({"version": 1, "scopes": scopes[-MAX_SCOPES:]}, indent=2),
        )
    return {
        "configured": True,
        "known_projects": known_projects,
        "expanded_projects": projects,
        "expanded_tasks": tasks,
    }
