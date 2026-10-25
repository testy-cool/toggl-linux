"""Shared native project editor for the tray and timer window."""
from copy import deepcopy
import re
import threading

from gi.repository import Gtk, GLib, Gdk
import toggl_control

_windows = {}


def project_values(name, color):
    name, color = name.strip(), color.strip()
    if not name or len(name) > 200:
        raise ValueError("Enter a project name of 1–200 characters")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("Enter a color as #RRGGBB, or use the color picker")
    return {"name": name, "color": color.upper()}


class ProjectEditor:
    def __init__(self, app, snapshot, project=None, parent=None, on_saved=None):
        self.app = app
        self.context = deepcopy(snapshot["project_context"])
        self.original = deepcopy(project)
        self.on_saved = on_saved
        self.busy = False
        self.window = Gtk.Window(title="Edit Selected Project" if project else "New Project")
        self.window.set_default_size(560, -1)
        if parent:
            self.window.set_transient_for(parent)
        self.window.get_style_context().add_class("toggl-project-editor")
        self.css = Gtk.CssProvider()
        self.css.load_from_data(b".toggl-project-editor, .toggl-project-editor * { font-size: 14px; }")
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + side)(16)
        self.window.add(box)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        self.name = Gtk.Entry()
        self.name.set_text(project.get("name", "") if project else "")
        self.name.set_max_length(200)
        self.name.set_hexpand(True)
        self.name.connect("activate", lambda *_: self._save())
        self.color_text = Gtk.Entry()
        initial = (project.get("color") or "#3B82F6") if project else "#3B82F6"
        self.color_text.set_text(initial)
        self.color_text.set_hexpand(True)
        self.color_text.connect("changed", self._color_typed)
        self.color_text.connect("activate", lambda *_: self._save())
        self.color = Gtk.ColorButton()
        self.color.set_title("Project color")
        self.color.set_use_alpha(False)
        self.color.connect("color-set", self._color_picked)
        self._color_typed()
        color_row = Gtk.Box(spacing=10)
        color_row.pack_start(self.color_text, True, True, 0)
        color_row.pack_start(self.color, False, False, 0)
        for row, (label, widget) in enumerate((("Name", self.name), ("Color", color_row))):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
        box.pack_start(grid, False, False, 0)
        self.message = Gtk.Label(xalign=0)
        self.message.set_line_wrap(True)
        self.message.set_max_width_chars(48)
        box.pack_start(self.message, False, False, 0)
        if not project:
            note = Gtk.Label(label="Create the project, then choose it from Project to use it.", xalign=0)
            note.set_line_wrap(True)
            box.pack_start(note, False, False, 0)
        actions = Gtk.Box(spacing=10)
        cancel = Gtk.Button(label="Close")
        cancel.connect("clicked", lambda *_: self.window.destroy())
        self.save = Gtk.Button(label="Save Project" if project else "Create Project")
        self.save.connect("clicked", lambda *_: self._save())
        actions.pack_start(cancel, True, True, 0)
        actions.pack_start(self.save, True, True, 0)
        box.pack_start(actions, False, False, 0)
        self.window.connect("destroy", self._destroy)
        self.window.connect("key-press-event", self._key)
        self.window.show_all()
        self.name.grab_focus()

    def _color_typed(self, *_):
        text = self.color_text.get_text().strip()
        if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
            rgba = Gdk.RGBA()
            rgba.parse(text)
            self.color.set_rgba(rgba)

    def _color_picked(self, *_):
        rgba = self.color.get_rgba()
        self.color_text.set_text("#{:02X}{:02X}{:02X}".format(*(round(v * 255) for v in (rgba.red, rgba.green, rgba.blue))))

    def _save(self):
        if self.busy:
            return
        try:
            payload = dict(project_values(self.name.get_text(), self.color_text.get_text()), context=self.context)
        except ValueError as error:
            self.message.set_text(str(error))
            return
        action = "project-update" if self.original else "project-create"
        if self.original:
            payload["project"] = self.original
        self.busy = True
        for widget in (self.name, self.color_text, self.color, self.save):
            widget.set_sensitive(False)
        self.message.set_text("Saving project…")
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
        for widget in (self.name, self.color_text, self.color, self.save):
            widget.set_sensitive(True)
        if error:
            self.message.set_text(error)
        else:
            if self.on_saved:
                self.on_saved(result)
            self.window.destroy()
        return False

    def _key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        return False

    def _destroy(self, *_):
        Gtk.StyleContext.remove_provider_for_screen(self.window.get_screen(), self.css)
        self.window = None
        for key, value in list(_windows.items()):
            if value is self:
                del _windows[key]


def show(app, project=None, parent=None, on_saved=None):
    snapshot = toggl_control.snapshot(app)
    context = snapshot["project_context"]
    key = (context["account"], str(context["workspace_id"]), str(project.get("id")) if project else "new")
    if key not in _windows or _windows[key].window is None:
        _windows[key] = ProjectEditor(app, snapshot, project, parent, on_saved)
    elif on_saved:
        _windows[key].on_saved = on_saved
    _windows[key].window.present()
    return False


def edit_selected(app, parent=None, on_saved=None, project_id=None):
    snapshot = toggl_control.snapshot(app)
    project_id = snapshot["state"].get("project_id") if project_id is None else project_id
    project = next((p for p in snapshot["projects"] if str(p.get("id")) == str(project_id)
                    and str(p.get("workspace_id")) == str(snapshot["state"].get("workspace_id"))), None)
    if project:
        show(app, deepcopy(project), parent, on_saved)
    else:
        app._notify("Select a saved project before editing it")
    return False


def manage(app, parent=None):
    try:
        url = toggl_control.snapshot(app)["projects_url"]
        if not url:
            app._notify("Connect your account and refresh projects before opening management")
            return False
        Gtk.show_uri_on_window(parent, url, Gdk.CURRENT_TIME)
    except Exception as error:
        app._notify(f"Could not open Projects: {error}")
    return False
