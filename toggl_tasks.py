"""OpenTickly/Toggl task metadata, capability detection, caching and writes."""
import json
import time
from urllib.parse import quote

import toggl_projects


class TaskError(ValueError):
    pass


def context(app):
    return toggl_projects.context(app)


def _task_fields(task, expected=None):
    if not isinstance(task, dict) or task.get("id") is None:
        raise TaskError("Task response was invalid; refresh tasks before trying again")
    name = task.get("name")
    if not isinstance(name, str) or not name.strip():
        raise TaskError("Task response was invalid; refresh tasks before trying again")
    workspace = task.get("workspace_id", task.get("wid", expected and expected.get("workspace_id")))
    project = task.get("project_id", task.get("pid"))
    if workspace is None or project is None:
        raise TaskError("Task response was invalid; refresh tasks before trying again")
    return {
        "id": task["id"],
        "workspace_id": workspace,
        "project_id": project,
        "name": name.strip(),
        "active": task.get("active", True),
    }


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise TaskError("Task name must contain 1 to 200 characters")
    return value.strip()


def cached(app):
    if app._task_cache is None:
        app._task_cache = app._load_json_with_backup(app.STATE_DIR / "tasks.json", {})
    expected = context(app)
    if not isinstance(app._task_cache, dict) or app._task_cache.get("context") != expected:
        return []
    tasks = app._task_cache.get("tasks", [])
    return tasks if isinstance(tasks, list) else []


def capability(app):
    if getattr(app, "_task_capability", "unknown") == "unknown" and cached(app):
        return "supported"
    return getattr(app, "_task_capability", "unknown")


def _save_cache(app, tasks, scope, fetched_at=None):
    cache = {"context": scope, "fetched_at": fetched_at or time.time(), "tasks": tasks}
    app._task_cache = cache
    app._atomic_write_text(app.STATE_DIR / "tasks.json", json.dumps(cache, indent=2))


def refresh(app, force=False):
    """Refresh up to 1,000 tasks. Unsupported accounts retain activity history."""
    if (not app.api_token or not context(app).get("workspace_id") or app._rate_limit_active()
            or app._request_budget_remaining() <= app.REQUEST_BUDGET_BACKGROUND_RESERVE):
        return False
    if not app._task_cache_lock.acquire(blocking=False):
        return False
    try:
        scope = context(app)
        token, base = app.api_token, toggl_projects._api_base(app)
        cached(app)
        now = time.time()
        previous = app._task_cache if isinstance(app._task_cache, dict) else {}
        if (not force and previous.get("context") == scope
                and 0 <= now - previous.get("fetched_at", 0) < app.CLOUD_POLL_INTERVAL_SECONDS):
            return False
        tasks = []
        seen = set()
        workspace = quote(str(scope["workspace_id"]), safe="")
        # OpenTickly's current source accepts active=both, but older deployed releases
        # generated a strict boolean parser. Two boolean requests work on both versions.
        for active in ("true", "false"):
            fetched_for_filter = 0
            for page in range(1, 6):
                response = app._api("GET", f"/workspaces/{workspace}/tasks/basic", token=token,
                                    base_url=base, params={"active": active, "page": page, "per_page": 200})
                data = response.get("data", []) if isinstance(response, dict) else response
                if not isinstance(data, list):
                    raise TaskError("Task list response was invalid")
                fetched_for_filter += len(data)
                for item in data:
                    saved = _task_fields(item, scope) if isinstance(item, dict) else None
                    identity = str(saved.get("id")) if saved else None
                    if saved and identity not in seen:
                        tasks.append(saved)
                        seen.add(identity)
                total = response.get("total_count") if isinstance(response, dict) else None
                if (len(tasks) >= 1000 or len(data) < 200
                        or (isinstance(total, int) and fetched_for_filter >= total)
                        or app._request_budget_remaining() <= app.REQUEST_BUDGET_BACKGROUND_RESERVE):
                    break
            if len(tasks) >= 1000 or app._request_budget_remaining() <= app.REQUEST_BUDGET_BACKGROUND_RESERVE:
                break
        if context(app) != scope:
            return False
        _save_cache(app, tasks[:1000], scope, now)
        app._task_capability = "supported"
        return True
    except Exception as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status in (403, 404):
            app._task_capability = "unavailable"
        print(f"Tasks unavailable: {error}", file=app.sys.stderr)
        return False
    finally:
        app._task_cache_lock.release()


