"""Native project forms preserve drafts and send stable project identities."""
from unittest.mock import MagicMock, patch

import pytest
import toggl_tray as app
import toggl_project_window as projects


def test_project_form_trims_name_and_normalizes_hex_color():
    assert projects.project_values('  New project  ', '#abcdef') == {'name': 'New project', 'color': '#ABCDEF'}


@pytest.mark.parametrize('name,color', [('', '#4466dd'), ('X'*201, '#4466dd'), ('Work', 'blue'), ('Work', '#fff')])
def test_project_form_rejects_incomplete_fields(name, color):
    with pytest.raises(ValueError):
        projects.project_values(name, color)


def test_editor_copies_project_and_context_so_external_changes_are_stale():
    original = dict(id=7, workspace_id=99, name='Original', color='#4466dd')
    context = dict(workspace_id=99, account='account')
    with patch.object(projects, 'Gtk') as gtk, patch.object(projects, 'Gdk'):
        gtk.Entry.return_value.get_text.return_value = '#4466dd'
        view = projects.ProjectEditor(app, {'project_context': context, 'projects': [original]}, original)
    original['name'] = 'Changed elsewhere'
    context['account'] = 'Replacement account'
    assert view.original['name'] == 'Original'
    assert view.context['account'] == 'account'


def test_failed_project_save_keeps_typed_fields():
    view = projects.ProjectEditor.__new__(projects.ProjectEditor)
    view.window = MagicMock()
    view.busy = True
    view.name = MagicMock()
    view.color_text = MagicMock()
    view.color = MagicMock()
    view.save = MagicMock()
    view.message = MagicMock()
    view._finish(None, 'Connection unavailable')
    view.name.set_text.assert_not_called()
    view.color_text.set_text.assert_not_called()
    assert view.busy is False
    view.message.set_text.assert_called_once_with('Connection unavailable')


def test_manage_projects_uses_the_configured_web_page():
    with patch.object(projects.toggl_control, 'snapshot', return_value={'projects_url': 'https://example.test/projects/99/list'}), \
         patch.object(projects.Gtk, 'show_uri_on_window') as open_url:
        projects.manage(app)
    assert open_url.call_args.args[1] == 'https://example.test/projects/99/list'
