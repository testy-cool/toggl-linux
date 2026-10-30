"""Private local control socket shared by the tray and launcher integrations."""
import json
import os
import socket
import socketserver
import threading
import time
import toggl_projects
import toggl_task_tree
import toggl_tasks
import toggl_debug
import toggl_today
from pathlib import Path

MAX_MESSAGE = 2 * 1024 * 1024
MAX_COMMAND = 256 * 1024  # A 10,000-character Unicode description plus its guarded original.
PENDING_FIELDS = ('start', 'stop', 'description', 'project_id', 'task_id', 'workspace_id',
                  '_pending_kind', '_remote_entry_id')


def _json_bytes(value):
    # Keep ordinary Unicode compact while escaping legacy unpaired surrogates.
    return json.dumps(value, ensure_ascii=False).encode('utf-8', errors='backslashreplace')


class ControlError(Exception):
    pass


def _cached_entries(app, context):
    legacy = toggl_today.legacy_entries(app, context)
    scoped = toggl_today.cached(app) or []
    return list({(str(e.get('workspace_id')), str(e.get('id'))): e for e in [*legacy, *scoped]}.values())


def snapshot(app):
    with app.pending_lock:
        all_pending = app._pending_as_entries()
        with app.state_lock:
            current = dict(app.state)
    today = app.datetime.now().date()
    context = toggl_projects.context(app)
    pending = [entry for entry in all_pending if (entry.get('_task_context') is None
               or isinstance(entry['_task_context'], dict) and entry['_task_context'].get('account') == context['account'])
               and toggl_today.overlaps(app, entry, current)]
    current_pending = [entry for entry in all_pending if current.get('tracking') and current.get('entry_id') is None
                       and entry.get('_pending_kind') == 'open' and entry.get('start') == current.get('start_time')
                       and str(entry.get('workspace_id')) == str(current.get('workspace_id'))]
    cached = _cached_entries(app, context)
    entries = app._merge_entries_with_pending(cached, pending)
    stale = [e for e in entries if not e.get("_offline") and e.get("duration", 0) < 0
             and (not current.get("tracking") or e.get("id") != current.get("entry_id"))]
    entries = [e for e in entries if e not in stale]
    entries = [dict(entry, _activity_key=toggl_today.activity_key(
        entry.get('project_id'), entry.get('description'))) for entry in entries]
    warnings = [getattr(app, "_control_warning", ""), getattr(app, "status_message", ""),
                "Older pending entries need full review; saving applies all displayed fields" if any(
                    item.get('_verify_running') and not item.get('_verify_snapshot') for item in app._load_pending()) else "",
                "Pending entry needs review in Today's Entries; edit it or discard local changes" if any(
                    item.get('_review_error') for item in app._load_pending()) else "",
                "Today's entries changed; refresh to update durations" if stale else ""]
    warning = " · ".join(dict.fromkeys(message for message in warnings if message))
    freshness = toggl_today.status(app)
    freshness['today_incomplete'] = freshness['today_incomplete'] or bool(stale)
    result = {"state": current, "projects": app._cached_projects(), "tasks": app._cached_tasks(),
            "task_capability": toggl_tasks.capability(app), "entries": entries,
            "pending_count": len(app._load_pending()), "warning": warning,
            "project_context": toggl_projects.context(app), "projects_url": toggl_projects.projects_url(app),
            "current_pending": current_pending[0] if len(current_pending) == 1 else None,
            "current_activity_key": toggl_today.activity_key(current.get('project_id'), current.get('description')),
            **freshness}
    # Keep whole immutable entry selectors; a truncated description could target
    # or overwrite a different interval. The cache still retains every record.
    result['entries'] = []
    budget = MAX_MESSAGE - len(_json_bytes({'ok': True, 'result': result})) - 65536
    for entry in entries:
        size = len(_json_bytes(entry)) + 2
        if size <= budget:
            result['entries'].append(entry)
            budget -= size
    omitted = len(entries) - len(result['entries'])
    if omitted:
        result['today_incomplete'] = True
        result['warning'] = ' · '.join(filter(None, [result['warning'],
            f"{omitted} daily entries exceed the response limit; today's totals are partial"]))
    return result