def _check_context(app, expected):
    current = context(app)
    if not isinstance(expected, dict) or expected != current:
        raise TaskError("Account or workspace changed; refresh tasks and reopen the editor")
    if not app.api_token or not current.get("workspace_id"):
        raise TaskError("Connect your account and refresh projects before saving tasks")
    return current


def _target(app, selected, scope):
    if not isinstance(selected, dict):
        raise TaskError("Task changed; refresh tasks and reopen the editor")
    task = next((item for item in cached(app)
                 if all(str(item.get(key)) == str(selected.get(key)) for key in
                        ("id", "workspace_id", "project_id"))
                 and item.get("name") == selected.get("name")
                 and item.get("active", True) == selected.get("active", True)), None)
    if task is None or str(task.get("workspace_id")) != str(scope["workspace_id"]):
        raise TaskError("Task changed; refresh tasks and reopen the editor")
    return task


def write(app, action, payload):
    if capability(app) == "unavailable":
        raise TaskError("Tasks are not supported by this server; use recent activities instead")
    scope = _check_context(app, payload.get("context"))
    original = _target(app, payload.get("task"), scope) if action == "task-update" else None
    name = _name(payload.get("name", original and original.get("name")))
    project_id = original.get("project_id") if original else payload.get("project_id")
    project = next((item for item in app._cached_projects()
                    if str(item.get("id")) == str(project_id)
                    and str(item.get("workspace_id")) == str(scope["workspace_id"])
                    and item.get("active") is not False), None)
    if not project:
        raise TaskError("Choose an active project before saving the task")
    duplicate = next((item for item in cached(app)
                      if str(item.get("id")) != str(original and original.get("id"))
                      and item.get("name", "").casefold() == name.casefold()), None)
    if duplicate:
        raise TaskError("A task with this name already exists in the workspace")
    active = payload.get("active", original.get("active", True) if original else True)
    if not isinstance(active, bool):
        raise TaskError("Task active state must be true or false")
    if app._rate_limit_active() or app._request_budget_exhausted():
        raise TaskError("Task changes are unavailable while requests are limited")
    workspace = quote(str(scope["workspace_id"]), safe="")
    project_path = quote(str(project_id), safe="")
    path = f"/workspaces/{workspace}/projects/{project_path}/tasks"
    if original:
        path += "/" + quote(str(original["id"]), safe="")
    values = {"name": name, "active": active}
    try:
        response = (app.api_put(path, values, token=app.api_token, base_url=toggl_projects._api_base(app))
                    if original else app.api_post(path, values, token=app.api_token,
                                                  base_url=toggl_projects._api_base(app)))
    except Exception as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status in (401, 403):
            raise TaskError("Task access denied; check your workspace permissions") from error
        if status == 429 or isinstance(error, app.RateLimitedError):
            raise TaskError("Task changes were rate limited; wait and refresh") from error
        raise TaskError("Task request failed and may have been saved; refresh tasks") from error
    data = response.get("data", response) if isinstance(response, dict) else None
    saved = _task_fields(data, scope)
    if original and str(saved["id"]) != str(original["id"]):
        raise TaskError("Task was saved, but its response did not match; refresh tasks")
    with app._task_cache_lock:
        if context(app) != scope:
            raise TaskError("Task was saved, but the account changed; refresh tasks")
        tasks = [dict(item) for item in cached(app)]
        tasks = [saved if str(item.get("id")) == str(saved["id"]) else item for item in tasks]
        if not any(str(item.get("id")) == str(saved["id"]) for item in tasks):
            tasks.append(saved)
        try:
            _save_cache(app, tasks, scope)
        except OSError as error:
            raise TaskError("Task was saved, but its local cache could not be saved") from error
    app._task_capability = "supported"
    return saved
