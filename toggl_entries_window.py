"""The Today's entries window: browse a day's entries and edit or delete them."""
from datetime import datetime, timedelta
from gi.repository import Gtk, Pango
import toggl_task_tree
import toggl_tray as app


def _show_entries_window():
    """Show entries window with day navigation."""
    win = Gtk.Window(title="Toggl Entries")
    win.set_default_size(app.ENTRY_WINDOW_WIDTH, app.ENTRY_WINDOW_HEIGHT)
    win.set_keep_above(True)

    ctx = {"date": datetime.now().date(), "entries": []}

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    vbox.set_margin_start(14)
    vbox.set_margin_end(14)
    vbox.set_margin_top(14)
    vbox.set_margin_bottom(14)

    nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    prev_btn = Gtk.Button(label="←")
    next_btn = Gtk.Button(label="→")
    today_btn = Gtk.Button(label="Today")
    prev_btn.set_tooltip_text("Previous day")
    next_btn.set_tooltip_text("Next day")
    today_btn.set_tooltip_text("Jump to today")

    title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    title_box.set_hexpand(True)
    header_label = Gtk.Label()
    header_label.set_xalign(0)
    summary_label = Gtk.Label()
    summary_label.set_xalign(0)
    summary_label.set_sensitive(False)
    title_box.pack_start(header_label, False, False, 0)
    title_box.pack_start(summary_label, False, False, 0)

    nav_box.pack_start(prev_btn, False, False, 0)
    nav_box.pack_start(title_box, True, True, 4)
    nav_box.pack_start(today_btn, False, False, 0)
    nav_box.pack_start(next_btn, False, False, 0)
    vbox.pack_start(nav_box, False, False, 0)

    scroll = Gtk.ScrolledWindow()
    scroll.set_vexpand(True)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_shadow_type(Gtk.ShadowType.IN)
    listbox = Gtk.ListBox()
    listbox.set_selection_mode(Gtk.SelectionMode.NONE)
    scroll.add(listbox)
    vbox.pack_start(scroll, True, True, 0)

    empty_label = Gtk.Label(label="No entries for this day")
    empty_label.set_sensitive(False)
    empty_label.set_vexpand(True)
    vbox.pack_start(empty_label, True, True, 0)

    def load_date(date_obj):
        ctx["date"] = date_obj
        header_label.set_markup("<b>Loading...</b>")
        summary_label.set_text("")
        for child in listbox.get_children():
            listbox.remove(child)
        empty_label.hide()
        next_btn.set_sensitive(date_obj < datetime.now().date())

        def _fetch():
            try:
                entries = app.fetch_entries_for_date(date_obj)
            except Exception:
                entries = []
            entries = app._merge_entries_with_pending(entries, app._pending_as_entries(date_obj))
            sorted_entries = sorted(entries, key=lambda x: x.get("start", ""))
            ctx["entries"] = sorted_entries
            app.GLib.idle_add(_populate, sorted_entries, date_obj)

        app.threading.Thread(target=_fetch, daemon=True).start()

    def _populate(entries, date_obj):
        for child in listbox.get_children():
            listbox.remove(child)

        total_secs = sum(app._entry_duration_seconds(entry) for entry in entries)

        today = datetime.now().date()
        if date_obj == today:
            day_str = "Today"
        elif date_obj == today - timedelta(days=1):
            day_str = "Yesterday"
        else:
            day_str = date_obj.strftime("%a %b %d")

        entry_count = len(entries)
        count_text = "1 entry" if entry_count == 1 else f"{entry_count} entries"
        header_label.set_markup(f"<b>{app.GLib.markup_escape_text(day_str)}</b>")
        summary_label.set_text(f"{count_text} · {app._format_duration(total_secs)} total")

        if not entries:
            empty_label.show()
        else:
            empty_label.hide()
            for entry in entries:
                row = _build_entry_row(entry, win, ctx["entries"], listbox, ctx)
                listbox.add(row)

        listbox.show_all()

    prev_btn.connect("clicked", lambda b: load_date(ctx["date"] - timedelta(days=1)))
    next_btn.connect("clicked", lambda b: load_date(ctx["date"] + timedelta(days=1)))
    today_btn.connect("clicked", lambda b: load_date(datetime.now().date()))

    win.add(vbox)
    win.show_all()
    load_date(ctx["date"])


