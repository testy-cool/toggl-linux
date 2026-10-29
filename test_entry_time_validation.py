"""Invalid time text cannot implicitly confirm an older interval's stored times."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
import toggl_tray as app
import toggl_entries_window as entries


@pytest.mark.parametrize('field_index', [1, 2])
def test_invalid_changed_time_keeps_full_review_open_and_does_not_save(field_index):
    now = datetime.now(timezone.utc)
    entry = dict(start=(now-timedelta(hours=2)).isoformat(), stop=(now-timedelta(hours=1)).isoformat(),
                 duration=3600, description='Older correction', _needs_full_review=True)
    fields = []
    class Field:
        def __init__(self): self.value=''; fields.append(self)
        def set_text(self, value): self.value=value
        def get_text(self): return self.value
        def __getattr__(self, _name): return lambda *_args: None
    class Combo:
        def __init__(self): self.selected=None
        def append(self, *_args): pass
        def set_active_id(self, value): self.selected=value
        def get_active_id(self): return self.selected
        def __getattr__(self, _name): return lambda *_args: None
    dialog = MagicMock()
    attempts = []
    def response():
        attempts.append(True)
        fields[field_index].set_text('25:00')
        return 1 if len(attempts) == 1 else 2
    dialog.run.side_effect = response
    with patch.object(entries.Gtk.ResponseType, 'OK', 1), patch.object(entries.Gtk.ResponseType, 'CANCEL', 2), \
         patch.object(entries.Gtk.ResponseType, 'REJECT', 3), patch.object(entries.Gtk, 'Dialog', return_value=dialog), \
         patch.object(entries.Gtk, 'Entry', Field), patch.object(entries.Gtk, 'ComboBoxText', Combo), \
         patch.object(app, '_cached_projects', return_value=[]), patch.object(app, '_cached_tasks', return_value=[]), \
         patch.object(entries.Gtk, 'events_pending', return_value=False), \
         patch.object(app.GLib, 'idle_add', side_effect=lambda callback: callback()):
        assert entries._gtk_edit_entry_dialog(entry) is None
    assert dialog.run.call_count == 2


def test_null_description_is_not_mistaken_for_an_explicit_blank_edit():
    now = datetime.now(timezone.utc)
    start, stop = now-timedelta(hours=2), now-timedelta(hours=1)
    entry = dict(start=start.isoformat(), stop=stop.isoformat(), duration=3600,
                 description=None, project_id=None, task_id=None)
    fields = [MagicMock(), MagicMock(), MagicMock()]
    for field, value in zip(fields, ['', start.astimezone().strftime('%H:%M'), stop.astimezone().strftime('%H:%M')]):
        field.get_text.return_value = value
    dialog, combo = MagicMock(), MagicMock()
    dialog.run.return_value = 1
    combo.get_active_id.return_value = 'none'
    with patch.object(entries.Gtk.ResponseType, 'OK', 1), patch.object(entries.Gtk.ResponseType, 'REJECT', 2), \
         patch.object(entries.Gtk, 'Dialog', return_value=dialog), patch.object(entries.Gtk, 'Entry', side_effect=fields), \
         patch.object(entries.Gtk, 'ComboBoxText', return_value=combo), \
         patch.object(entries.Gtk, 'events_pending', return_value=False), \
         patch.object(app, '_cached_projects', return_value=[]), patch.object(app, '_cached_tasks', return_value=[]), \
         patch.object(app.GLib, 'idle_add', side_effect=lambda callback: callback()):
        assert entries._gtk_edit_entry_dialog(entry) is None
