"""Native task creation and management for OpenTickly task metadata."""
from copy import deepcopy
import threading

from gi.repository import Gtk, GLib, Gdk, Pango
import toggl_control

_editors = {}
_manager = None


def task_name(value):
    value = value.strip() if isinstance(value, str) else ""
    if not value or len(value) > 200:
        raise ValueError("Enter a task name of 1–200 characters")
    return value


def project_tasks(snapshot, project_id, include_archived=False):
    return [task for task in snapshot.get("tasks", [])
            if str(task.get("workspace_id")) == str(snapshot["state"].get("workspace_id"))
            and str(task.get("project_id")) == str(project_id)
            and (include_archived or task.get("active") is not False)]


class TaskEditor:
    def __init__(self, app, snapshot, project_id, task=None, parent=None, on_saved=None, start=False):
        self.app = app
        self.context = deepcopy(snapshot["project_context"])
        self.original = deepcopy(task)
        self.project_id = task.get("project_id") if task else project_id
        self.on_saved = on_saved
        self.start_after_save = bool(start and not task)
        self.busy = False
        project = next((p for p in snapshot["projects"] if str(p.get("id")) == str(self.project_id)), None)
        self.window = Gtk.Window(title="Edit Task" if task else "New Task")
        self.window.set_default_size(500, -1)
        if parent:
            self.window.set_transient_for(parent)
        self.window.get_style_context().add_class("toggl-task-editor")
        self.css = Gtk.CssProvider()
        self.css.load_from_data(b".toggl-task-editor, .toggl-task-editor * { font-size: 14px; }")
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), self.css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + side)(16)
        self.window.add(box)
        box.pack_start(Gtk.Label(label=f"Project: {(project or {}).get('name', 'Selected project')}", xalign=0),
                       False, False, 0)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        grid.attach(Gtk.Label(label="Task name", xalign=0), 0, 0, 1, 1)
        self.name = Gtk.Entry()
        self.name.set_text(task.get("name", "") if task else "")
        self.name.set_max_length(200)
        self.name.set_hexpand(True)
        self.name.connect("activate", lambda *_: self._save(self.start_after_save))
        grid.attach(self.name, 1, 0, 1, 1)
        box.pack_start(grid, False, False, 0)
        self.message = Gtk.Label(xalign=0)
        self.message.set_line_wrap(True)
        box.pack_start(self.message, False, False, 0)
        actions = Gtk.Box(spacing=10)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.window.destroy())
        self.save = Gtk.Button(label="Save Task" if task else "Create Task")
        self.save.connect("clicked", lambda *_: self._save(False))
        actions.pack_start(close, True, True, 0)
        actions.pack_start(self.save, True, True, 0)
        if not task:
            self.create_start = Gtk.Button(label="Create & Start")
            self.create_start.connect("clicked", lambda *_: self._save(True))
            actions.pack_start(self.create_start, True, True, 0)
        box.pack_start(actions, False, False, 0)
        self.window.connect("destroy", self._destroy)
        self.window.connect("key-press-event", self._key)
        self.window.show_all()
        self.name.grab_focus()

    def _save(self, start=False):
        if self.busy:
            return
        try:
            name = task_name(self.name.get_text())
        except ValueError as error:
            self.message.set_text(str(error))
            return
        payload = {"context": self.context, "name": name, "project_id": self.project_id}
        action = "task-update" if self.original else "task-create"
        if self.original:
            payload.update(task=self.original, active=self.original.get("active", True))
        self.busy = True
        self.start_after_save = bool(start)
        self.name.set_sensitive(False)
        self.save.set_sensitive(False)
        if hasattr(self, "create_start"):
            self.create_start.set_sensitive(False)
        self.message.set_text("Saving task…")

        def worker():
            try:
                result = toggl_control.request(self.app.STATE_DIR, action, payload)
                if self.start_after_save:
                    saved = result["saved_task"]
                    result = toggl_control.request(self.app.STATE_DIR, "switch", {
                        "changes": {"project_id": saved["project_id"], "task_id": saved["id"],
                                    "description": ""},
                        "expected": result["state"], "context": result["project_context"],
                    })
                GLib.idle_add(self._finish, result, None)
            except Exception as error:
                GLib.idle_add(self._finish, None, str(error))
        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, result, error):
        self.busy = False
        if self.window is None:
            return False
        self.name.set_sensitive(True)
        self.save.set_sensitive(True)
        if hasattr(self, "create_start"):
            self.create_start.set_sensitive(True)
        if error:
            self.message.set_text(error)
        else:
            if self.on_saved:
                self.on_saved(result)
            self.window.destroy()
        return False

    def _key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        return False

    def _destroy(self, *_):
        screen = self.window.get_screen() if self.window else Gdk.Screen.get_default()
        Gtk.StyleContext.remove_provider_for_screen(screen, self.css)
        self.window = None
        for key, editor in list(_editors.items()):
            if editor is self:
                del _editors[key]


