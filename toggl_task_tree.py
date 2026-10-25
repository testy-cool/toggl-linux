"""Bounded, account-scoped project/task history for instant tray choices."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
import toggl_projects

HISTORY_DAYS = 30
MAX_ENTRIES = 1000
_cache_lock = threading.Lock()
_refresh_lock = threading.Lock()
_attempts = {}


def _cache(app):
    data = app._load_json_with_backup(app.STATE_DIR / 'task-history.json', {})
    if not isinstance(data, dict) or data.get('context') != toggl_projects.context(app):
        return {}
    for key in ('entries', 'local_entries'):
        data[key] = [e for e in data.get(key, []) if isinstance(e, dict)] if isinstance(data.get(key), list) else []
    if not isinstance(data.get('fetched_at', 0), (int, float)):
        data['fetched_at'] = 0
    return data


def cached_entries(app):
    data = _cache(app)
    return [e for key in ('local_entries', 'entries')
            for e in (data.get(key) if isinstance(data.get(key), list) else []) if isinstance(e, dict)]


def _stamp(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.timestamp() if dt.tzinfo else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def _bounded(entries):
    valid = [e for e in entries if isinstance(e, dict) and _stamp(e.get('start')) is not None]
    return sorted(valid, key=lambda e: _stamp(e['start']), reverse=True)[:MAX_ENTRIES]


def refresh(app, force=False):
    """Fetch at most one history page off the GTK thread, preserving local choices."""
    scope = toggl_projects.context(app)
    token, base = app.api_token, toggl_projects._api_base(app)
    if (not token or not scope['workspace_id'] or app._rate_limit_active()
            or app._request_budget_remaining() <= app.REQUEST_BUDGET_BACKGROUND_RESERVE):
        return False
    if not _refresh_lock.acquire(blocking=False):
        return False
    try:
        key = (str(app.STATE_DIR), json.dumps(scope, sort_keys=True))
        now = time.time()
        cache = _cache(app)
        if now - _attempts.get(key, 0) < (60 if force else app.SYNC_INTERVAL_SECONDS):
            return False
        if not force and 0 <= now - cache.get('fetched_at', 0) < app.CLOUD_POLL_INTERVAL_SECONDS:
            return False
        _attempts[key] = now
        end = datetime.now(timezone.utc)
        entries = app._api('GET', '/me/time_entries', token=token, base_url=base,
                           params={'start_date': (end-timedelta(days=HISTORY_DAYS)).isoformat(),
                                   'end_date': end.isoformat()})
        if not isinstance(entries, list) or toggl_projects.context(app) != scope:
            return False
        with _cache_lock:
            cache = _cache(app)
            if toggl_projects.context(app) != scope:
                return False
            cloud = _bounded(entries)
            cloud_starts = {_stamp(e.get('start')) for e in cloud}
            local = [e for e in cache.get('local_entries', []) if _stamp(e.get('start')) not in cloud_starts]
            data = dict(context=scope, fetched_at=now, entries=cloud, local_entries=local)
            _save(app, data)
        return True
    except Exception as error:
        print(f'Project/task history unavailable: {error}', file=app.sys.stderr)
        return False
    finally:
        _refresh_lock.release()


def _save(app, data):
    path = app.STATE_DIR / 'task-history.json'
    app._atomic_write_text(path, json.dumps(data))
    path.chmod(0o600)
    backup = app._backup_path(path)
    if backup.exists():
        backup.chmod(0o600)


def _other_account(app, current, scope):
    """True when the running interval is a queued start made under a different account."""
    start = _stamp(current.get('start_time'))
    return any(_stamp(e.get('start')) == start and e.get('_task_context') not in (None, scope)
               for e in app._pending_as_entries())


def remember(app, current):
    """Keep a task available immediately after stopping, including while offline."""
    if not current.get('tracking') or not current.get('start_time'):
        return
    if _other_account(app, current, toggl_projects.context(app)):
        return
    entry = dict(id=current.get('entry_id') or 'local:'+current['start_time'],
                 start=current['start_time'], workspace_id=current.get('workspace_id'),
                 project_id=current.get('project_id'), description=current.get('description', ''))
    try:
        with _cache_lock:
            data = _cache(app)
            local = [e for e in data.get('local_entries', []) if e.get('id') != entry['id']]
            data.update(context=toggl_projects.context(app), local_entries=_bounded([entry]+local))
            _save(app, data)
    except OSError as error:
        print(f'Project/task history not saved: {error}', file=app.sys.stderr)


def group_projects(projects, entries, workspace, query='', now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = (now-timedelta(days=HISTORY_DAYS)).timestamp()
    groups = {str(p['id']): dict(p, tasks=[], count=0, last_used=0) for p in projects
              if isinstance(p, dict) and p.get('id') is not None and p.get('active') is not False
              and str(p.get('workspace_id')) == str(workspace)}
    groups['None'] = dict(id=None, workspace_id=workspace, name='No project', tasks=[], count=0, last_used=0)
    tasks, seen = {}, set()
    remote_ids = {_stamp(e.get('start')): str(e['id']) for e in entries if isinstance(e, dict) and e.get('id') is not None
                  and not e.get('_offline') and not str(e['id']).startswith(('local:', 'pending:'))
                  and str(e.get('workspace_id')) == str(workspace)}
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get('workspace_id')) != str(workspace):
            continue
        stamp = _stamp(entry.get('start'))
        project = groups.get(str(entry.get('project_id')))
        description = entry.get('description') or ''
        if stamp is None or not cutoff <= stamp <= now.timestamp()+60 or project is None or not isinstance(description, str):
            continue
        identity = str(entry.get('_remote_entry_id') or entry.get('id') or 'local:'+entry['start'])
        if identity.startswith(('local:', 'pending:')):
            identity = remote_ids.get(stamp, 'local:'+str(stamp))
        if identity in seen:
            continue
        seen.add(identity)
        key = (str(project['id']), description)
        task = tasks.setdefault(key, dict(project_id=project['id'], description=description, count=0, last_used=0))
        task['count'] += 1
        task['last_used'] = max(task['last_used'], stamp)
        project['count'] += 1
        project['last_used'] = max(project['last_used'], stamp)
    needle = query.strip().casefold()
    result = []
    for key, project in groups.items():
        children = [t for (pid, _), t in tasks.items() if pid == key]
        if needle and needle not in (project.get('name') or '').casefold():
            children = [t for t in children if needle in t['description'].casefold()]
            if not children:
                continue
        project['tasks'] = sorted(children, key=lambda t: (-t['last_used'], -t['count'], t['description'].casefold()))
        result.append(project)
    return sorted(result, key=lambda p: (-p['last_used'], -p['count'], (p.get('name') or '').casefold()))


def tree(app, query='', now=None):
    with app.state_lock:
        current = dict(app.state)
    cached = cached_entries(app)
    scope = toggl_projects.context(app)
    known_starts = {_stamp(e.get('start')) for e in cached}
    # Untagged legacy queue entries remain editable, but need a scoped history match to be suggested.
    pending = [e for e in app._pending_as_entries() if e.get('_task_context') == scope
               or (e.get('_task_context') is None and _stamp(e.get('start')) in known_starts)]
    pending_starts = {_stamp(e.get('start')) for e in pending}
    entries = pending+[e for e in cached if _stamp(e.get('start')) not in pending_starts]
    if current.get('tracking') and current.get('start_time') and not _other_account(app, current, scope):
        entries = [dict(id=current.get('entry_id') or 'local:'+current['start_time'], start=current['start_time'],
                        workspace_id=current.get('workspace_id'), project_id=current.get('project_id'),
                        description=current.get('description', ''))]+entries
    return group_projects(app._cached_projects(), entries, current.get('workspace_id'), query, now)


def quick_choices(rows, recent=4, frequent=3):
    """Show recent rows plus frequent alternatives without duplicate choices."""
    used = [r for r in rows if r.get('count')]
    first = sorted(used, key=lambda r: (-r['last_used'], -r['count']))[:recent]
    rest = sorted((r for r in used if r not in first), key=lambda r: (-r['count'], -r['last_used']))[:frequent]
    return first, rest


def select_task(app, project_id, description, context, expected):
    import toggl_control
    if context != toggl_projects.context(app):
        raise ValueError('Account or workspace changed; reopen projects and tasks')
    # Choosing the task that is already running keeps its timer instead of restarting it.
    if (expected.get('tracking') and str(expected.get('project_id')) == str(project_id)
            and ' '.join((expected.get('description') or '').split()) == ' '.join(description.split())):
        return None
    return toggl_control.dispatch(app, 'switch', {'changes': {'project_id': project_id, 'description': description},
                                                'expected': expected, 'context': context})
