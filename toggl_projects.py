"""Validated, immediate project writes shared by GTK and launcher integrations."""
import hashlib
import json
import re
from urllib.parse import quote, urlsplit


class ProjectError(ValueError):
    pass


def _api_base(app):
    getter = getattr(app, "get_api_base", None)
    base = getter() if callable(getter) else getattr(app, "API_BASE", "https://api.track.toggl.com/api/v9")
    return str(base).rstrip("/")


def context(app):
    identity = app._project_account()
    account = hashlib.sha256(json.dumps([_api_base(app), identity]).encode()).hexdigest() if identity else None
    with app.state_lock:
        workspace = app.state.get("workspace_id")
    return {"workspace_id": workspace, "account": account}


def projects_url(app):
    base = urlsplit(_api_base(app))
    if base.hostname in {"api.track.toggl.com", "track.toggl.com"}:
        return "https://track.toggl.com/projects"
    if base.scheme not in {"http", "https"} or not base.netloc:
        return None
    workspace = context(app)["workspace_id"]
    if not workspace:
        return None
    return f"{base.scheme}://{base.netloc}/projects/{quote(str(workspace), safe='')}/list"


def _fields(name, color):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        raise ProjectError("Project name must contain 1 to 200 characters")
    if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ProjectError("Choose a color in #RRGGBB format")
    return {"name": name.strip(), "color": color}


def _check_context(app, expected):
    current = context(app)
    if not isinstance(expected, dict) or expected != current:
        raise ProjectError("Account or workspace changed; refresh projects before saving")
    if not current["workspace_id"]:
        raise ProjectError("No workspace is available; connect your account and refresh projects")
    if not current["account"] or not app.api_token:
        raise ProjectError("Connect your account before saving projects")
    return current


def _target(app, selected, workspace):
    fields = ("id", "workspace_id", "name", "color")
    if not isinstance(selected, dict) or not all(k in selected for k in fields):
        raise ProjectError("Project changed; refresh projects before editing")
    project = next((p for p in app._cached_projects() if isinstance(p, dict)
                    and p.get("id") is not None
                    and str(p.get("workspace_id")) == str(workspace)
                    and str(p.get("id")) == str(selected.get("id"))
                    and str(p.get("workspace_id")) == str(selected.get("workspace_id"))
                    and all(p.get(k) == selected.get(k) for k in ("name", "color"))), None)
    if project is None:
        raise ProjectError("Project changed; refresh projects before editing")
    return project


def _saved_project(response, expected, values, original):
    # Deployed OpenTickly and Toggl return flat objects; tolerate data envelopes.
    data = response.get("data", response) if isinstance(response, dict) else None
    if not isinstance(data, dict) or not data.get("id"):
        raise ProjectError("Project may have been saved, but the response was invalid; refresh projects before trying again")
    workspace = data.get("workspace_id", data.get("wid", expected["workspace_id"]))
    if (str(workspace) != str(expected["workspace_id"])
            or (original and str(data["id"]) != str(original["id"]))):
        raise ProjectError("Project was saved, but its response did not match this workspace; refresh projects")
    try:
        saved = _fields(data.get("name", values["name"]), data.get("color", values["color"]))
    except ProjectError as error:
        raise ProjectError("Project may have been saved, but the response was invalid; refresh projects before trying again") from error
    return {**(original or {}), "id": data["id"], "workspace_id": workspace, **saved,
            "active": data.get("active", original.get("active", True) if original else True)}


def _cache_saved_project(app, saved, expected):
    with app._project_cache_lock:
        if context(app) != expected:
            raise ProjectError("Project was saved, but account or workspace changed; refresh projects before continuing")
        existing = [dict(p) for p in app._cached_projects() if isinstance(p, dict)]
        replaced = False
        for idx, project in enumerate(existing):
            if (str(project.get("id")) == str(saved["id"])
                    and str(project.get("workspace_id")) == str(saved["workspace_id"])):
                existing[idx] = saved
                replaced = True
                break
        if not replaced:
            existing.append(saved)
        previous = app._project_cache if isinstance(app._project_cache, dict) else {}
        cache = {"account": app._project_account(), "projects": existing,
                 "fetched_at": previous.get("fetched_at", 0)}
        # This write updates one item; do not postpone a full-list refresh.
        app._project_cache = cache
        app._last_tray_icon_path = None
        try:
            app._atomic_write_text(app.STATE_DIR / "projects.json", json.dumps(cache, indent=2))
        except OSError as error:
            raise ProjectError("Project was saved, but the local cache could not be saved; refresh projects") from error


def write(app, action, payload):
    values = _fields(payload.get("name"), payload.get("color"))
    expected = _check_context(app, payload.get("context"))
    original = _target(app, payload.get("project"), expected["workspace_id"]) if action == "project-update" else None
    if app._rate_limit_active() or app._request_budget_exhausted():
        raise ProjectError("Project changes are unavailable while requests are limited; wait and refresh projects")
    path = f"/workspaces/{quote(str(expected['workspace_id']), safe='')}/projects"
    if original:
        path += "/" + quote(str(original["id"]), safe="")
    try:
        response = app.api_put(path, values) if original else app.api_post(path, dict(values, active=True))
    except Exception as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status in (401, 403):
            raise ProjectError("Project access denied; check your account and workspace permissions") from error
        if status == 429 or isinstance(error, app.RateLimitedError):
            raise ProjectError("Project changes were rate limited; wait and refresh projects") from error
        raise ProjectError("Project request failed and may have been saved; refresh projects before trying again") from error
    saved = _saved_project(response, expected, values, original)
    _cache_saved_project(app, saved, expected)
    return saved
