"""Cinnamon tray click bindings without touching a desktop or timer."""
from unittest.mock import MagicMock, patch

from test_toggl_tray import reset_state
import toggl_tray as app


def test_native_tray_menu_opens_from_both_mouse_buttons_and_refreshes():
    tray, native = MagicMock(), MagicMock()
    first, refreshed = MagicMock(), MagicMock()
    tray._create_menu.side_effect = [first, refreshed]
    with patch.object(app, 'icon_ref', tray), patch.object(app, 'native_status_icon_ref', native):
        assert app._update_native_status_menu() is False
        assert app._update_native_status_menu() is False
    assert [c.args[0] for c in native.set_primary_menu.call_args_list] == [first, refreshed]
    assert [c.args[0] for c in native.set_secondary_menu.call_args_list] == [first, refreshed]