def _same_pending(source, selected):
    if not isinstance(source, dict) or not isinstance(selected, dict):
        return False
    ids = selected.get('_pending_ids')
    source_ids = source.get('_pending_ids')
    return (isinstance(ids, list) and bool(ids) and all(isinstance(value, str) for value in ids)
            and isinstance(source_ids, list) and len(ids) == len(source_ids) == len(set(ids))
            and set(ids) == set(source_ids)
            and all(selected.get(key) == source.get(key) for key in PENDING_FIELDS))


def _update_resolution_entries(records, entry, context):
    for record in records:
        if (record['context'] == context and str(record['entry'].get('id')) == str(entry['id'])
                and str(record['entry'].get('workspace_id')) == str(entry['workspace_id'])):
            record['entry'] = entry


def record_pending_resolution(app, queue, op, remote):
    """Keep the identity of a successfully synced interval for open UI drafts."""
    if not isinstance(remote, dict) or remote.get('id') is None:
        return
    app._ensure_pending_ids(queue)
    source = next((entry for entry in app._pending_entries_from_queue(queue)
                   if set(entry['_pending_indexes']) == set(op['indexes'])), None)
    context = toggl_projects.context(app)
    if (not source or str(source.get('workspace_id')) != str(context['workspace_id'])
            or source.get('_task_context') not in (None, context)):
        return
    entry = {key: remote.get(key, source.get(key)) for key in
             ('id', 'workspace_id', 'description', 'project_id', 'task_id', 'start', 'stop', 'duration')}
    if str(entry.get('workspace_id')) != str(source.get('workspace_id')):
        return
    if op['kind'] != 'open' and not entry.get('stop'):
        return  # A handoff must describe the confirmed completed interval.
    sources = [source]
    if op['kind'] == 'create' and len(op['indexes']) == 2:
        first = queue[op['indexes'][0]]
        if first.get('action') == 'start':
            sources += app._pending_entries_from_queue([first])
    records = getattr(app, '_pending_resolutions', [])
    _update_resolution_entries(records, entry, context)
    app._pending_resolutions = (records + [dict(source=dict(source), entry=entry, context=context)
                                         for source in sources])[-1024:]
    toggl_today.remember(app, entry, scope=context)


def _pending_resolution(app, selected):
    with app.pending_lock:
        context = toggl_projects.context(app)
        matches = [item for item in getattr(app, '_pending_resolutions', [])
                   if item['context'] == context and _same_pending(item['source'], selected)]
        return matches[-1] if matches else None


def _known_entry(app, selected):
    context = toggl_projects.context(app)
    cached = _cached_entries(app, context)
    resolved = [record['entry'] for record in getattr(app, '_pending_resolutions', []) if record['context'] == context]
    entry = next((entry for entry in [*cached, *reversed(resolved)]
                 if str(entry.get('id')) == str(selected.get('id'))
                 and str(entry.get('workspace_id')) == str(selected.get('workspace_id'))), None)
    if entry and selected.get('stop') and not entry.get('stop'):
        return None  # A completed form must never be applied to a stale running record.
    return entry


def _confirmed_current_draft(app, payload, expected, current):
    selected = payload.get('draft_entry')
    if not isinstance(expected, dict) or not isinstance(selected, dict):
        return False
    resolved = _pending_resolution(app, selected)
    if not resolved or expected.get('entry_id') is not None or not expected.get('tracking'):
        return False
    entry = resolved['entry']
    for timer, target in ((expected, selected), (current, entry)):
        if (timer.get('start_time') != target.get('start')
                or (timer.get('description') or '') != (target.get('description') or '')
                or any(str(timer.get(key)) != str(target.get(key)) for key in ('project_id','task_id','workspace_id'))):
            return False
    return (current.get('tracking') is True and not entry.get('stop')
            and str(current.get('entry_id')) == str(entry.get('id')))


