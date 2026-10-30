"""Scoped daily entry cache; cloud reads never hold the timer mutation lock."""
from datetime import datetime, timedelta
import hashlib
import math
import threading
import time

import toggl_projects

_cache_lock = threading.RLock()
_refresh_lock = threading.Lock()
MAX_ENTRIES = 1000


def activity_key(project, description):
    import toggl_task_tree
    value = toggl_task_tree._activity_key(project, description if isinstance(description, str) else '')
    return hashlib.sha256(value.encode('utf-8', errors='backslashreplace')).hexdigest()


def window(app):
    day = app.datetime.now().date()
    start = datetime(day.year, day.month, day.day).astimezone()
    tomorrow = day + timedelta(days=1)
    end = datetime(tomorrow.year, tomorrow.month, tomorrow.day).astimezone()
    return day.isoformat(), start, end


def overlaps(app, entry, current=None):
    _, start, end = window(app)
    try:
        began = app._parse_iso(entry.get('start'))
        stopped = app._parse_iso(entry.get('stop'))
        if began is None or began.tzinfo is None:
            return False
        duration = entry.get('duration')
        if (stopped is None and isinstance(duration, (int, float))
                and math.isfinite(duration) and duration >= 0):
            stopped = began + timedelta(seconds=duration)
        if stopped is None:
            return began < end and (began >= start or (current and current.get('tracking')
                and began == app._parse_iso(current.get('start_time'))
                and str(entry.get('workspace_id')) == str(current.get('workspace_id'))))
        return stopped.tzinfo is not None and began < end and stopped > start and stopped >= began
    except (TypeError, ValueError, OverflowError):
        return False


def _key(entry):
    return str(entry.get('workspace_id')), str(entry.get('id'))


def _cache(app):
    value = getattr(app, '_today_cache', None)
    day, _, _ = window(app)
    context = toggl_projects.context(app)
    return value if (value and value['date'] == day and value['context'] == context
                     and value.get('origin') == app.get_api_base()) else None


def _empty(app):
    return dict(date=window(app)[0], context=toggl_projects.context(app), origin=app.get_api_base(), entries=[],
                fetched_at=None, incomplete=False, error='', version=0, changes={})


def cached(app):
    with _cache_lock:
        value = _cache(app)
        return None if value is None else list(value['entries'])


def status(app):
    with _cache_lock:
        value = _cache(app) or _empty(app)
        return dict(today_date=value['date'], today_fetched_at=value['fetched_at'],
                    today_incomplete=value['incomplete'], today_error=value['error'])


def legacy_entries(app, scope=None):
    scope = scope or toggl_projects.context(app)
    tag = getattr(app, '_control_entries_context', None)
    if (not isinstance(tag, dict) or tag.get('origin') != app.get_api_base()
            or getattr(app, '_control_entries_date', '') != window(app)[0]
            or scope['account'] is not None and tag.get('account') != scope['account']):
        return []
    return list(getattr(app, '_control_entries', []))


def remember(app, entry, deleted=False, scope=None):
    """Retain local stops and corrections across a concurrent read response."""
    if not entry.get('id'):
        return
    with _cache_lock:
        if scope is not None and scope != toggl_projects.context(app):
            return
        scope = toggl_projects.context(app)
        value = _cache(app) or _empty(app)
        entries = [e for e in value['entries'] if _key(e) != _key(entry)]
        included = not deleted and overlaps(app, entry, app.state)
        if included:
            entries.append(dict(entry))
        value['version'] += 1
        value['changes'][_key(entry)] = (value['version'],
            dict(entry) if included else None)
        value['entries'] = entries
        app._today_cache = value
        legacy = [e for e in legacy_entries(app, scope) if _key(e) != _key(entry)]
        if included:
            legacy.append(dict(entry))
        app._control_entries = legacy
        app._control_entries_date = value['date']
        app._control_entries_context = dict(scope, origin=app.get_api_base())


def forget(app, entry, scope=None):
    selected = dict(entry, id=entry.get('_remote_entry_id') or entry.get('id'))
    remember(app, selected, deleted=True, scope=scope)
    if entry.get('_offline') and entry.get('_remote_entry_id'):
        with _cache_lock:
            value = _cache(app)
            if value:
                value.update(fetched_at=None, incomplete=True,
                             error="Refresh today's totals after discarding local changes")


def refresh(app, force=False):
    """One account-frozen read, throttled across window reopenings and callers."""
    if not _refresh_lock.acquire(blocking=False):
        return False
    try:
        with app.toggle_lock:
            scope, day = toggl_projects.context(app), window(app)[0]
            token, base = app.api_token, app.get_api_base()
            previous = getattr(app, '_today_attempt', None)
            now = time.monotonic()
            with _cache_lock:
                value = _cache(app) or _empty(app)
                if previous and previous[:2] == (scope, day) and now - previous[2] < 60:
                    return bool(value['fetched_at'])
                if not force and value['fetched_at']:
                    return True
                app._today_attempt = (scope, day, now)
                app._today_cache = value
                baseline = value['version']
            if (not token or not scope['workspace_id'] or app._rate_limit_active()
                    or app._request_budget_remaining() <= app.REQUEST_BUDGET_BACKGROUND_RESERVE):
                with _cache_lock:
                    value['error'] = "Today's totals unavailable; saved and pending time is shown"
                    app._today_cache = value
                return False
            _, start, _ = window(app)
        try:
            # Modified-since includes a long interval that began before midnight
            # but ended today, unlike the API's start_date filter.
            response = app.api_get('/me/time_entries', params={'since': int(start.timestamp())},
                                   token=token, base_url=base)
            if not isinstance(response, list):
                raise ValueError('Expected an entry list')
        except Exception:
            with _cache_lock:
                if toggl_projects.context(app) == scope and window(app)[0] == day:
                    value = _cache(app) or _empty(app)
                    value['error'] = "Today's totals unavailable; saved and pending time is shown"
                    app._today_cache = value
            return False
        with app.toggle_lock:
            if toggl_projects.context(app) != scope or window(app)[0] != day:
                return False
            with _cache_lock:
                latest = _cache(app)
                if latest is not value:
                    return False  # The account/day changed away and back during the read.
                incoming = {_key(e): dict(e) for e in response[:MAX_ENTRIES]
                            if isinstance(e, dict) and e.get('id') is not None
                            and not e.get('server_deleted_at') and not e.get('deleted_at')
                            and str(e.get('workspace_id')) == str(scope['workspace_id'])
                            and overlaps(app, e, app.state)}
                # A stop/edit made while the HTTP request was in flight wins.
                for key, (version, entry) in latest['changes'].items():
                    if version > baseline:
                        incoming.pop(key, None)
                        if entry is not None:
                            incoming[key] = dict(entry)
                latest.update(entries=list(incoming.values()), fetched_at=time.time(),
                              incomplete=len(response) >= MAX_ENTRIES, error='', changes={})
                app._today_cache = latest
                other = [e for e in legacy_entries(app, scope)
                         if str(e.get('workspace_id')) != str(scope['workspace_id'])]
                app._control_entries = other + list(latest['entries'])
                app._control_entries_date = day
                app._control_entries_context = dict(scope, origin=base)
        return True
    finally:
        _refresh_lock.release()
