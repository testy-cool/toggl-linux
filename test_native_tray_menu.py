"""Cinnamon tray click bindings without touching a desktop or timer."""
from unittest.mock import MagicMock, patch

import toggl_tray as app


def test_native_tray_menu_opens_from_right_click_and_refreshes():
    tray, native = MagicMock(), MagicMock()
    first, refreshed = MagicMock(), MagicMock()
    tray._create_menu.side_effect = [first, refreshed]
    with patch.object(app, 'icon_ref', tray), patch.object(app, 'native_status_icon_ref', native):
        assert app._update_native_status_menu() is False
        assert app._update_native_status_menu() is False
    # Left click is left free for the project/task popup.
    assert [c.args[0] for c in native.set_primary_menu.call_args_list] == [None, None]
    assert [c.args[0] for c in native.set_secondary_menu.call_args_list] == [first, refreshed]


def test_left_click_opens_the_project_task_popup_at_the_icon():
    import toggl_webview_launcher
    with patch.object(app.GLib, 'idle_add') as idle:
        app._on_native_status_icon_press(None, 2002, 40, 1, 0, 0)
        app._on_native_status_icon_press(None, 2002, 40, 3, 0, 0)
    assert idle.call_args_list == [((toggl_webview_launcher.show, app, 2002, 40, 'work', True),)]