def _attach_resolution(app, payload, result):
    selected = payload.get('draft_entry')
    if selected and payload.get('draft_context') == result['project_context']:
        resolved = _pending_resolution(app, selected)
        if resolved is not None and resolved['context'] == result['project_context']:
            result['draft_resolution'] = dict(source=selected, entry=resolved['entry'], context=resolved['context'])
    return result


def _work_snapshot(app, payload):
    result = _attach_resolution(app, payload, snapshot(app))
    # The legacy tree aliases activities as tasks. The web protocol needs one copy.
    history = toggl_task_tree.tree(app)
    groups = [{k: v for k, v in group.items() if k not in {'tasks', 'activities'}} for group in history]
    for group in groups:
        group['activities'] = []
    result.update(activities=groups, activities_omitted=0)
    budget = MAX_MESSAGE - len(_json_bytes({'ok': True, 'result': result})) - 1024
    candidates = sorted(((activity, index) for index, group in enumerate(history)
                         for activity in group['activities']), key=lambda item: -item[0]['last_used'])
    for activity, index in candidates:
        activity = dict(activity, key=toggl_today.activity_key(groups[index]['id'], activity['description']))
        size = len(_json_bytes(activity)) + 2
        if size <= budget:
            groups[index]['activities'].append(activity)
            budget -= size
        else:
            result['activities_omitted'] += 1
    if result['activities_omitted']:
        result['warning'] = ' · '.join(filter(None, [result['warning'],
            f"{result['activities_omitted']} older recent activities exceed the local response limit"]))
    return result


def _changes(app, values, context=None):
    context = app.state if context is None else context
    if not isinstance(values, dict) or set(values) - {"description", "project_id", "task_id", "start"}:
        raise ControlError("Only description, project, task, and current start time may be edited")
    if "description" in values and (not isinstance(values["description"], str) or len(values["description"]) > 10000):
        raise ControlError("Description must be text of at most 10000 characters")
    result = dict(values)
    if "start" in result:
        try:
            dt = app._parse_iso(result["start"]) if isinstance(result["start"], str) else None
        except ValueError:
            dt = None
        if dt is None or dt.tzinfo is None or dt > app.datetime.now(app.timezone.utc):
            raise ControlError("Start time must be a valid timestamp in the past")
    if result.get("project_id") is not None:
        if str(result["project_id"]) == str(context.get("project_id")):
            result["project_id"] = context["project_id"]
        else:
            project = next((p for p in app._cached_projects() if str(p.get("id")) == str(result["project_id"])
                            and str(p.get("workspace_id")) == str(context.get("workspace_id"))), None)
            if not project:
                raise ControlError("Project is unavailable; refresh the project list")
            result["project_id"] = project["id"]
    target_project = result.get("project_id", context.get("project_id"))
    if result.get("task_id") is not None:
        task = next((t for t in app._cached_tasks() if str(t.get("id")) == str(result["task_id"])
                    and str(t.get("workspace_id")) == str(context.get("workspace_id"))
                    and t.get("active") is not False), None)
        if not task:
            raise ControlError("Task is unavailable; refresh the task list")
        result["task_id"] = task["id"]
        result["project_id"] = task["project_id"]
    elif "task_id" in result:
        result["task_id"] = None
    elif context.get("task_id") is not None and str(target_project) != str(context.get("project_id")):
        result["task_id"] = None
    return result