class TaskManager:
    def __init__(self, app, parent=None):
        self.app = app
        self.snapshot = toggl_control.snapshot(app)
        self.window = Gtk.Window(title="Manage Tasks")
        self.window.set_default_size(600, 520)
        if parent:
            self.window.set_transient_for(parent)
        self.window.get_style_context().add_class("toggl-task-manager")
        self.css = Gtk.CssProvider()
        self.css.load_from_data(b".toggl-task-manager, .toggl-task-manager * { font-size: 14px; }")
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), self.css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + side)(16)
        self.window.add(box)
        filters = Gtk.Box(spacing=10)
        self.project = Gtk.ComboBoxText()
        projects = [p for p in self.snapshot["projects"] if p.get("active") is not False
                    and str(p.get("workspace_id")) == str(self.snapshot["state"].get("workspace_id"))]
        for item in projects:
            self.project.append(str(item["id"]), item.get("name") or str(item["id"]))
        selected = self.snapshot["state"].get("project_id")
        self.project.set_active_id(str(selected) if selected is not None else (str(projects[0]["id"]) if projects else None))
        self.project.connect("changed", lambda *_: self._fill())
        self.archived = Gtk.CheckButton(label="Show archived")
        self.archived.connect("toggled", lambda *_: self._fill())
        new = Gtk.Button(label="New Task")
        new.connect("clicked", self._new)
        filters.pack_start(self.project, True, True, 0)
        filters.pack_start(self.archived, False, False, 0)
        filters.pack_start(new, False, False, 0)
        box.pack_start(filters, False, False, 0)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.list)
        box.pack_start(scroll, True, True, 0)
        self.message = Gtk.Label(xalign=0)
        self.message.set_line_wrap(True)
        box.pack_start(self.message, False, False, 0)
        self.window.connect("destroy", self._destroy)
        self._fill()
        self.window.show_all()

    def _fill(self):
        for child in self.list.get_children():
            self.list.remove(child)
        selected = self.project.get_active_id()
        tasks = project_tasks(self.snapshot, selected, self.archived.get_active()) if selected else []
        for task in sorted(tasks, key=lambda item: (item.get("active") is False, item.get("name", "").casefold())):
            row = Gtk.ListBoxRow()
            line = Gtk.Box(spacing=10)
            label = Gtk.Label(label=task.get("name") or "Unnamed task", xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            line.pack_start(label, True, True, 0)
            edit = Gtk.Button(label="Edit")
            edit.connect("clicked", lambda _button, item=deepcopy(task): self._edit(item))
            toggle = Gtk.Button(label="Restore" if task.get("active") is False else "Archive")
            toggle.connect("clicked", lambda _button, item=deepcopy(task): self._toggle(item))
            line.pack_start(edit, False, False, 0)
            line.pack_start(toggle, False, False, 0)
            row.add(line)
            self.list.add(row)
        self.list.show_all()
        self.message.set_text("" if tasks else "No tasks in this project.")

    def _new(self, *_):
        selected = self.project.get_active_id()
        if selected:
            show(self.app, selected, parent=self.window, on_saved=self._saved)

    def _edit(self, task):
        show(self.app, task.get("project_id"), task=task, parent=self.window, on_saved=self._saved)

    def _toggle(self, task):
        self.message.set_text("Saving task…")
        payload = {"context": self.snapshot["project_context"], "task": task,
                   "name": task["name"], "active": task.get("active") is False}
        def worker():
            try:
                result = toggl_control.request(self.app.STATE_DIR, "task-update", payload)
                GLib.idle_add(self._saved, result)
            except Exception as error:
                GLib.idle_add(self.message.set_text, str(error))
        threading.Thread(target=worker, daemon=True).start()

    def _saved(self, snapshot):
        if self.window is None:
            return False
        self.snapshot = snapshot
        self._fill()
        return False

    def _destroy(self, *_):
        global _manager
        screen = self.window.get_screen() if self.window else Gdk.Screen.get_default()
        Gtk.StyleContext.remove_provider_for_screen(screen, self.css)
        self.window = None
        if _manager is self:
            _manager = None


def show(app, project_id, task=None, parent=None, on_saved=None, start=False):
    snapshot = toggl_control.snapshot(app)
    key = (snapshot["project_context"]["account"], str(snapshot["project_context"]["workspace_id"]),
           str(task.get("id")) if task else f"new:{project_id}:{int(bool(start))}")
    if key not in _editors or _editors[key].window is None:
        _editors[key] = TaskEditor(app, snapshot, project_id, task, parent, on_saved, start)
    elif on_saved:
        _editors[key].on_saved = on_saved
    _editors[key].window.present()
    return False


def manage(app, parent=None):
    global _manager
    if _manager is None or _manager.window is None:
        _manager = TaskManager(app, parent)
    _manager.window.present()
    return False
