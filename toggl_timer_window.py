"""Native GTK timer editor, with common controls visible in one place."""
import threading
from gi.repository import Gtk, GLib, Gdk, Pango
import toggl_control

_window = None


def editor_changes(app, baseline, description, project, start):
    changes = {}
    if description != baseline.get("description", ""):
        changes["description"] = description
    project = None if project == "none" else project
    if str(project) != str(baseline.get("project_id")):
        changes["project_id"] = project
    if baseline.get("tracking"):
        previous = app._coerce_datetime(baseline["start_time"]).astimezone()
        if start != previous.strftime("%H:%M"):
            parsed = app._parse_hhmm(start, previous)
            if parsed is None:
                raise ValueError("Enter the start time as HH:MM")
            changes["start"] = parsed.isoformat()
    return changes


def elapsed_text(app, state):
    if not state.get("tracking"):
        return "0:00:00"
    seconds = (app.datetime.now(app.timezone.utc) - app._coerce_datetime(state["start_time"])).total_seconds()
    return app._format_duration(seconds)


class TimerEditor:
    def __init__(self, app):
        self.app = app
        self.updating = False
        self.dirty = False
        self.busy = False
        self.baseline = {}
        self.window = Gtk.Window(title="Toggl Timer")
        self.window.set_default_size(560, 550)
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
        self.description = Gtk.Entry()
        self.start = Gtk.Entry()
        self.start.set_placeholder_text("HH:MM")
        for row, (title, widget) in enumerate((("Project", self.project), ("Description", self.description), ("Start time", self.start))):
            label = Gtk.Label(label=title, xalign=0)
            grid.attach(label, 0, row, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)
            widget.connect("changed", self._changed)
        self.description.connect("activate", lambda *_: self._save())
        self.start.connect("activate", lambda *_: self._save())
        actions = Gtk.Box(spacing=10)
        box.pack_start(actions, False, False, 0)
        self.toggle = Gtk.Button(label="Start Timer")
        self.new_task = Gtk.Button(label="Start New Task")
        self.save = Gtk.Button(label="Save Changes")
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
        self.panel_timer = Gtk.CheckButton(label="Show elapsed time in panel")
        self.panel_timer.set_active(app.get_panel_timer_enabled())
        self.panel_timer.connect("toggled", lambda b: self._panel("timer", b.get_active()))
        self.panel_description = Gtk.CheckButton(label="Show description in panel")
        self.panel_description.set_active(app.get_panel_description_enabled())
        self.panel_description.connect("toggled", lambda b: self._panel("description", b.get_active()))
        box.pack_start(self.panel_timer, False, False, 0)
        box.pack_start(self.panel_description, False, False, 0)
        secondary = Gtk.Box(spacing=10)
        for title, callback in (("Today's Entries", lambda *_: app.on_view_today(None, None)),
                                ("Sync Now", lambda *_: self._act("sync", {})),
                                ("Connection…", lambda *_: app.on_set_token(None, None))):
            button = Gtk.Button(label=title)
            button.connect("clicked", callback)
            secondary.pack_start(button, True, True, 0)
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
        self.description.set_text(self.baseline.get("description", ""))
        self.project.remove_all()
        self.project.append("none", "No project")
        projects = [p for p in snapshot["projects"] if str(p.get("workspace_id")) == str(self.baseline.get("workspace_id"))
                    and (p.get("active") is not False or p.get("id") == self.baseline.get("project_id"))]
        for p in projects:
            self.project.append(str(p["id"]), p.get("name") or str(p["id"]))
        selected = self.baseline.get("project_id")
        if selected is not None and not any(str(p["id"]) == str(selected) for p in projects):
            self.project.append(str(selected), "Current project (not cached)")
        self.project.set_active_id("none" if selected is None else str(selected))
        self.start.set_text(self.app._coerce_datetime(self.baseline["start_time"]).astimezone().strftime("%H:%M") if self.baseline.get("tracking") else "")
        self.start.set_sensitive(bool(self.baseline.get("tracking")))
        self.updating = False
        self.dirty = False
        self.message.set_text("")
        self.reload.hide()
        self._headers(snapshot)

    def _headers(self, snapshot):
        current = snapshot["state"]
        self.rendered_state = dict(current)
        name = next((p.get("name") for p in snapshot["projects"] if str(p.get("id")) == str(current.get("project_id"))
                     and str(p.get("workspace_id")) == str(current.get("workspace_id"))), "No project")
        self.status.set_text(("Tracking · " if current.get("tracking") else "Stopped · ") + str(name) + (f" · {snapshot['pending_count']} pending" if snapshot["pending_count"] else ""))
        self.elapsed.set_text(elapsed_text(self.app, current))
        self.toggle.set_label("Stop Timer" if current.get("tracking") else "Start Timer")
        self.new_task.set_sensitive(bool(current.get("tracking")) and not self.busy)

    def _tick(self):
        snapshot = toggl_control.snapshot(self.app)
        self._headers(snapshot)
        if not self.busy:
            if not self.dirty and (snapshot["state"] != self.baseline or snapshot["projects"] != self.projects_snapshot):
                self._load(snapshot)
            elif snapshot["state"] != self.baseline:
                self.message.set_text("Timer changed. Reload the current timer before saving.")
                self.reload.show()
        return True

    def _changes(self):
        return editor_changes(self.app, self.baseline, self.description.get_text(), self.project.get_active_id(), self.start.get_text())

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
            self._act("start", {"expected": self.rendered_state, "changes": {"description": self.description.get_text(), "project_id": None if self.project.get_active_id() == "none" else self.project.get_active_id()}})

    def _new_task(self):
        self._act("switch", {"expected": self.baseline, "changes": {"description": self.description.get_text(), "project_id": None if self.project.get_active_id() == "none" else self.project.get_active_id()}})

    def _panel(self, kind, active):
        (self.app.set_panel_timer_enabled if kind == "timer" else self.app.set_panel_description_enabled)(active)
        self.app._update_panel_timer_label()

    def _act(self, action, payload):
        if self.busy:
            return
        self.busy = True
        for button in (self.toggle, self.new_task, self.save, self.description, self.project, self.start):
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
        self.description.set_sensitive(True)
        self.project.set_sensitive(True)
        self.start.set_sensitive(bool(self.baseline.get("tracking")))
        if error:
            self.message.set_text(error)
            self.reload.show()
        else:
            self._load(result)
        return False

    def _key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        if event.keyval == Gdk.KEY_Return and event.state & Gdk.ModifierType.CONTROL_MASK:
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
