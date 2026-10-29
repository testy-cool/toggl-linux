"""Left-click work panel with visible recent work and embedded editing."""
import threading
import time
from datetime import datetime, timezone
from html import escape
from gi.repository import Gtk, GLib, Gdk, Pango
import toggl_projects
import toggl_task_tree
import toggl_tasks
import toggl_control
from toggl_popup_editor import InlineEditor

NO_PROJECT_COLOR = '#8a8a8a'
ACTIVITIES_PER_PROJECT = 5
WIDTH = 440
MAX_HEIGHT = 560

_popup = None
_closed_at = 0.0
_browse_state = None


def running_timer(current, projects, tasks, now=None):
    """Describe the current interval independently of filtered or hidden history."""
    if not current.get('tracking'):
        return None
    workspace = str(current.get('workspace_id'))
    project = next((item for item in projects
                    if str(item.get('workspace_id')) == workspace
                    and str(item.get('id')) == str(current.get('project_id'))), {})
    task = next((item for item in tasks
                 if str(item.get('workspace_id')) == workspace
                 and str(item.get('project_id')) == str(current.get('project_id'))
                 and str(item.get('id')) == str(current.get('task_id'))), {})
    task_name = task.get('name') or ('Current task' if current.get('task_id') is not None else '')
    description = ' '.join((current.get('description') or '').split())
    project_name = project.get('name') or ('No project' if current.get('project_id') is None else 'Current project')
    seconds = 0
    try:
        start = datetime.fromisoformat(current['start_time'].replace('Z', '+00:00'))
        seconds = max(0, int(((now or datetime.now(timezone.utc)) - start).total_seconds()))
    except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
        pass
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return dict(title=description or task_name or 'No description', project=project_name,
                task=task_name if description else '', elapsed=f'{hours}:{minutes:02d}:{seconds:02d}')


def rows(groups, projects, current, query='', tasks=None, expanded=()):
    """Flatten the tree into display rows. Pure, so it can be tested without GTK."""
    colors = {str(p.get('id')): p.get('color') for p in projects if isinstance(p, dict)}
    tracking = bool(current.get('tracking'))
    running = ' '.join((current.get('description') or '').split())
    tasks = tasks or []
    needle = query.strip().casefold()
    result = []
    for group in groups:
        activities = group.get('activities', group.get('tasks', []))
        server_tasks = [task for task in tasks if str(task.get('project_id')) == str(group['id'])
                        and (current.get('workspace_id') is None
                             or str(task.get('workspace_id')) == str(current['workspace_id']))
                        and task.get('active') is not False]
        project_match = needle and needle in (group.get('name') or '').casefold()
        if needle and not project_match:
            server_tasks = [task for task in server_tasks if needle in task.get('name', '').casefold()]
            activities = [activity for activity in activities
                          if needle in activity.get('description', '').casefold()]
        if not server_tasks and not activities and group['id'] is None:
            continue
        if needle and not project_match and not server_tasks and not activities:
            continue
        color = colors.get(str(group['id'])) or NO_PROJECT_COLOR
        on_project = tracking and str(current.get('project_id')) == str(group['id'])
        result.append(dict(kind='project', project_id=group['id'], label=group.get('name') or str(group['id']),
                           color=color, active=on_project,
                           hint='Start a new timer in this project'))
        for task in sorted(server_tasks, key=lambda item: item.get('name', '').casefold()):
            result.append(dict(kind='task', project_id=group['id'], task_id=task['id'], task=task,
                               description='', label=task.get('name') or 'Unnamed task', color=color,
                               active=on_project and str(current.get('task_id')) == str(task['id'])))
        if activities:
            show_all = bool(needle) or str(group['id']) in {str(item) for item in expanded}
            visible = activities if show_all else activities[:ACTIVITIES_PER_PROJECT]
            result.append(dict(kind='activity-heading', project_id=group['id'],
                               label='Recent work', color=color, active=False))
            for activity in visible:
                description = ' '.join(activity['description'].split())
                result.append(dict(kind='activity', project_id=group['id'], task_id=None,
                                   description=activity['description'], label=description or 'No description',
                                   color=color, active=on_project and current.get('task_id') is None
                                   and description == running))
            if not show_all and len(activities) > ACTIVITIES_PER_PROJECT:
                result.append(dict(kind='activity-toggle', project_id=group['id'],
                                   label=f'Show {len(activities) - ACTIVITIES_PER_PROJECT} more',
                                   color=color, active=False))
    return result


