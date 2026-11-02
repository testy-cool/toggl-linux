"""Project dots and actual tray pixels use one theme palette without account writes."""
from copy import deepcopy
from unittest.mock import patch

from PIL import Image, ImageColor
import pytest

import toggl_tray as app
import toggl_control as control
import toggl_project_colors as colors


@pytest.mark.parametrize("theme,expected", [("grok-night", "#7aa2f7"), ("gruvbox", "#83a598")])
@pytest.mark.parametrize("project_id", [7, 8])
def test_dot_payload_matches_actual_tray_pixels_after_theme_change(theme, expected, project_id):
    projects = [dict(id=8, workspace_id="ws_123", name="Second", color="#ff0000"),
                dict(id=7, workspace_id="ws_123", name="First", color="#00ff00"),
                dict(id=1, workspace_id="other", name="Other", color="#123456")]
    original = deepcopy(projects)
    app.state.update(tracking=True, project_id=project_id, description="Current")
    before = dict(app.state)
    with patch.object(app, "_cached_projects", return_value=projects), \
         patch.object(app, "_cached_tasks", return_value=[]), \
         patch.object(app, "_icon_path_active", None), patch.object(app, "_icon_path_inactive", None), \
         patch.object(app.GLib, "idle_add") as idle, patch.object(app, "_api") as network:
        result = control.dispatch(app, "set-ui-theme", {"theme": theme})
        assert result["project_colors"] == {"7": expected, "8": colors.PALETTES[theme][1]}
        assert control.snapshot(app)["project_colors"] == result["project_colors"]
        display = result["project_colors"][str(project_id)]
        assert app._tracking_color() == display
        with Image.open(app._current_icon_path()) as icon:
            assert icon.getpixel((app.ICON_SIZE//2, app.ICON_PADDING+2)) == (*ImageColor.getrgb(display), 255)
            assert icon.getpixel((0, 0))[3] == 0
        idle.assert_called_once_with(app._update_tray_icon_once)
        network.assert_not_called()
    assert projects == original and app.state == before


def test_project_identity_colors_ignore_order_names_and_other_workspaces():
    projects = [dict(id=10, workspace_id=1, name="A"), dict(id=2, workspace_id=1, name="Z"),
                dict(id=1, workspace_id=2, name="Other")]
    expected = {"2": "#7aa2f7", "10": "#9ece6a"}
    assert colors.project_colors(projects, 1, "grok-night") == expected
    assert colors.project_colors([dict(p, name="Renamed") for p in reversed(projects)], 1, "grok-night") == expected
    assert colors.project_colors(projects, "1", "grok-night") == expected


@pytest.mark.parametrize("theme", ["grok-night", "gruvbox"])
def test_missing_project_metadata_and_unassigned_tasks_are_theme_stable(theme):
    current = dict(workspace_id=1, project_id="missing", description="Work")
    result = colors.appearance([], [], current, theme)
    assert result["project_colors"]["missing"] == colors.tracking_color([], current, theme)
    current["project_id"] = None
    color = colors.tracking_color([], current, theme)
    assert color in colors.PALETTES[theme]
    assert colors.tracking_color([], dict(current, description=" Work  "), theme) == color
    assert colors.tracking_color([], dict(current, description=""), theme) is None
