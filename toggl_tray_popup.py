"""Left-click popup: projects, real tasks, and explicitly separate recent activities."""
import threading
import time
from html import escape
from gi.repository import Gtk, GLib, Gdk, Pango
import toggl_projects
import toggl_task_tree
import toggl_tasks
import toggl_control

NO_PROJECT_COLOR = '#8a8a8a'
ACTIVITIES_PER_PROJECT = 5
WIDTH = 440
MAX_HEIGHT = 560

_popup = None
_closed_at = 0.0


def project_action(current, project_id):
    """Choose explicit start semantics when there is no current timer to edit."""
    if current.get('tracking'):
        return 'edit-current', {'project_id': project_id, 'task_id': None}
    return 'start', {'project_id': project_id, 'task_id': None, 'description': ''}


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
                           hint='Use this project for the current timer' if tracking
                           else 'Start a timer with this project'))
        for task in sorted(server_tasks, key=lambda item: item.get('name', '').casefold()):
            result.append(dict(kind='task', project_id=group['id'], task_id=task['id'], task=task,
                               description='', label=task.get('name') or 'Unnamed task', color=color,
                               active=on_project and str(current.get('task_id')) == str(task['id'])))
        if activities:
            if needle or str(group['id']) in {str(item) for item in expanded}:
                visible = activities if needle else activities[:ACTIVITIES_PER_PROJECT]
                result.append(dict(kind='activity-heading', project_id=group['id'],
                                   label='Recent activities', color=color, active=False))
                for activity in visible:
                    description = ' '.join(activity['description'].split())
                    result.append(dict(kind='activity', project_id=group['id'], task_id=None,
                                       description=activity['description'], label=description or 'No description',
                                       color=color, active=on_project and current.get('task_id') is None
                                       and description == running))
            else:
                result.append(dict(kind='activity-toggle', project_id=group['id'],
                                   label=f'Show {len(activities)} recent activit' + ('y' if len(activities) == 1 else 'ies'),
                                   color=color, active=False))
    return result


class TrayPopup:
    def __init__(self, app, x, y):
        self.app = app
        self.context = toggl_projects.context(app)
        self.expanded = set()
        self.busy = False
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
        ''')
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), self.css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(box, 'set_margin_'+side)(10)
        self.window.add(box)
        self.search = Gtk.SearchEntry(placeholder_text='Search projects, tasks, and activities…')
        self.search.connect('search-changed', lambda *_: self._fill())
        self.search.connect('activate', lambda *_: self._activate_first())
        box.pack_start(self.search, False, False, 0)
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
        box.pack_start(self.message, False, False, 0)
        manage = Gtk.Button(label='Manage Tasks')
        manage.connect('clicked', self._manage_tasks)
        manage.set_sensitive(toggl_tasks.capability(app) != 'unavailable')
        box.pack_start(manage, False, False, 0)
        self.window.set_size_request(WIDTH, -1)
        self.window.connect('key-press-event', self._key)
        self.window.connect('focus-out-event', lambda *_: self._close_unless_busy())
        self.window.connect('destroy', self._destroy)
        self._fill()
        self.window.show_all()
        self.message.set_visible(False)
        self._place(x, y)
        self.window.present()
        self.search.grab_focus()

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
        for child in self.list.get_children():
            self.list.remove(child)
        query = self.search.get_text()
        with self.app.state_lock:
            current = dict(self.app.state)
        groups = toggl_task_tree.tree(self.app)
        for row in rows(groups, self.app._cached_projects(), current, query,
                        self.app._cached_tasks(), self.expanded):
            self.list.add(self._row(row))
        self.list.show_all()
        if not self.list.get_children():
            self._say('No matching project, task, or activity.')

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
            if row['active']:
                dot = Gtk.Label()
                dot.set_markup(f'<span foreground="{row["color"]}">●</span>')
                line.pack_start(dot, False, False, 0)
        label = Gtk.Label(xalign=0)
        label.set_markup(escape(row['label']))
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.get_style_context().add_class(row['kind'])
        if row['active']:
            label.get_style_context().add_class('running')
        line.pack_start(label, True, True, 0)
        if (row['kind'] == 'project' and row['project_id'] is not None
                and toggl_tasks.capability(self.app) != 'unavailable'):
            add = Gtk.Button(label='New Task')
            add.connect('clicked', lambda *_: self._new_task(row['project_id']))
            line.pack_end(add, False, False, 0)
        elif row['kind'] == 'task':
            edit = Gtk.Button(label='Edit')
            edit.connect('clicked', lambda *_: self._edit_task(row['task']))
            line.pack_end(edit, False, False, 0)
        elif row['kind'] == 'activity':
            save = Gtk.Button(label='Save as Task')
            save.connect('clicked', lambda *_: self._save_activity(row))
            hide = Gtk.Button(label='Hide')
            hide.connect('clicked', lambda *_: self._hide_activity(row))
            line.pack_end(hide, False, False, 0)
            if row['project_id'] is not None:
                line.pack_end(save, False, False, 0)
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
        with self.app.state_lock:
            expected = dict(self.app.state)
        self.busy = True
        self.list.set_sensitive(False)
        self._say('Starting…' if row['kind'] in ('task', 'activity') else 'Assigning project…')
        def work():
            error = None
            try:
                if row['kind'] in ('task', 'activity'):
                    toggl_task_tree.select_task(self.app, row['project_id'], row['description'], self.context,
                                                expected, row.get('task_id'))
                else:
                    action, changes = project_action(expected, row['project_id'])
                    toggl_control.dispatch(self.app, action, {'changes': changes, 'expected': expected,
                                                              'context': self.context})
            except Exception as exc:
                error = str(exc)
            GLib.idle_add(self._finish, error)
        threading.Thread(target=work, daemon=True).start()

    def _new_task(self, project_id, name='', start=False):
        import toggl_task_window
        editor = toggl_task_window.show(self.app, project_id, parent=self.window,
                                        on_saved=lambda _snapshot: self._fill(), start=start)
        if name:
            key = next((key for key, value in toggl_task_window._editors.items()
                        if value.project_id == project_id and value.original is None), None)
            if key is not None:
                toggl_task_window._editors[key].name.set_text(name)
        return editor

    def _edit_task(self, task):
        import toggl_task_window
        toggl_task_window.show(self.app, task['project_id'], task=task, parent=self.window,
                               on_saved=lambda _snapshot: self._fill())

    def _save_activity(self, row):
        self._new_task(row['project_id'], ' '.join(row['description'].split()))

    def _hide_activity(self, row):
        try:
            toggl_task_tree.hide_activity(self.app, row['project_id'], row['description'])
            self._fill()
        except ValueError as error:
            self._say(str(error))

    def _manage_tasks(self, *_):
        import toggl_task_window
        toggl_task_window.manage(self.app, self.window)

    def _finish(self, error):
        if self.window is None:
            return False
        self.busy = False
        if error:
            self.list.set_sensitive(True)
            self._say(error)
        else:
            self.window.destroy()
        return False

    def _say(self, text):
        self.message.set_text(text)
        self.message.set_visible(bool(text))

    def _close_unless_busy(self):
        if not self.busy and self.window is not None:
            self.window.destroy()
        return False

    def _key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        return False

    def _destroy(self, *_):
        global _popup, _closed_at
        _closed_at = time.monotonic()
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
        _popup.window.destroy()
    elif time.monotonic()-_closed_at > 0.4:  # this click already closed it by taking focus away
        _popup = TrayPopup(app, x, y)
    return False