def _build_entry_row(entry, win, entries, listbox, ctx):
    """Build a single clickable row for an entry."""
    start_dt = datetime.fromisoformat(entry["start"]).astimezone()
    running = (entry.get("duration", 0) or 0) < 0
    dur = app._entry_duration_seconds(entry)
    task_name = next((item.get("name") for item in app._cached_tasks()
                      if str(item.get("id")) == str(entry.get("task_id"))), None)
    desc = entry.get("description") or task_name or "(no description)"

    stop_str = "now" if running else ""
    if not running and entry.get("stop"):
        stop_dt = datetime.fromisoformat(entry["stop"]).astimezone()
        stop_str = stop_dt.strftime("%H:%M")

    row = Gtk.ListBoxRow()
    row.set_activatable(True)
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    outer.set_margin_start(10)
    outer.set_margin_end(10)
    outer.set_margin_top(8)
    outer.set_margin_bottom(8)

    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    desc_label = Gtk.Label()
    desc_label.set_xalign(0)
    desc_label.set_hexpand(True)
    desc_label.set_ellipsize(Pango.EllipsizeMode.END)
    desc_label.set_max_width_chars(app.ENTRY_ROW_DESC_CHARS)
    escaped_desc = app.GLib.markup_escape_text(desc)
    if entry.get("description") or task_name:
        desc_label.set_markup(f"<b>{escaped_desc}</b>")
    else:
        desc_label.set_markup(f"<span foreground='#6f6f6f'>{escaped_desc}</span>")
    top.pack_start(desc_label, True, True, 0)

    offline = entry.get("_offline", False)
    status_parts = []
    if running:
        status_parts.append("Running")
    if offline:
        status_parts.append("Local")
    if entry.get('_review_error'):
        status_parts.append('Needs review')
    if status_parts:
        status_label = Gtk.Label(label=" · ".join(status_parts))
        status_label.set_sensitive(False)
        top.pack_start(status_label, False, False, 0)

    edit_btn = Gtk.Button(label="Edit")
    edit_btn.set_tooltip_text("Edit entry")
    edit_btn.connect("clicked", lambda b: _on_edit_entry(entry, win, entries, listbox, ctx))
    top.pack_end(edit_btn, False, False, 0)
    outer.pack_start(top, False, False, 0)

    meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    time_label = Gtk.Label()
    time_label.set_markup(
        f"<tt>{start_dt.strftime('%H:%M')} - {app.GLib.markup_escape_text(stop_str)}</tt>"
    )
    time_label.set_xalign(0)
    time_label.set_sensitive(False)
    meta.pack_start(time_label, True, True, 0)

    duration_label = Gtk.Label(label=app._format_duration(dur))
    duration_label.set_xalign(1)
    duration_label.set_sensitive(False)
    meta.pack_end(duration_label, False, False, 0)
    outer.pack_start(meta, False, False, 0)

    row.add(outer)
    return row


def _on_edit_entry(entry, parent_win, entries, listbox, ctx):
    """Open edit dialog for a time entry — description, start, stop times."""
    def _do():
        result = _gtk_edit_entry_dialog(entry)
        if result is None:
            return
        if entry.get("_offline"):
            if result == "delete":
                desc = entry.get("description", "(no description)")
                title = 'Discard Local Changes' if entry.get('_review_error') else 'Delete Entry'
                question = ("Discard these local changes? The cloud entry will be kept."
                            if entry.get('_review_error') else f"Delete '{desc}'?")
                if app._gtk_confirm(title, question):
                    if app._delete_pending_entry(entry):
                        toggl_task_tree.forget(app, entry)
                        app._play_sound(app.SOUND_STOP)
                        _refresh_entries(ctx["date"], entries, listbox, parent_win, ctx)
                    else:
                        app._play_sound(app.SOUND_ERROR)
                return

            if app._update_pending_entry(entry, result):
                toggl_task_tree.correct(app, entry, result)
                app._play_sound(app.SOUND_STOP)
                _refresh_entries(ctx["date"], entries, listbox, parent_win, ctx)
            else:
                app._play_sound(app.SOUND_ERROR)
            return

        if result == "delete":
            wid = entry.get("workspace_id", app.state["workspace_id"])
            desc = entry.get("description", "(no description)")
            if app._gtk_confirm("Delete Entry", f"Delete '{desc}'?"):
                try:
                    app.delete_entry(wid, entry["id"])
                    toggl_task_tree.forget(app, entry)
                    app._play_sound(app.SOUND_STOP)
                    _refresh_entries(ctx["date"], entries, listbox, parent_win, ctx)
                except Exception:
                    app._play_sound(app.SOUND_ERROR)
            return

        wid = entry.get("workspace_id", app.state["workspace_id"])
        try:
            app.update_entry(wid, entry["id"], result)
            toggl_task_tree.correct(app, entry, result)
            app._play_sound(app.SOUND_STOP)
            _refresh_entries(ctx["date"], entries, listbox, parent_win, ctx)
        except Exception:
            app._play_sound(app.SOUND_ERROR)

    app.threading.Thread(target=_do, daemon=True).start()


