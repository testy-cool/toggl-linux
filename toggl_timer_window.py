"""Native GTK timer editor, with common controls visible in one place."""
import threading
from copy import deepcopy
from gi.repository import Gtk, GLib, Gdk, Pango
import toggl_control

_window = None


def editor_changes(app, baseline, description, project, start, task=None):
    changes = {}
    if description != baseline.get("description", ""):
        changes["description"] = description
    project = None if project == "none" else project
    if str(project) != str(baseline.get("project_id")):
        changes["project_id"] = project
    if task is not None:
        task = None if task == "none" else task
        if str(task) != str(baseline.get("task_id")):
            changes["task_id"] = task
    if baseline.get("tracking"):
        previous = app._coerce_datetime(baseline["start_time"]).astimezone()
        unchanged = start.strip() == previous.strftime("%Y-%m-%d %H:%M") or (
            previous.date() == app.datetime.now().astimezone().date() and start.strip() == previous.strftime("%H:%M"))
        if not unchanged:
            try:
                text = start.strip()
                if " " not in text:
                    text = app.datetime.now().astimezone().strftime("%Y-%m-%d ") + text
                parsed = app.datetime.strptime(text, "%Y-%m-%d %H:%M").astimezone()
            except ValueError:
                raise ValueError("Enter HH:MM for today, or YYYY-MM-DD HH:MM") from None
            changes["start"] = parsed.isoformat()
    return changes


def elapsed_text(app, state):
    if not state.get("tracking"):
        return "0:00:00"
    seconds = (app.datetime.now(app.timezone.utc) - app._coerce_datetime(state["start_time"])).total_seconds()
    return app._format_duration(seconds)


def previous_tasks(app, snapshot):
    completed = []
    for entry in snapshot["entries"]:
        if not entry.get("stop") or entry.get("duration", 0) < 0 or not entry.get("workspace_id"):
            continue
        try:
            stamp = app._coerce_datetime(entry["start"]).timestamp()
        except (ValueError, TypeError, KeyError):
            continue
        completed.append((stamp, entry))
    result, seen = [], set()
    for _, entry in sorted(completed, key=lambda item: item[0], reverse=True):
        key = (entry.get("description", ""), str(entry.get("project_id")), str(entry.get("task_id")),
               str(entry["workspace_id"]))
        if key not in seen:
            result.append(deepcopy(entry))
            seen.add(key)
    return result[:20]