def _edit_current(app, changes, expected=None):
    with app.state_lock:
        current = dict(app.state)
        if expected is not None and any(current.get(k) != expected.get(k) for k in
                                       ("tracking", "entry_id", "start_time", "description", "project_id", "task_id", "workspace_id")):
            raise ControlError("Timer changed; refresh before editing")
    if "start" in changes and not current.get("tracking"):
        raise ControlError("Start time can only be changed while tracking")
    remote_changes = dict(changes)
    local_changes = dict(changes)
    if "start" in changes:
        remote_changes["duration"] = -int(app._parse_iso(changes["start"]).timestamp())
        local_changes["start_time"] = local_changes.pop("start")
    if current.get("tracking") and current.get("entry_id"):
        if not app.api_token or app._rate_limit_active() or app._request_budget_exhausted():
            raise ControlError("Cloud entry cannot be edited right now; retry when connected")
        app.update_entry(current["workspace_id"], current["entry_id"], remote_changes)
    if current.get("tracking") and not current.get("entry_id"):
        with app.pending_lock:
            queue = app._load_pending()
            candidates = [op for op in app._pending_operations(queue) if op["kind"] == "open"
                          and op.get("start_time") == current.get("start_time")]
            if not candidates:
                raise ControlError("Pending timer changed; refresh before editing")
            for idx in candidates[-1]["indexes"]:
                queue[idx].update(local_changes)
            app._save_pending(queue)
    with app.state_lock:
        if any(app.state.get(k) != current.get(k) for k in ("tracking", "entry_id", "start_time")):
            raise ControlError("Timer changed while saving; refresh before editing again")
        app.state.update(local_changes)
        app.save_state()


def _entry_changes(app, values, entry):
    if not isinstance(values, dict) or set(values) - {'description', 'project_id', 'task_id', 'start', 'stop'}:
        raise ControlError('Only description, project, task, start and stop may be edited')
    changes = _changes(app, {k: v for k, v in values.items() if k not in {'start', 'stop'}}, entry)
    if not {'start', 'stop'} & values.keys():
        return changes
    running = entry.get('duration', 0) < 0
    if running and 'stop' in values:
        raise ControlError('Stop the running timer before editing its end time')
    try:
        start = app._parse_iso(values.get('start', entry.get('start')))
        stop = app.datetime.now(app.timezone.utc) if running else app._parse_iso(values.get('stop', entry.get('stop')))
        now = app.datetime.now(app.timezone.utc)
        if not start or not stop or not start.tzinfo or not stop.tzinfo or start > stop or stop > now:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ControlError('Use valid start and stop times in order, no later than now') from None
    changes.update({key: values[key] for key in ('start', 'stop') if key in values})
    changes['duration'] = -int(start.timestamp()) if running else int((stop-start).total_seconds())
    return changes


def _edit_entry(app, selected, changes, reviewed_interval=False):
    if not isinstance(selected, dict):
        raise ControlError("Entry is required")
    if selected.get("_offline"):
        with app.toggle_lock, app.pending_lock:
            queue = app._load_pending()
            entry = app._pending_entry_candidate(queue, selected)
            if not entry:
                resolved = _pending_resolution(app, selected)
                if resolved is not None:
                    return _edit_entry(app, resolved['entry'], changes, reviewed_interval)
                raise ControlError("Pending entry changed; refresh before editing")
            if entry.get('_needs_full_review') and reviewed_interval is not True:
                raise ControlError('Review and approve all displayed interval fields before saving')
            changes = _entry_changes(app, changes, entry)
            if not app._update_pending_entry(entry, changes):
                raise ControlError('Pending entry changed; refresh before editing')
        toggl_task_tree.correct(app, entry, changes)
    else:
        entry = _known_entry(app, selected)
        if not entry:
            raise ControlError("Entry changed; refresh before editing")
        changes = _entry_changes(app, changes, entry)
        if not app.api_token or app._rate_limit_active() or app._request_budget_exhausted():
            raise ControlError("Cloud entry cannot be edited right now; retry when connected")
        app.update_entry(entry.get("workspace_id") or app.state["workspace_id"], entry["id"], changes)
        toggl_task_tree.correct(app, dict(entry), changes)
        record_edit(app, entry, changes)