class TrayPopup:
    def __init__(self, app, x, y):
        self.app = app
        self.context = toggl_projects.context(app)
        saved = _browse_state if _browse_state and _browse_state['context'] == self.context else {}
        self.expanded = set(saved.get('expanded', ()))
        self.editor = None
        self.busy = False
        self.rendered_state = {}
        self._render_key = None
        self.tick_source = None
        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_decorated(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_keep_above(True)
        self.window.set_type_hint(Gdk.WindowTypeHint.DROPDOWN_MENU)
        self.window.get_style_context().add_class('toggl-tray-popup')
        self.css = Gtk.CssProvider()
        self.css.load_from_data(b'''
            .toggl-tray-popup, .toggl-tray-popup * { font-size: 14px; }
            .toggl-tray-popup list row { padding: 5px 10px; }
            .toggl-tray-popup .project { font-weight: bold; }
            .toggl-tray-popup .running { font-weight: bold; }
            .toggl-tray-popup .current-timer {
                background-color: alpha(@theme_selected_bg_color, 0.13);
                border-radius: 8px;
                padding: 10px;
            }
        ''')
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), self.css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(root, 'set_margin_'+side)(12)
        self.window.add(root)
        self.pages = Gtk.Stack()
        self.pages.set_vhomogeneous(False)
        root.pack_start(self.pages, True, True, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.pages.add_named(box, 'browse')
        self.search = Gtk.SearchEntry(placeholder_text='Search work…')
        self.search.set_text(saved.get('query', ''))
        self.search.connect('search-changed', lambda *_: self._fill())
        self.search.connect('activate', lambda *_: self._activate_first())
        box.pack_start(self.search, False, False, 0)
        self.current = Gtk.Box(spacing=10)
        self.current.get_style_context().add_class('current-timer')
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.current_status = Gtk.Label(xalign=0)
        self.current_title = Gtk.Label(xalign=0)
        self.current_title.get_style_context().add_class('running')
        self.current_project = Gtk.Label(xalign=0)
        for label in (self.current_status, self.current_title, self.current_project):
            label.set_ellipsize(Pango.EllipsizeMode.END)
            details.pack_start(label, False, False, 0)
        self.current.pack_start(details, True, True, 0)
        actions = Gtk.Box(spacing=8)
        actions.set_valign(Gtk.Align.CENTER)
        self.stop = Gtk.Button(label='Stop')
        self.stop.connect('clicked', self._stop_current)
        self.edit = Gtk.Button(label='Edit')
        self.edit.connect('clicked', self._edit_current)
        actions.pack_start(self.stop, False, False, 0)
        actions.pack_start(self.edit, False, False, 0)
        self.current.pack_end(actions, False, False, 0)
        box.pack_start(self.current, False, False, 0)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list.set_activate_on_single_click(True)
        self.list.connect('row-activated', self._clicked)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(MAX_HEIGHT)
        self.scroll.add(self.list)
        box.pack_start(self.scroll, True, True, 0)
        self.message = Gtk.Label(xalign=0)
        self.message.set_line_wrap(True)
        root.pack_start(self.message, False, False, 0)
        footer = Gtk.Box(spacing=8)
        new = Gtk.Button(label='New timer')
        new.connect('clicked', lambda *_: self._open_editor('draft'))
        footer.pack_start(new, True, True, 0)
        manage = Gtk.Button(label='Manage Tasks')
        manage.connect('clicked', self._manage_tasks)
        manage.set_sensitive(toggl_tasks.capability(app) != 'unavailable')
        footer.pack_start(manage, True, True, 0)
        box.pack_start(footer, False, False, 0)
        self.window.set_size_request(WIDTH, -1)
        self.window.connect('key-press-event', self._key)
        self.window.connect('focus-out-event', lambda *_: self._close_unless_busy())
        self.window.connect('destroy', self._destroy)
        self.window.connect('size-allocate', lambda *_: self._place(x, y))
        self._fill()
        GLib.idle_add(self._restore_scroll, saved.get('scroll', 0))
        self.window.show_all()
        self.current.set_visible(bool(self.rendered_state.get('tracking')))
        self.message.set_visible(False)
        self._place(x, y)
        self.window.present()
        self.search.grab_focus()
        self.tick_source = GLib.timeout_add_seconds(1, self._tick)

    def _place(self, x, y):
        display = Gdk.Display.get_default()
        monitor = display.get_monitor_at_point(x, y) if display else None
        width, height = self.window.get_size()
        if monitor is None:
            self.window.move(max(0, x-width//2), y)
            return
        area = monitor.get_workarea()
        left = min(max(area.x, x-width//2), area.x+area.width-width)
        top = y if y < area.y+area.height//2 else y-height  # open away from the panel
        self.window.move(left, max(area.y, min(top, area.y+area.height-height)))

    def _fill(self):
        if self.window is None or self.editor is not None:
            return
        adjustment = self.scroll.get_vadjustment()
        position = adjustment.get_value()
        for child in self.list.get_children():
            self.list.remove(child)
        query = self.search.get_text()
        with self.app.state_lock:
            current = dict(self.app.state)
        projects, tasks = self.app._cached_projects(), self.app._cached_tasks()
        self._update_current(current, projects, tasks)
        groups = toggl_task_tree.tree(self.app)
        display_rows = rows(groups, projects, current, query, tasks, self.expanded)
        for row in display_rows:
            self.list.add(self._row(row))
        self._render_key = (current, projects, tasks, display_rows)
        self.list.show_all()
        GLib.idle_add(self._resize)
        GLib.idle_add(self._restore_scroll, position)
        if not display_rows:
            self._say('No matching work.')
        else:
            self._say('')

    def _resize(self):
        if self.window is None:
            return False
        height = self.window.get_child().get_preferred_height()[1]
        display = Gdk.Display.get_default()
        x, y = self.window.get_position()
        monitor = display.get_monitor_at_point(x, y) if display else None
        if monitor:
            height = min(height, monitor.get_workarea().height)
        self.window.resize(WIDTH, max(1, height))
        return False

    def _update_current(self, current, projects, tasks):
        self.rendered_state = dict(current)
        info = running_timer(current, projects, tasks)
        self.current.set_visible(info is not None)
        if info:
            self.current_status.set_text('Running · ' + info['elapsed'])
            self.current_title.set_text(info['title'])
            self.current_title.set_tooltip_text(info['title'])
            project = 'Project: ' + info['project']
            if info['task']:
                project += ' · Task: ' + info['task']
            self.current_project.set_text(project)
            self.current_project.set_tooltip_text(project)

    def _tick(self):
        if self.window is None:
            return False
        if toggl_projects.context(self.app) != self.context:
            if self.editor is not None:
                self.editor.save.set_sensitive(False)
                self._say('Account or workspace changed. Copy your draft, then cancel and reopen the panel.')
                return True
            self.window.destroy()
            return False
        if self.editor is not None:
            return True  # Never rebuild an input or its choices underneath a draft.
        if not self.busy:
            with self.app.state_lock:
                current = dict(self.app.state)
            projects, tasks = self.app._cached_projects(), self.app._cached_tasks()
            display_rows = rows(toggl_task_tree.tree(self.app), projects, current,
                                self.search.get_text(), tasks, self.expanded)
            if self._render_key != (current, projects, tasks, display_rows):
                self._fill()
            else:
                self._update_current(current, projects, tasks)
        return True

    def _restore_scroll(self, position):
        if self.window is not None:
            adjustment = self.scroll.get_vadjustment()
            adjustment.set_value(min(position, max(adjustment.get_lower(),
                                                   adjustment.get_upper() - adjustment.get_page_size())))
        return False

    def _row(self, row):
        widget = Gtk.ListBoxRow()
        widget.data = row
        line = Gtk.Box(spacing=8)
        if row['kind'] == 'project':
            dot = Gtk.Label()
            dot.set_markup(f'<span foreground="{row["color"]}">●</span>')
            line.pack_start(dot, False, False, 0)
        else:
            indent = Gtk.Label(label='')
            indent.set_margin_start(18)
            line.pack_start(indent, False, False, 0)
        label = Gtk.Label(xalign=0)
        label.set_markup(escape(row['label']))
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.get_style_context().add_class(row['kind'])
        if row['active']:
            label.get_style_context().add_class('running')
        line.pack_start(label, True, True, 0)
        if row['active'] and row['kind'] in ('task', 'activity'):
            line.pack_start(Gtk.Label(label='Running'), False, False, 0)
        if (row['kind'] == 'project' and row['project_id'] is not None
                and toggl_tasks.capability(self.app) != 'unavailable'):
            add = Gtk.Button(label='New task')
            add.connect('clicked', lambda *_: self._new_task(row['project_id']))
            line.pack_end(add, False, False, 0)
        elif row['kind'] in ('task', 'activity'):
            edit = Gtk.Button(label='Edit')
            edit.connect('clicked', lambda *_: self._edit_row(row))
            line.pack_end(edit, False, False, 0)
        widget.add(line)
        if row['kind'] == 'task':
            widget.set_tooltip_text('Start this task with a blank description')
        elif row['kind'] == 'activity':
            widget.set_tooltip_text('Start this recent activity again')
        elif row['kind'] == 'project':
            widget.set_tooltip_text(row['hint'])
        elif row['kind'] == 'activity-toggle':
            widget.set_tooltip_text('Show recent time-entry descriptions')
        else:
            widget.set_activatable(False)
        return widget

    def _activate_first(self):
        task = next((r for r in self.list.get_children() if r.data['kind'] in ('task', 'activity')), None)
        if task is not None:
            self._clicked(self.list, task)

    def _clicked(self, _list, widget):
        if self.busy:
            return
        row = widget.data
        if row['kind'] == 'activity-heading':
            return
        if row['kind'] == 'activity-toggle':
            self.expanded.add(row['project_id'])
            self._fill()
            return
        if row['kind'] == 'project':
            self._open_editor('draft', row=dict(project_id=row['project_id'], description=''))
            return
        expected = dict(self.rendered_state)
        self.busy = True
        self.list.set_sensitive(False)
        self._say('Starting…')
        def work():
            error = None
            try:
                toggl_task_tree.select_task(self.app, row['project_id'], row['description'], self.context,
                                            expected, row.get('task_id'))
            except Exception as exc:
                error = str(exc)
            GLib.idle_add(self._finish, error)
        threading.Thread(target=work, daemon=True).start()

    def _new_task(self, project_id):
        self._open_editor('new-task', row=dict(project_id=project_id, description=''))

    def _edit_row(self, row):
        if row['active']:
            self._edit_current()
        elif row['kind'] == 'task':
            self._open_editor('task', row=row['task'])
        else:
            self._open_editor('draft', row=row)

    def _open_editor(self, mode, row=None):
        if self.busy or self.window is None or self.editor is not None:
            return
        self.browse_position = self.scroll.get_vadjustment().get_value()
        snapshot = toggl_control.snapshot(self.app)
        if snapshot['project_context'] != self.context:
            self._say('Account or workspace changed; reopen the panel.')
            return
        if mode in ('current', 'draft'):
            snapshot['state'] = dict(self.rendered_state)
        self.editor = InlineEditor(snapshot, mode, self._submit_edit, self._cancel_edit, row)
        if mode == 'draft' and row and row.get('kind') == 'activity':
            hide = Gtk.Button(label='Hide suggestion')
            hide.connect('clicked', lambda *_: self._hide_edited_activity(row))
            self.editor.widget.pack_start(hide, False, False, 0)
        self.pages.add_named(self.editor.widget, 'edit')
        self.editor.widget.show_all()
        self.pages.set_visible_child_name('edit')
        self._say('')
        GLib.idle_add(self._resize)
        self.editor.description.grab_focus()

    def _cancel_edit(self):
        if self.busy or self.editor is None:
            return
        if toggl_projects.context(self.app) != self.context:
            self.window.destroy()
            return
        self.pages.set_visible_child_name('browse')
        self.pages.remove(self.editor.widget)
        self.editor = None
        self._fill()
        GLib.idle_add(self._restore_scroll, self.browse_position)
        self.search.grab_focus()

    def _hide_edited_activity(self, row):
        if self.busy:
            return
        if toggl_projects.context(self.app) != self.context:
            self._say('Account or workspace changed; cancel and reopen the panel.')
            return
        try:
            toggl_task_tree.hide_activity(self.app, row['project_id'], row['description'])
        except ValueError as error:
            self._say(str(error))
            return
        self._cancel_edit()

    def _submit_edit(self, action, payload, success):
        if self.busy or self.editor is None:
            return
        if toggl_projects.context(self.app) != self.context:
            self._say('Account or workspace changed. Copy your draft, then cancel and reopen the panel.')
            return
        if action == 'edit-current' and not payload.get('changes'):
            self._cancel_edit()
            self._say('No changes to save')
            return
        self.busy = True
        self.editor.widget.set_sensitive(False)
        self._say('Saving…' if action != 'switch' else 'Starting…')

        def worker():
            error = None
            try:
                toggl_control.dispatch(self.app, action, payload)
            except Exception as exc:
                error = str(exc)
            GLib.idle_add(self._edit_finished, error, success)
        threading.Thread(target=worker, daemon=True).start()

    def _edit_finished(self, error, success):
        if self.window is None:
            return False
        self.busy = False
        self.editor.widget.set_sensitive(True)
        if not error:
            self._cancel_edit()
        self._say(error or success)
        return False

    def _manage_tasks(self, *_):
        if self.busy:
            return
        import toggl_task_window
        toggl_task_window.manage(self.app, self.window)

    def _stop_current(self, *_):
        if self.busy or self.window is None:
            return
        expected = dict(self.rendered_state)
        self.busy = True
        for widget in (self.list, self.stop, self.edit):
            widget.set_sensitive(False)
        self._say('Stopping…')

        def worker():
            error = None
            try:
                toggl_control.dispatch(self.app, 'stop', {'expected': expected, 'context': self.context})
            except Exception as exc:
                error = str(exc)
            GLib.idle_add(self._finish, error)
        threading.Thread(target=worker, daemon=True).start()

    def _edit_current(self, *_):
        if self.busy or self.window is None:
            return
        self._open_editor('current')

    def _finish(self, error):
        if self.window is None:
            return False
        self.busy = False
        self.list.set_sensitive(True)
        self.stop.set_sensitive(True)
        self.edit.set_sensitive(True)
        if not error:
            self._fill()
        self._say(error or '')
        return False

    def _say(self, text):
        if self.window is None:
            return
        self.message.set_text(text)
        self.message.set_visible(bool(text))

    def _close_unless_busy(self):
        if not self.busy and self.editor is None and self.window is not None:
            self.window.destroy()
        return False

    def _key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            if self.busy:
                return True
            if self.editor is not None:
                self._cancel_edit()
            else:
                self.window.destroy()
            return True
        return False

    def _destroy(self, *_):
        global _popup, _closed_at, _browse_state
        _browse_state = dict(context=self.context, query=self.search.get_text(), expanded=set(self.expanded),
                             scroll=getattr(self, 'browse_position', self.scroll.get_vadjustment().get_value())
                             if self.editor is not None else self.scroll.get_vadjustment().get_value())
        _closed_at = time.monotonic()
        if self.tick_source is not None:
            GLib.source_remove(self.tick_source)
            self.tick_source = None
        self.window = None
        if _popup is self:
            _popup = None
        Gtk.StyleContext.remove_provider_for_screen(Gdk.Screen.get_default(), self.css)


def toggle_at_pointer(app):
    """Open the popup where the mouse is, for the menu item and desktops without a left-click hook."""
    display = Gdk.Display.get_default()
    seat = display.get_default_seat() if display else None
    pointer = seat.get_pointer() if seat else None
    if pointer is None:  # no mouse: open near the top middle of the primary screen
        monitor = display.get_primary_monitor() or display.get_monitor(0) if display else None
        area = monitor.get_workarea() if monitor else None
        x, y = (area.x+area.width//2, area.y) if area else (0, 0)
    else:
        _screen, x, y = pointer.get_position()
    return toggle(app, x, y)


def toggle(app, x, y):
    """Open the popup at the icon, or close it when it is already open."""
    global _popup
    if _popup is not None and _popup.window is not None:
        if _popup.editor is not None or _popup.busy:
            _popup.window.present()
        else:
            _popup.window.destroy()
    elif time.monotonic()-_closed_at > 0.4:  # this click already closed it by taking focus away
        _popup = TrayPopup(app, x, y)
    return False