class TimerEditor:
    def __init__(self, app):
        self.app = app
        self.updating = False
        self.dirty = False
        self.busy = False
        self.baseline = {}
        self.window = Gtk.Window(title="Toggl Timer")
        self.window.set_default_size(560, 480)
        self.window.get_style_context().add_class("toggl-editor")
        css = Gtk.CssProvider()
        self.css = css
        css.load_from_data(b".toggl-editor, .toggl-editor * { font-size: 14px; } .toggl-editor .timer-elapsed { font-size: 28px; }")
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + side)(16)
        self.window.add(box)
        self.status = Gtk.Label(xalign=0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.elapsed = Gtk.Label(xalign=0)
        self.elapsed.get_style_context().add_class("timer-elapsed")
        box.pack_start(self.status, False, False, 0)
        box.pack_start(self.elapsed, False, False, 0)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        grid.set_hexpand(True)
        box.pack_start(grid, False, False, 0)
        self.project = Gtk.ComboBoxText()
        self.task = Gtk.ComboBoxText()
        self.description = Gtk.Entry()
        self.start = Gtk.Entry()
        self.start.set_placeholder_text("YYYY-MM-DD HH:MM")
        self.start.set_tooltip_text("Enter a date and time, or HH:MM for today")
        for row, (title, widget) in enumerate((("Project", self.project), ("Task", self.task),
                                               ("Description", self.description), ("Start time", self.start))):
            label = Gtk.Label(label=title, xalign=0)
            grid.attach(label, 0, row, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1 if row in (0, 1) else 2, 1)
            widget.connect("changed", self._changed)
        self.project.connect("changed", self._project_changed)
        self.project_tools = Gtk.MenuButton(label="Projects")
        project_menu = Gtk.Menu()
        project_menu.get_style_context().add_class("toggl-editor")
        for title, callback in (("New Project…", self._new_project),
                                ("Edit Selected Project…", self._edit_project),
                                ("Manage Projects…", self._manage_projects)):
            item = Gtk.MenuItem(label=title)
            item.connect("activate", callback)
            project_menu.append(item)
            if title.startswith("Edit"):
                self.edit_project_item = item
        project_menu.connect("show", self._project_menu_opened)
        project_menu.show_all()
        self.project_tools.set_popup(project_menu)
        grid.attach(self.project_tools, 2, 0, 1, 1)
        self.task_tools = Gtk.MenuButton(label="Tasks")
        task_menu = Gtk.Menu()
        task_menu.get_style_context().add_class("toggl-editor")
        for title, callback in (("New Task…", self._new_saved_task),
                                ("Edit Selected Task…", self._edit_saved_task),
                                ("Manage Tasks…", self._manage_tasks)):
            item = Gtk.MenuItem(label=title)
            item.connect("activate", callback)
            task_menu.append(item)
            if title.startswith("Edit"):
                self.edit_task_item = item
        task_menu.connect("show", self._task_menu_opened)
        task_menu.show_all()
        self.task_tools.set_popup(task_menu)
        grid.attach(self.task_tools, 2, 1, 1, 1)
        self.description.connect("activate", lambda *_: self._save())
        self.start.connect("activate", lambda *_: self._save())
        actions = Gtk.Box(spacing=10)
        box.pack_start(actions, False, False, 0)
        self.toggle = Gtk.Button(label="Start Timer")
        self.new_task = Gtk.Button(label="Start New Entry")
        self.save = Gtk.Button(label="Save Changes")
        self.save.set_tooltip_text("Correct this interval · Ctrl+Enter")
        self.new_task.set_tooltip_text("End this interval and start another · Ctrl+Shift+Enter")
        self.toggle.connect("clicked", self._toggle)
        self.new_task.connect("clicked", lambda *_: self._new_task())
        self.save.connect("clicked", lambda *_: self._save())
        for button in (self.toggle, self.new_task, self.save):
            actions.pack_start(button, True, True, 0)
        self.message = Gtk.Label(xalign=0)
        self.message.set_line_wrap(True)
        self.message.set_max_width_chars(55)
        box.pack_start(self.message, False, False, 0)
        self.reload = Gtk.Button(label="Reload Current Timer")
        self.reload.connect("clicked", lambda *_: self._load(toggl_control.snapshot(app)))
        box.pack_start(self.reload, False, False, 0)
        secondary = Gtk.Box(spacing=10)
        today = Gtk.Button(label="Today's Entries")
        today.connect("clicked", lambda *_: app.on_view_today(None, None))
        secondary.pack_start(today, True, True, 0)
        self.resume = Gtk.Button(label="Resume Recent Activity")
        self.resume.connect("clicked", lambda *_: self._resume_previous())
        secondary.pack_start(self.resume, True, True, 0)
        self.more = Gtk.MenuButton(label="More")
        menu = Gtk.Menu()
        menu.get_style_context().add_class("toggl-editor")
        self.panel_timer = Gtk.CheckMenuItem(label="Show elapsed time in panel")
        self.panel_description = Gtk.CheckMenuItem(label="Show description in panel")
        self.panel_timer.connect("toggled", lambda b: self._panel("timer", b.get_active()))
        self.panel_description.connect("toggled", lambda b: self._panel("description", b.get_active()))
        for item in (self.panel_timer, self.panel_description):
            menu.append(item)
        for title, callback in (("Sync Now", lambda *_: self._act("sync", {})),
                                ("Refresh Projects", lambda *_: self._act("refresh", {"force": True})),
                                ("Connection…", lambda *_: app.on_set_token(None, None))):
            item = Gtk.MenuItem(label=title)
            item.connect("activate", callback)
            menu.append(item)
        menu.connect("show", self._settings_opened)
        menu.show_all()
        self.more.set_popup(menu)
        secondary.pack_start(self.more, True, True, 0)
        box.pack_end(secondary, False, False, 0)
        self.window.connect("destroy", self._destroy)
        self.window.connect("key-press-event", self._key)
        self._load(toggl_control.snapshot(app))
        self.window.show_all()
        self.reload.hide()
        self.tick_source = GLib.timeout_add_seconds(1, self._tick)

    def _changed(self, *_):
        if not self.updating:
            self.dirty = True

    def _load(self, snapshot):
        self.updating = True
        self.baseline = dict(snapshot["state"])
        self.projects_snapshot = list(snapshot["projects"])
        self.tasks_snapshot = list(snapshot.get("tasks", []))
        self.description.set_text(self.baseline.get("description", ""))
        self._load_project_choices(snapshot, self.baseline.get("project_id"))
        self._load_task_choices(snapshot, self.baseline.get("task_id"))
        self.start.set_text(self.app._coerce_datetime(self.baseline["start_time"]).astimezone().strftime("%Y-%m-%d %H:%M") if self.baseline.get("tracking") else "")
        self.start.set_sensitive(bool(self.baseline.get("tracking")))
        self.updating = False
        self.dirty = False
        self.message.set_text("")
        self.reload.hide()
        self._headers(snapshot)

    def _load_project_choices(self, snapshot, selected):
        self.project.remove_all()
        self.project.append("none", "No project")
        projects = [p for p in snapshot["projects"] if str(p.get("workspace_id")) == str(self.baseline.get("workspace_id"))
                    and (p.get("active") is not False or str(p.get("id")) == str(selected))]
        for p in projects:
            self.project.append(str(p["id"]), p.get("name") or str(p["id"]))
        if selected is not None and not any(str(p["id"]) == str(selected) for p in projects):
            self.project.append(str(selected), "Current project (not cached)")
        self.project.set_active_id("none" if selected is None else str(selected))

    def _load_task_choices(self, snapshot, selected):
        self.task.remove_all()
        self.task.append("none", "No task")
        project_id = self.project.get_active_id()
        tasks = [item for item in snapshot.get("tasks", [])
                 if str(item.get("project_id")) == str(project_id)
                 and str(item.get("workspace_id")) == str(self.baseline.get("workspace_id"))
                 and (item.get("active") is not False or str(item.get("id")) == str(selected))]
        for item in tasks:
            self.task.append(str(item["id"]), item.get("name") or str(item["id"]))
        if selected is not None and not any(str(item["id"]) == str(selected) for item in tasks):
            self.task.append(str(selected), "Current task (not cached)")
        self.task.set_active_id("none" if selected is None else str(selected))
        self.task.set_sensitive(project_id not in (None, "none"))

    def _project_changed(self, *_):
        if self.updating:
            return
        self._load_task_choices({"tasks": self.tasks_snapshot}, None)
        self.dirty = True

    def _project_menu_opened(self, *_):
        selected = self.project.get_active_id()
        snapshot = toggl_control.snapshot(self.app)
        self.edit_project_item.set_sensitive(selected not in (None, "none") and any(
            str(p.get("id")) == str(selected)
            and str(p.get("workspace_id")) == str(snapshot["state"].get("workspace_id")) for p in snapshot["projects"]))

    def _new_project(self, *_):
        import toggl_project_window
        toggl_project_window.show(self.app, parent=self.window, on_saved=self._project_saved)

    def _edit_project(self, *_):
        import toggl_project_window
        selected = self.project.get_active_id()
        if selected in (None, "none"):
            self.message.set_text("Select a project to edit its name or color.")
            return
        toggl_project_window.edit_selected(self.app, self.window, self._project_saved, selected)

    def _manage_projects(self, *_):
        import toggl_project_window
        toggl_project_window.manage(self.app, self.window)

    def _project_saved(self, snapshot):
        if self.window is None:
            return
        selected = self.project.get_active_id()
        self.updating = True
        try:
            self._load_project_choices(snapshot, None if selected == "none" else selected)
            self.projects_snapshot = list(snapshot["projects"])
        finally:
            self.updating = False
        self._headers(snapshot)
        self.message.set_text("Project saved. Choose it from Project when you want to use it.")

    def _task_menu_opened(self, *_):
        selected = self.task.get_active_id()
        self.edit_task_item.set_sensitive(selected not in (None, "none") and any(
            str(item.get("id")) == str(selected) for item in self.tasks_snapshot))

    def _new_saved_task(self, *_):
        import toggl_task_window
        project_id = self.project.get_active_id()
        if project_id in (None, "none"):
            self.message.set_text("Choose a project before creating a task.")
            return
        toggl_task_window.show(self.app, project_id, parent=self.window, on_saved=self._task_saved)

    def _edit_saved_task(self, *_):
        import toggl_task_window
        selected = self.task.get_active_id()
        task = next((item for item in self.tasks_snapshot if str(item.get("id")) == str(selected)), None)
        if not task:
            self.message.set_text("Select a saved task before editing it.")
            return
        toggl_task_window.show(self.app, task["project_id"], task=task, parent=self.window,
                               on_saved=self._task_saved)

    def _manage_tasks(self, *_):
        import toggl_task_window
        toggl_task_window.manage(self.app, self.window)

    def _task_saved(self, snapshot):
        if self.window is None:
            return
        selected = self.task.get_active_id()
        self.tasks_snapshot = list(snapshot.get("tasks", []))
        self.updating = True
        try:
            self._load_task_choices(snapshot, selected)
        finally:
            self.updating = False
        self.message.set_text("Task saved.")

    def _headers(self, snapshot):
        current = snapshot["state"]
        self.rendered_state = dict(current)
        name = next((p.get("name") for p in snapshot["projects"] if str(p.get("id")) == str(current.get("project_id"))
                     and str(p.get("workspace_id")) == str(current.get("workspace_id"))), "No project")
        task_name = next((item.get("name") for item in snapshot.get("tasks", [])
                          if str(item.get("id")) == str(current.get("task_id"))), None)
        detail = current.get("description") or task_name
        self.status.set_text(("Tracking · " if current.get("tracking") else "Stopped · ") + str(name)
                             + (f" · {detail}" if detail else "")
                             + (f" · {snapshot['pending_count']} pending" if snapshot["pending_count"] else ""))
        self.elapsed.set_text(elapsed_text(self.app, current))
        self.toggle.set_label("Stop Timer" if current.get("tracking") else "Start Timer")
        self.new_task.set_sensitive(bool(current.get("tracking")) and not self.busy)
        self.task_tools.set_sensitive(snapshot.get("task_capability") != "unavailable")

    def _tick(self):
        snapshot = toggl_control.snapshot(self.app)
        self._headers(snapshot)
        if not self.busy:
            if not self.dirty and (snapshot["state"] != self.baseline or snapshot["projects"] != self.projects_snapshot
                                   or snapshot.get("tasks", []) != self.tasks_snapshot):
                self._load(snapshot)
            elif snapshot["projects"] != self.projects_snapshot:
                self._project_saved(snapshot)
            elif snapshot.get("tasks", []) != self.tasks_snapshot:
                selected = self.task.get_active_id()
                self.tasks_snapshot = list(snapshot.get("tasks", []))
                self.updating = True
                try:
                    self._load_task_choices(snapshot, None if selected == "none" else selected)
                finally:
                    self.updating = False
            if self.dirty and snapshot["state"] != self.baseline:
                self.message.set_text("Timer changed. Reload the current timer before saving.")
                self.reload.show()
        return True

    def _changes(self):
        return editor_changes(self.app, self.baseline, self.description.get_text(), self.project.get_active_id(),
                              self.start.get_text(), self.task.get_active_id())

    def _save(self):
        try:
            changes = self._changes()
            if changes:
                self._act("edit-current", {"changes": changes, "expected": self.baseline})
        except ValueError as error:
            self.message.set_text(str(error))

    def _toggle(self, *_):
        if self.rendered_state.get("tracking"):
            self._act("stop", {"expected": self.rendered_state})
        else:
            self._act("start", {"expected": self.baseline, "changes": {"description": self.description.get_text(),
                      "project_id": None if self.project.get_active_id() == "none" else self.project.get_active_id(),
                      "task_id": None if self.task.get_active_id() == "none" else self.task.get_active_id()}})

    def _new_task(self):
        self._act("switch", {"expected": self.baseline, "changes": {"description": self.description.get_text(),
                  "project_id": None if self.project.get_active_id() == "none" else self.project.get_active_id(),
                  "task_id": None if self.task.get_active_id() == "none" else self.task.get_active_id()}})

    def _resume_previous(self):
        if self.busy:
            return
        snapshot = toggl_control.snapshot(self.app)
        if not previous_tasks(self.app, snapshot):
            self.pending_resume_picker = True
            self._act("refresh", {})
        else:
            self._show_resume_picker(snapshot)

    def _show_resume_picker(self, snapshot):
        entries = previous_tasks(self.app, snapshot)
        if not entries:
            self.message.set_text(snapshot.get("warning") or "No completed activities from today to resume yet.")
            return
        dialog = Gtk.Dialog(title="Resume Recent Activity", transient_for=self.window, modal=True)
        dialog.get_style_context().add_class("toggl-editor")
        dialog.set_default_size(560, -1)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Start Again", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_border_width(16)
        content.set_spacing(12)
        label = Gtk.Label(label="Choose a recent activity. Starting again creates a new time entry.", xalign=0)
        label.set_line_wrap(True)
        content.pack_start(label, False, False, 0)
        picker = Gtk.ComboBoxText()
        for index, entry in enumerate(entries):
            project = next((p.get("name") for p in snapshot["projects"]
                            if str(p["id"]) == str(entry.get("project_id"))
                            and str(p.get("workspace_id")) == str(entry["workspace_id"])),
                           "No project" if entry.get("project_id") is None else "Saved project")
            picker.append(str(index), f"{entry.get('description') or 'No description'} · {project}")
        for cell in picker.get_cells():
            cell.set_property("ellipsize", Pango.EllipsizeMode.END)
            cell.set_property("width-chars", 36)
        picker.set_active(0)
        content.pack_start(picker, False, False, 0)
        if self.dirty:
            note = Gtk.Label(label="Starting again replaces the unsaved draft in this window.", xalign=0)
            note.set_line_wrap(True)
            content.pack_start(note, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        selected = picker.get_active()
        dialog.destroy()
        if response == Gtk.ResponseType.OK and selected >= 0:
            self._act("resume", {"entry": entries[selected], "expected": snapshot["state"]})

    def _panel(self, kind, active):
        if self.updating:
            return
        (self.app.set_panel_timer_enabled if kind == "timer" else self.app.set_panel_description_enabled)(active)
        self.app._update_panel_timer_label()
        if self.app.icon_ref:
            self.app._set_tray_menu(self.app.build_menu())

    def _settings_opened(self, *_):
        self.updating = True
        try:
            self.panel_timer.set_active(self.app.get_panel_timer_enabled())
            self.panel_description.set_active(self.app.get_panel_description_enabled())
        finally:
            self.updating = False

    def _act(self, action, payload):
        if self.busy:
            return
        self.busy = True
        self.pending_action = action
        for button in (self.toggle, self.new_task, self.save, self.resume, self.description, self.project,
                       self.task, self.start):
            button.set_sensitive(False)
        self.message.set_text("Saving…" if action != "refresh" else "Refreshing…")
        def worker():
            try:
                result = toggl_control.request(self.app.STATE_DIR, action, payload)
                GLib.idle_add(self._finish, result, None)
            except Exception as error:
                GLib.idle_add(self._finish, None, str(error))
        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, result, error):
        self.busy = False
        if self.window is None:
            return False
        self.toggle.set_sensitive(True)
        self.save.set_sensitive(True)
        self.resume.set_sensitive(True)
        self.description.set_sensitive(True)
        self.project.set_sensitive(True)
        self.task.set_sensitive(self.project.get_active_id() not in (None, "none"))
        self.start.set_sensitive(bool(self.baseline.get("tracking")))
        if error:
            self.message.set_text(error)
            self.reload.show()
        elif self.dirty and getattr(self, "pending_action", None) in {"stop", "sync", "refresh"}:
            if self.pending_action == "stop":
                self.baseline = dict(result["state"])
            self._headers(result)
            self.start.set_sensitive(bool(result["state"].get("tracking")))
            self.message.set_text("Draft kept. Use Save Changes or Start New Entry when ready." if result["state"].get("tracking")
                                  else "Draft kept. Use Start Timer when ready.")
            if result["state"] != self.baseline:
                self.message.set_text("Draft kept. Timer changed; reload before saving.")
                self.reload.show()
        else:
            self._load(result)
        if getattr(self, "pending_resume_picker", False):
            self.pending_resume_picker = False
            if not error:
                self._show_resume_picker(result)
        return False

    def _key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        if event.keyval == Gdk.KEY_Return and event.state & Gdk.ModifierType.CONTROL_MASK:
            if event.state & Gdk.ModifierType.SHIFT_MASK:
                if self.rendered_state.get("tracking"):
                    self._new_task()
            else:
                self._save()
            return True
        return False

    def _destroy(self, *_):
        global _window
        if getattr(self, "tick_source", None):
            GLib.source_remove(self.tick_source)
        Gtk.StyleContext.remove_provider_for_screen(self.window.get_screen(), self.css)
        self.window = None
        _window = None


def show(app):
    global _window
    if _window is None:
        _window = TimerEditor(app)
    else:
        _window.window.present()
    return False