def _refresh_entries(date_obj, old_entries, listbox, win, ctx):
    """Re-fetch entries for the given date and rebuild the listbox."""
    try:
        new_entries = app.fetch_entries_for_date(date_obj)
    except Exception:
        new_entries = []
    new_entries = app._merge_entries_with_pending(new_entries, app._pending_as_entries(date_obj))
    sorted_entries = sorted(new_entries, key=lambda x: x.get("start", ""))
    old_entries.clear()
    old_entries.extend(sorted_entries)
    ctx["entries"] = sorted_entries
    app.GLib.idle_add(_rebuild_listbox, listbox, sorted_entries, win, old_entries, ctx)


def _rebuild_listbox(listbox, entries, win, entries_ref, ctx):
    for child in listbox.get_children():
        listbox.remove(child)
    for entry in entries:
        row = _build_entry_row(entry, win, entries_ref, listbox, ctx)
        listbox.add(row)
    listbox.show_all()


def _gtk_edit_entry_dialog(entry):
    """Dialog with description, start time, stop time fields. Returns update dict or 'delete' or None."""
    result = {"value": None}
    done = app.threading.Event()

    start_dt = datetime.fromisoformat(entry["start"]).astimezone()
    dur = entry.get("duration", 0)
    running = dur < 0
    stop_text = ""
    if not running and entry.get("stop"):
        stop_dt = datetime.fromisoformat(entry["stop"]).astimezone()
        stop_text = stop_dt.strftime("%H:%M")

    def run():
        dialog = Gtk.Dialog(title="Edit Entry", modal=True)
        dialog.set_keep_above(True)
        dialog.set_resizable(True)
        dialog.set_default_size(460, -1)
        dialog.add_buttons(
            "Discard Local Changes" if entry.get('_review_error') else "Delete", Gtk.ResponseType.REJECT,
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            "Save All Fields" if entry.get('_needs_full_review') else Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dialog.set_default_response(Gtk.ResponseType.OK)

        del_btn = dialog.get_widget_for_response(Gtk.ResponseType.REJECT)
        if del_btn:
            style_ctx = del_btn.get_style_context()
            style_ctx.add_class("destructive-action")

        box = dialog.get_content_area()
        box.set_spacing(12)
        box.set_margin_start(18)
        box.set_margin_end(18)
        box.set_margin_top(16)
        box.set_margin_bottom(12)
        if entry.get('_needs_full_review'):
            notice = Gtk.Label(label='This older entry needs full review. Saving applies all displayed times and metadata.')
            notice.set_line_wrap(True)
            notice.set_max_width_chars(50)
            box.add(notice)

        grid = Gtk.Grid(column_spacing=12, row_spacing=10)

        grid.attach(Gtk.Label(label="Description", xalign=0), 0, 0, 1, 1)
        desc_entry = Gtk.Entry()
        desc_entry.set_text(entry.get("description") or "")
        desc_entry.set_hexpand(True)
        desc_entry.set_activates_default(True)
        desc_entry.set_width_chars(34)
        grid.attach(desc_entry, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Project", xalign=0), 0, 1, 1, 1)
        project_picker = Gtk.ComboBoxText()
        project_picker.append("none", "No project")
        projects = [item for item in app._cached_projects()
                    if str(item.get("workspace_id")) == str(entry.get("workspace_id", app.state.get("workspace_id")))
                    and (item.get("active") is not False or str(item.get("id")) == str(entry.get("project_id")))]
        for item in projects:
            project_picker.append(str(item["id"]), item.get("name") or str(item["id"]))
        project_picker.set_active_id("none" if entry.get("project_id") is None else str(entry.get("project_id")))
        grid.attach(project_picker, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="Task", xalign=0), 0, 2, 1, 1)
        task_picker = Gtk.ComboBoxText()
        def load_tasks(selected=None):
            task_picker.remove_all()
            task_picker.append("none", "No task")
            project_id = project_picker.get_active_id()
            tasks = [item for item in app._cached_tasks()
                     if str(item.get("workspace_id")) == str(entry.get("workspace_id", app.state.get("workspace_id")))
                     and str(item.get("project_id")) == str(project_id)
                     and (item.get("active") is not False or str(item.get("id")) == str(selected))]
            for item in tasks:
                task_picker.append(str(item["id"]), item.get("name") or str(item["id"]))
            task_picker.set_active_id("none" if selected is None else str(selected))
            task_picker.set_sensitive(project_id not in (None, "none"))
        load_tasks(entry.get("task_id"))
        project_picker.connect("changed", lambda *_: load_tasks())
        grid.attach(task_picker, 1, 2, 1, 1)

        grid.attach(Gtk.Label(label="Start", xalign=0), 0, 3, 1, 1)
        start_entry = Gtk.Entry()
        start_entry.set_text(start_dt.strftime("%H:%M"))
        start_entry.set_max_length(5)
        start_entry.set_width_chars(7)
        start_entry.set_placeholder_text("HH:MM")
        grid.attach(start_entry, 1, 3, 1, 1)

        grid.attach(Gtk.Label(label="Stop", xalign=0), 0, 4, 1, 1)
        stop_entry = Gtk.Entry()
        if running:
            stop_entry.set_text("running")
            stop_entry.set_sensitive(False)
        else:
            stop_entry.set_text(stop_text)
        stop_entry.set_max_length(7)
        stop_entry.set_width_chars(7)
        stop_entry.set_placeholder_text("HH:MM")
        grid.attach(stop_entry, 1, 4, 1, 1)

        box.add(grid)
        time_error = Gtk.Label(label='Use a valid HH:MM time for Start and Stop.')
        time_error.set_line_wrap(True)
        box.add(time_error)
        dialog.show_all()
        time_error.hide()
        desc_entry.grab_focus()
        while True:
            resp = dialog.run()
            if resp != Gtk.ResponseType.OK:
                break
            start_text = start_entry.get_text().strip()
            changed_start = start_text != start_dt.strftime('%H:%M')
            stop_value = stop_entry.get_text().strip()
            changed_stop = not running and stop_value != stop_text
            stop_base = datetime.fromisoformat(entry['stop']).astimezone() if entry.get('stop') else start_dt
            if ((changed_start and app._parse_hhmm(start_text, start_dt) is None)
                    or (changed_stop and app._parse_hhmm(stop_value, stop_base) is None)):
                time_error.show()
                continue
            break

        if resp == Gtk.ResponseType.REJECT:
            result["value"] = "delete"
        elif resp == Gtk.ResponseType.OK:
            update = {}
            new_desc = desc_entry.get_text().strip()
            if new_desc != (entry.get("description") or ""):
                update["description"] = new_desc

            project_id = project_picker.get_active_id()
            project_id = None if project_id == "none" else project_id
            task_id = task_picker.get_active_id()
            task_id = None if task_id == "none" else task_id
            if str(project_id) != str(entry.get("project_id")):
                update["project_id"] = project_id
            if str(task_id) != str(entry.get("task_id")):
                update["task_id"] = task_id

            new_start_str = start_entry.get_text().strip()
            orig_start_str = start_dt.strftime("%H:%M")
            if new_start_str != orig_start_str:
                parsed = app._parse_hhmm(new_start_str, start_dt)
                if parsed:
                    update["start"] = parsed.isoformat()

            if not running:
                new_stop_str = stop_entry.get_text().strip()
                if new_stop_str != stop_text:
                    base = datetime.fromisoformat(entry["stop"]).astimezone() if entry.get("stop") else start_dt
                    parsed = app._parse_hhmm(new_stop_str, base)
                    if parsed:
                        update["stop"] = parsed.isoformat()

            if update or entry.get('_needs_full_review'):
                result["value"] = update

        dialog.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        done.set()

    app.GLib.idle_add(run)
    done.wait()
    return result["value"]
