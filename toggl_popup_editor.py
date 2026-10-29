"""Forms embedded in the tray panel, with frozen targets for guarded writes."""
from copy import deepcopy

from gi.repository import Gtk


class InlineEditor:
    def __init__(self, snapshot, mode, submit, cancel, row=None):
        self.mode = mode
        self.snapshot = deepcopy(snapshot)
        self.row = deepcopy(row or {})
        self.submit = submit
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        current = snapshot['state']
        self.original = deepcopy(current if mode == 'current' else self.row)
        titles = {'current': 'Edit running timer', 'task': 'Edit saved task',
                  'new-task': 'New task', 'draft': 'Start with changes'}
        heading = Gtk.Label(label=titles[mode], xalign=0)
        heading.get_style_context().add_class('project')
        self.widget.pack_start(heading, False, False, 0)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        self.widget.pack_start(grid, False, False, 0)
        self.description = Gtk.Entry()
        self.description.set_hexpand(True)
        task_mode = mode in ('task', 'new-task')
        self.description.set_max_length(200 if task_mode else 10000)
        self.description.set_text(self.original.get('name' if mode == 'task' else 'description', ''))
        grid.attach(Gtk.Label(label='Name' if task_mode else 'Description', xalign=0), 0, 0, 1, 1)
        grid.attach(self.description, 1, 0, 1, 1)
        self.project = Gtk.ComboBoxText()
        self.project.set_hexpand(True)
        self.project.append('none', 'No project')
        projects = [p for p in snapshot['projects']
                    if str(p.get('workspace_id')) == str(current.get('workspace_id'))
                    and p.get('active') is not False]
        self.projects = projects
        for project in projects:
            self.project.append(str(project['id']), project['name'])
        project_id = self.original.get('project_id')
        if project_id is not None and not any(str(p['id']) == str(project_id) for p in projects):
            self.project.append(str(project_id), 'Current project')
        self.project.set_active_id(str(project_id) if project_id is not None else 'none')
        grid.attach(Gtk.Label(label='Project', xalign=0), 0, 1, 1, 1)
        grid.attach(self.project, 1, 1, 1, 1)
        # The service only supports name/active updates for existing task metadata.
        self.project.set_sensitive(mode != 'task')
        self.task = Gtk.ComboBoxText()
        self.task.set_hexpand(True)
        self.selected_task = self.original.get('task_id')
        self.project.connect('changed', self._project_changed)
        self._fill_tasks(self.selected_task)
        if not task_mode:
            grid.attach(Gtk.Label(label='Task', xalign=0), 0, 2, 1, 1)
            grid.attach(self.task, 1, 2, 1, 1)
        hint = {'current': 'Save keeps this time interval. Changing project clears its task.',
                'task': 'Renames this saved task. OpenTickly keeps it in its original project.',
                'new-task': 'Saves a reusable task in the selected project.',
                'draft': 'Starts a new interval. Earlier time entries stay unchanged.'}[mode]
        note = Gtk.Label(label=hint, xalign=0)
        note.set_line_wrap(True)
        note.set_max_width_chars(48)
        self.widget.pack_start(note, False, False, 0)
        self.actions = Gtk.Box(spacing=8)
        back = Gtk.Button(label='Cancel')
        back.connect('clicked', lambda *_: cancel())
        self.actions.pack_start(back, False, False, 0)
        label = {'current': 'Save changes', 'task': 'Save changes',
                 'new-task': 'Create task', 'draft': 'Start timer'}[mode]
        self.save = Gtk.Button(label=label)
        self.save.get_style_context().add_class('suggested-action')
        self.save.connect('clicked', lambda *_: self._save())
        self.actions.pack_end(self.save, False, False, 0)
        self.description.connect('activate', lambda *_: self._save())
        self.widget.pack_start(self.actions, False, False, 0)
        if mode == 'draft':
            extras = Gtk.Box(spacing=8)
            self.bookmark = Gtk.Button(label='Save as task')
            self.bookmark.connect('clicked', lambda *_: self._save(as_task=True))
            self.description.connect('changed', lambda *_: self._update_bookmark())
            self._update_bookmark()
            extras.pack_start(self.bookmark, False, False, 0)
            self.widget.pack_start(extras, False, False, 0)
        self.widget.show_all()

    def project_id(self):
        selected = self.project.get_active_id()
        if selected in (None, 'none'):
            return None
        return next((p['id'] for p in self.projects if str(p['id']) == selected),
                    self.original.get('project_id'))

    def _fill_tasks(self, selected=None):
        self.task.remove_all()
        self.task.append('none', 'No task')
        choices = [t for t in self.snapshot.get('tasks', [])
                   if str(t.get('workspace_id')) == str(self.snapshot['state'].get('workspace_id'))
                   and str(t.get('project_id')) == str(self.project_id())
                   and t.get('active') is not False]
        self.task_choices = choices
        for task in choices:
            self.task.append(str(task['id']), task['name'])
        # Retain an uncached/archived current assignment when editing its description.
        if selected is not None and not any(str(t['id']) == str(selected) for t in choices):
            self.task.append(str(selected), 'Current task')
        self.task.set_active_id(str(selected) if selected is not None else 'none')

    def _project_changed(self, *_):
        self._fill_tasks()
        self._update_bookmark()

    def _update_bookmark(self):
        if hasattr(self, 'bookmark'):
            enabled = (self.snapshot.get('task_capability') != 'unavailable'
                       and self.project_id() is not None
                       and 0 < len(self.description.get_text().strip()) <= 200)
            self.bookmark.set_sensitive(enabled)
            self.bookmark.set_tooltip_text('Save this description as a reusable task in the selected project'
                                           if enabled else 'Choose a project and enter a task name of 1–200 characters')

    def _save(self, as_task=False):
        text = self.description.get_text()
        project_id = self.project_id()
        context = self.snapshot['project_context']
        if self.mode in ('task', 'new-task') or as_task:
            action = 'task-update' if self.mode == 'task' else 'task-create'
            payload = dict(context=context, project_id=project_id, name=text)
            if self.mode == 'task':
                payload['task'] = self.original
            self.submit(action, payload, 'Task saved')
            return
        selected = self.task.get_active_id()
        task_id = None if selected in (None, 'none') else next(
            (t['id'] for t in self.task_choices if str(t['id']) == selected),
            self.original.get('task_id'))
        changes = dict(description=text, project_id=project_id, task_id=task_id)
        if self.mode == 'current':
            # Send only edits, retaining an archived task without trying to assign it anew.
            changes = {key: value for key, value in changes.items()
                       if value != self.original.get(key)}
        self.submit('edit-current' if self.mode == 'current' else 'switch',
                    dict(changes=changes, expected=self.snapshot['state'], context=context),
                    'Changes saved' if self.mode == 'current' else 'Timer started')