def _resume_entry(app, selected):
    """Resolve a completed selection before changing the running interval."""
    fields = ("id", "start", "stop", "duration", "description", "project_id", "task_id", "workspace_id",
              "_offline", "_pending_kind", "_pending_ids", "_remote_entry_id")
    if not isinstance(selected, dict):
        raise ControlError("Entry changed; refresh before resuming")
    entry = next((e for e in snapshot(app)["entries"]
                  if all(e.get(k) == selected.get(k) for k in fields)), None)
    if entry is None:
        raise ControlError("Entry changed; refresh before resuming")
    duration = entry.get("duration")
    if (not entry.get("start") or not entry.get("stop")
            or not isinstance(duration, (int, float)) or duration < 0
            or (app.state.get("tracking") and entry.get("id") == app.state.get("entry_id"))):
        raise ControlError("Only completed entries can be resumed")
    if not entry.get("workspace_id"):
        raise ControlError("Entry workspace is unavailable; refresh before resuming")
    return entry


def dispatch(app, action, payload):
    if action in {'snapshot', 'work-snapshot'}:
        return _dispatch(app, action, payload)
    known = {'set-api-base', 'project-create', 'project-update', 'task-create', 'task-update',
             'sync', 'refresh', 'refresh-today', 'start', 'switch', 'resume', 'stop', 'edit-current', 'edit-entry', 'hide-activity'}
    label = action if isinstance(action, str) and action in known else 'unknown'
    toggl_debug.LOGGER.debug('Control action=%s requested', label)
    try:
        result = _dispatch(app, action, payload)
    except Exception:
        toggl_debug.LOGGER.error('Control action=%s failed', label, exc_info=True)
        raise
    toggl_debug.LOGGER.info('Control action=%s completed tracking=%s entry_id=%s',
                             label, app.state.get('tracking'), app.state.get('entry_id'))
    return result


