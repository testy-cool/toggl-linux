"""Searchable native project/task tree opened directly from the tray."""
import threading
from gi.repository import Gtk, GLib, Gdk, Pango
import toggl_projects
import toggl_task_tree
import toggl_control

_picker = None


class TaskPicker:
    def __init__(self, app):
        self.app = app
        self.context = toggl_projects.context(app)
        self.busy = False
        self.window = Gtk.Window(title='Projects and Tasks')
        self.window.set_default_size(640, 520)
        self.window.get_style_context().add_class('toggl-task-picker')
        self.css = Gtk.CssProvider()
        self.css.load_from_data(b'.toggl-task-picker, .toggl-task-picker * { font-size: 14px; }')
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(box, 'set_margin_'+side)(16)
        self.window.add(box)
        self.search = Gtk.SearchEntry(placeholder_text='Search projects and tasks…')
        self.search.connect('search-changed', lambda *_: self._fill())
        box.pack_start(self.search, False, False, 0)
        label = Gtk.Label(label='Recent projects first. Starting a task creates a new interval.', xalign=0)
        label.set_line_wrap(True)
        box.pack_start(label, False, False, 0)
        self.model = Gtk.TreeStore(str, str, object)
        self.tree = Gtk.TreeView(model=self.model)
        self.tree.set_headers_visible(True)
        self.tree.set_enable_search(False)
        renderer = Gtk.CellRendererText()
        renderer.set_property('ellipsize', Pango.EllipsizeMode.END)
        column = Gtk.TreeViewColumn('Project / task', renderer, text=0)
        column.set_expand(True)
        self.tree.append_column(column)
        self.tree.append_column(Gtk.TreeViewColumn('Uses in 30 days', Gtk.CellRendererText(), text=1))
        self.tree.get_selection().connect('changed', lambda *_: self._selection_changed())
        self.tree.connect('row-activated', self._activated)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.tree)
        box.pack_start(scroll, True, True, 0)
        self.message = Gtk.Label(xalign=0)
        self.message.set_line_wrap(True)
        box.pack_start(self.message, False, False, 0)
        actions = Gtk.Box(spacing=10)
        self.refresh = Gtk.Button(label='Refresh')
        self.use_project = Gtk.Button(label='Use Project')
        self.start_task = Gtk.Button(label='Start Task')
        self.refresh.connect('clicked', lambda *_: self._refresh())
        self.use_project.connect('clicked', lambda *_: self._apply(False))
        self.start_task.connect('clicked', lambda *_: self._apply(True))
        for button in (self.refresh, self.use_project, self.start_task):
            actions.pack_start(button, True, True, 0)
        box.pack_start(actions, False, False, 0)
        self.window.connect('destroy', self._destroy)
        self.window.connect('key-press-event', self._key)
        self._fill()
        self.window.show_all()
        self.search.grab_focus()

    def _fill(self):
        if self.window is None:
            return
        self.model.clear()
        groups = toggl_task_tree.tree(self.app, query=self.search.get_text())
        for group in groups:
            parent = self.model.append(None, [group.get('name') or str(group['id']), str(group['count']),
                                             dict(kind='project', project_id=group['id'])])
            for task in group['tasks']:
                self.model.append(parent, [task['description'] or 'No description', str(task['count']),
                                           dict(task, kind='task')])
        for i in range(len(groups) if self.search.get_text().strip() else min(4, len(groups))):
            self.tree.expand_row(Gtk.TreePath.new_from_indices([i]), False)
        self.message.set_text('No matching project or task.' if not groups else '')
        self._selection_changed()

    def _selection(self):
        model, row = self.tree.get_selection().get_selected()
        return model.get_value(row, 2) if row is not None else None

    def _selection_changed(self):
        selected = self._selection()
        self.use_project.set_sensitive(bool(selected) and not self.busy)
        self.start_task.set_sensitive(bool(selected and selected['kind'] == 'task') and not self.busy)

    def _activated(self, _tree, path, _column):
        if self.busy:
            return
        selected = self._selection()
        if selected and selected['kind'] == 'task':
            self._apply(True)
        elif self.tree.row_expanded(path):
            self.tree.collapse_row(path)
        else:
            self.tree.expand_row(path, False)

    def _set_busy(self, busy):
        self.busy = busy
        for widget in (self.search, self.tree, self.refresh):
            widget.set_sensitive(not busy)
        self._selection_changed()

    def _apply(self, start_task):
        selected = self._selection()
        if self.busy or not selected or (start_task and selected['kind'] != 'task'):
            return
        with self.app.state_lock:
            expected = dict(self.app.state)
        self._set_busy(True)
        self.message.set_text('Starting task…' if start_task else 'Assigning project…')
        def work():
            error = None
            try:
                if start_task:
                    toggl_task_tree.select_task(self.app, selected['project_id'], selected['description'], self.context, expected)
                else:
                    toggl_control.dispatch(self.app, 'edit-current', {'changes': {'project_id': selected['project_id']},
                                           'expected': expected, 'context': self.context})
            except Exception as exc:
                error = str(exc)
            GLib.idle_add(self._finish, error)
        threading.Thread(target=work, daemon=True).start()

    def _finish(self, error):
        if self.window is None:
            return False
        self._set_busy(False)
        if error:
            self.message.set_text(error)
        else:
            self.window.destroy()
        return False

    def _refresh(self):
        if self.busy:
            return
        self._set_busy(True)
        self.message.set_text('Refreshing projects and tasks…')
        def work():
            self.app._refresh_projects(force=True)
            toggl_task_tree.refresh(self.app, force=True)
            GLib.idle_add(self._refreshed)
        threading.Thread(target=work, daemon=True).start()

    def _refreshed(self):
        if self.window is None:
            return False
        self._set_busy(False)
        if self.context != toggl_projects.context(self.app):
            self.message.set_text('Account or workspace changed. Close and reopen this selector.')
            self.use_project.set_sensitive(False)
            self.start_task.set_sensitive(False)
        else:
            self._fill()
        if self.app.icon_ref:
            self.app._set_tray_menu(self.app.build_menu())
        return False

    def _key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        if event.keyval == Gdk.KEY_Down and self.search.has_focus():
            self.tree.grab_focus()
            self.tree.set_cursor(Gtk.TreePath.new_from_indices([0]))
            return True
        return False

    def _destroy(self, *_):
        global _picker
        self.window = None
        if _picker is self:
            _picker = None
        Gtk.StyleContext.remove_provider_for_screen(Gdk.Screen.get_default(), self.css)


def show(app):
    global _picker
    if _picker is not None:
        _picker.window.present()
    else:
        _picker = TaskPicker(app)
    return False