def _dispatch(app, action, payload):
    if not isinstance(payload, dict):
        raise ControlError("Command payload must be an object")
    if action == "snapshot":
        return snapshot(app)
    if action == 'work-snapshot':
        return _work_snapshot(app, payload)
    if action == 'refresh-today':
        toggl_today.refresh(app, force=payload.get('force') is True)
        return _work_snapshot(app, payload)
    if action == 'hide-activity':
        with app.toggle_lock:
            if payload.get('context') != toggl_projects.context(app):
                raise ControlError('Account or workspace changed; reopen the work panel')
            values = _changes(app, {'project_id': payload.get('project_id'),
                                    'description': payload.get('description')})
            toggl_task_tree.hide_activity(app, values['project_id'], values['description'])
            return _dispatch(app, 'work-snapshot', {})
    if action == "set-api-base":
        with app.toggle_lock:
            try:
                api_base, pending_ids = app.switch_api_base(payload.get("url"))
            except ValueError as error:
                raise ControlError(str(error)) from error
        return {"api_base": api_base, "cleared_ids": pending_ids, "snapshot": snapshot(app)}
    if action in {"project-create", "project-update"}:
        with app.toggle_lock:
            try:
                saved = toggl_projects.write(app, action, payload)
            except toggl_projects.ProjectError as error:
                raise ControlError(str(error)) from error
        if app.icon_ref:
            app.GLib.idle_add(_refresh_ui, app)
        app._clear_status_message()
        result = snapshot(app)
        result["saved_project"] = saved
        return result
    if action in {"task-create", "task-update"}:
        with app.toggle_lock:
            try:
                saved = toggl_tasks.write(app, action, payload)
            except toggl_tasks.TaskError as error:
                raise ControlError(str(error)) from error
        app._clear_status_message()
        result = snapshot(app)
        result["saved_task"] = saved
        return result
    if action == "sync":
        with app.toggle_lock:
            app._run_sync_cycle(retry_review=True)
        return snapshot(app)
    if action == "refresh":
        now = time.monotonic()
        last = getattr(app, "_control_refreshed_at", None)
        last_manual = getattr(app, "_control_forced_at", None)
        force = payload.get("force") is True and (last_manual is None or now - last_manual >= 60)
        if last is None or now - last >= 60 or force:
            app._control_refreshed_at = now
            if force:
                app._control_forced_at = now
            app._refresh_projects(force=force)
            app._refresh_tasks(force=force)
            try:
                if not app.api_token or app._rate_limit_active() or app._request_budget_remaining() <= app.REQUEST_BUDGET_BACKGROUND_RESERVE:
                    raise ControlError("Cloud refresh unavailable; showing cached and pending entries")
                if not toggl_today.refresh(app, force=force):
                    raise ControlError('Today refresh unavailable')
                app._control_warning = ""
            except Exception:
                app._control_warning = "Cloud refresh unavailable; showing cached and pending entries"
        return snapshot(app)
    if action not in {"start", "switch", "resume", "stop", "edit-current", "edit-entry"}:
        raise ControlError("Unknown action")
    # Share the tray's mutation lock, including its existing toggle implementation.
    with app.toggle_lock:
        before = dict(app.state)
        if "context" in payload and payload["context"] != toggl_projects.context(app):
            raise ControlError("Account or workspace changed; reopen projects and tasks")
        if action == "resume" and "expected" not in payload:
            raise ControlError("Timer changed; refresh before resuming")
        if action in {"edit-current", "switch", "resume", "start", "stop"} and "expected" in payload:
            expected = payload["expected"]
            fields = ("tracking", "entry_id", "start_time", "description", "project_id", "task_id")
            if action == "resume":
                fields += ("workspace_id",)
            if not isinstance(expected, dict) or any(expected.get(k) != before.get(k) for k in fields):
                if action not in {'edit-current', 'switch'} or not _confirmed_current_draft(app, payload, expected, before):
                    raise ControlError("Timer changed; refresh before editing")
        if action == "resume":
            entry = _resume_entry(app, payload.get("entry"))
            changes = _changes(app, {"description": entry.get("description", ""),
                                     "project_id": entry.get("project_id"),
                                     "task_id": entry.get("task_id") if any(
                                         str(t.get("id")) == str(entry.get("task_id")) and t.get("active") is not False
                                         for t in app._cached_tasks()) else None}, entry)
            if app.state.get("tracking"):
                app.toggle_tracking(prompt_for_description=False)
            _edit_current(app, dict(changes, workspace_id=entry["workspace_id"]))
            app.toggle_tracking(prompt_for_description=False)
        elif action == "switch":
            changes = _changes(app, payload.get("changes", {}))
            if "start" in changes:
                raise ControlError("A new task starts now; edit the current timer to adjust its start")
            if "context" in payload and payload["context"] != toggl_projects.context(app):
                raise ControlError("Account or workspace changed; reopen projects and tasks")
            if app.state.get("tracking"):
                app.toggle_tracking(prompt_for_description=False)
            _edit_current(app, changes)
            app.toggle_tracking(prompt_for_description=False)
        elif action == "start":
            if not app.state.get("tracking"):
                _edit_current(app, _changes(app, payload.get("changes", {})))
                app.toggle_tracking(prompt_for_description=False)
        elif action == "stop":
            if app.state.get("tracking"):
                app.toggle_tracking(prompt_for_description=False)
        elif action == "edit-current":
            _edit_current(app, _changes(app, payload.get("changes", {}), before), expected=before)
        elif action == "edit-entry":
            _edit_entry(app, payload.get("entry"), payload.get("changes", {}), payload.get('reviewed_interval', False))
        stopped_entry = None
        if action == 'stop' and before.get('tracking') and not app.state.get('tracking'):
            entries = app._merge_entries_with_pending(getattr(app, '_control_entries', []), app._pending_as_entries())
            matches = [e for e in entries if e.get('stop') and e.get('start') == before.get('start_time')
                       and str(e.get('workspace_id')) == str(before.get('workspace_id'))
                       and (before.get('entry_id') is None or e.get('id') == before['entry_id'])]
            if len(matches) == 1:
                stopped_entry = matches[0]
    if app.state.get("tracking") and app.state.get("entry_id"):
        current = app.state
        entry = {"id": current["entry_id"], "workspace_id": current["workspace_id"],
                 "description": current.get("description", ""), "project_id": current.get("project_id"),
                 "task_id": current.get("task_id"),
                 "start": current["start_time"], "stop": None,
                 "duration": -int(app._coerce_datetime(current["start_time"]).timestamp())}
        toggl_today.remember(app, entry)
    if app.icon_ref:
        app.GLib.idle_add(_refresh_ui, app)
    result = snapshot(app)
    if stopped_entry is not None:
        result['stopped_entry'] = stopped_entry
    return _attach_resolution(app, payload, result)


def record_stop(app, entry_id, start, description, workspace_id, project_id, task_id, stop):
    """Keep cached durations correct when stopping through any tray path."""
    if not entry_id or not start:
        return
    entry = {"id": entry_id, "workspace_id": workspace_id, "project_id": project_id,
             "task_id": task_id, "description": description, "start": start, "stop": stop,
             "duration": app._duration_seconds(start, stop)}
    with app.pending_lock:
        context = toggl_projects.context(app)
        _update_resolution_entries(getattr(app, '_pending_resolutions', []), entry, context)
        toggl_today.remember(app, entry, scope=context)


def record_edit(app, original, changes, scope=None):
    with app.toggle_lock:
        scope = scope or toggl_projects.context(app)
        if scope != toggl_projects.context(app):
            return
        updated = dict(original, **changes)
        with app.pending_lock:
            _update_resolution_entries(getattr(app, '_pending_resolutions', []), updated, scope)
            toggl_today.remember(app, updated, scope=scope)
            with app.state_lock:
                if (str(app.state.get('entry_id')) == str(original.get('id'))
                        and str(app.state.get('workspace_id')) == str(original.get('workspace_id'))):
                    app.state.update({('start_time' if k == 'start' else k): v for k,v in changes.items()
                                      if k in {'description','project_id','task_id','start'}})
                    app.save_state()


def record_delete(app, entry, scope=None):
    with app.toggle_lock:
        scope = scope or toggl_projects.context(app)
        if scope != toggl_projects.context(app):
            return
        with app.pending_lock:
            toggl_today.forget(app, entry, scope=scope)
            with app.state_lock:
                if (str(app.state.get('entry_id')) == str(entry.get('id'))
                        and str(app.state.get('workspace_id')) == str(entry.get('workspace_id'))):
                    app.state.update(tracking=False, entry_id=None, start_time=None)
                    app.save_state()


def _refresh_ui(app):
    if app.icon_ref:
        app.update_tray_icon()
        app.icon_ref.title = app.get_tooltip()
        app._set_tray_menu(app.build_menu())
    return False


def start_server(app):
    app._ensure_private_state_dir()
    path = app.STATE_DIR / "control.sock"
    path.unlink(missing_ok=True)  # Caller already owns the single-instance lock.

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(10)
            try:
                line = self.rfile.readline(MAX_COMMAND + 1)
                if len(line) > MAX_COMMAND or not line.endswith(b"\n"):
                    raise ControlError("Command is too large or incomplete")
                command = json.loads(line)
                result = dispatch(app, command["action"], command.get("payload", {}))
                response = {"ok": True, "result": result}
            except Exception as error:
                response = {"ok": False, "error": str(error)[:500]}
            self.wfile.write(_json_bytes(response) + b"\n")

    previous_mask = os.umask(0o177)
    try:
        # Every mutation uses toggle_lock; reads can wait on HTTP independently.
        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            pass
        server = Server(str(path), Handler)
    finally:
        os.umask(previous_mask)
    os.chmod(path, 0o600)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def request(state_dir, action, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(35)
        try:
            client.connect(str(Path(state_dir) / "control.sock"))
        except OSError as error:
            raise ControlError("Toggl Tray is not running. Launch the tray app and try again.") from error
        command = _json_bytes({"action": action, "payload": payload}) + b"\n"
        if len(command) > MAX_COMMAND:
            raise ControlError("Command is too large")
        client.sendall(command)
        with client.makefile("rb") as reader:
            line = reader.readline(MAX_MESSAGE + 1)
        if not line or len(line) > MAX_MESSAGE:
            raise ControlError("Invalid response from Toggl Tray")
        response = json.loads(line)
        if not response.get("ok"):
            raise ControlError(response.get("error", "Command failed"))
        return response["result"]
